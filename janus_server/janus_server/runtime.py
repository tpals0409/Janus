"""오케스트레이터-워커 실행 엔진.

에이전트 = 오케스트레이터 1개. 오케스트레이터는 `create_worker` 스킬로 런타임에
워커를 만들고, 워커는 그 실행의 트레이스에만 존재한다(저장·재사용 없음).

LangGraph 없이 실행을 직접 제어하므로 스팬을 명시적으로 열고 닫는다 — 이벤트
귀속 추측(구 trace.py)이 필요 없다. 이벤트는 워커 스레드에서 `send` 콜백으로
바로 나간다.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Callable

from openai import OpenAI

from . import adaptive as adaptive_mod
from . import agent as agent_mod
from . import budget as budget_mod
from . import spec as spec_mod
from . import scheduler as scheduler_mod
from . import telemetry as telemetry_mod
from . import tools as T
from .workspace import WorkspaceContext

# UI의 짧은 이름 -> 로컬에 실제로 존재하는 스냅샷 경로.
#
# 절대 repo ID("orcarouter/Qwen3.8-...")를 보내면 안 된다. mlx_vlm.server는 로드되지
# 않은 모델 id를 받으면 HuggingFace에서 **리포 전체를**(모든 quant, ~80GB) 내려받기
# 시작하고, 그동안 요청은 응답 없이 매달린다. 로컬 경로만 넘긴다.
LOCAL_MODELS = {
    "qwen3.8-27b": "~/.cache/huggingface/hub/"
                   "models--orcarouter--Qwen3.8-27B-Uncensored-MLX/snapshots/*/4-bit",
}

MLX_BASE_URL = "http://localhost:8080/v1"
WORKER_SYSTEM_MAX_CHARS = 8_000
WORKER_TASK_MAX_CHARS = 6_000
WORKER_CONTEXT_MAX_CHARS = 4_000
WORKER_ROLES = {
    "scout", "researcher", "planner", "prototyper",
    "implementer", "verifier", "operator",
}
READ_ONLY_WORKER_ROLES = {"scout", "researcher", "planner", "verifier"}
_RESOURCE_ROOT = Path(__file__).resolve().parent
_PERSONA_FILES = {
    "janus": "personas/janus.md",
    "scout": "personas/scout.md",
    "researcher": "personas/scout.md",  # backwards-compatible alias
    "planner": "personas/planner.md",
    "prototyper": "personas/prototyper.md",
    "implementer": "personas/implementer.md",
    "verifier": "personas/verifier.md",
    "operator": "personas/operator.md",
}
_ROLE_SKILLS = {
    "janus": ("task-contract",),
    "scout": ("codebase-recon",),
    "researcher": ("codebase-recon",),
    "planner": ("task-contract",),
    "prototyper": (),
    "implementer": ("minimal-patch", "verification-before-completion"),
    "verifier": ("verification-before-completion",),
    "operator": ("runtime-diagnostics",),
}
MAX_MODEL_QUEUE_FOR_SPAWN = 1
SINGLE_SLOT_PARENT_RESERVE_NUMERATOR = 6
SINGLE_SLOT_PARENT_RESERVE_DENOMINATOR = 10
TIGHT_DISPATCH_STEP_LIMIT = 16
# 도구 호출 몇 번 + 결과를 읽고 답하는 1 step. 2로 두면 두 번째 도구 호출에서 소진돼
# 답을 못 쓴 채 끝난다(실측: read_file×2 → glob → step_limit 소진).
MIN_WORKER_STEPS = 4
EXPLICIT_WORKER_PHRASES = (
    "create_worker", "spawn worker", "spawn a worker", "delegate to a worker",
    "worker를", "워커를", "위임",
)
EXPLICIT_WORKER_KOREAN_ACTIONS = ("배치", "생성", "추가", "실행", "스폰", "위임")
READ_ONLY_REQUEST_WORDS = (
    "investigate", "inspect", "research", "analyze", "audit", "explain", "explore",
    "조사", "살펴", "확인", "분석", "검토", "설명", "요약", "파악", "탐색", "훑",
)
MUTATING_REQUEST_WORDS = (
    "edit", "modify", "write", "implement", "fix", "refactor", "create", "delete",
    "수정", "변경", "작성", "구현", "고쳐", "리팩터", "생성", "삭제", "추가",
)


def _bundled_markdown(relative_path: str) -> str:
    path = (_RESOURCE_ROOT / relative_path).resolve()
    if not path.is_relative_to(_RESOURCE_ROOT):
        raise RuntimeError(f"invalid bundled prompt path: {relative_path}")
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError(f"missing bundled prompt: {relative_path}") from error
    if relative_path.endswith("SKILL.md") and content.startswith("---\n"):
        _frontmatter, separator, body = content[4:].partition("\n---\n")
        if not separator:
            raise RuntimeError(f"invalid bundled skill frontmatter: {relative_path}")
        content = body.strip()
    return content


def persona_prompt(role: str, *, custom_prompt: str = "") -> str:
    """Build a trusted, fixed persona/skill prompt for one runtime role."""
    normalized = str(role).lower().strip()
    persona_path = _PERSONA_FILES.get(normalized)
    if persona_path is None:
        raise ValueError(f"unknown persona role: {normalized}")
    sections = [_bundled_markdown(persona_path)]
    for skill in _ROLE_SKILLS[normalized]:
        sections.append(_bundled_markdown(f"builtin_skills/{skill}/SKILL.md"))
    if custom_prompt.strip():
        sections.append("## Delegated emphasis\n\n" + custom_prompt.strip())
    prompt = "\n\n---\n\n".join(sections)
    if len(prompt) > WORKER_SYSTEM_MAX_CHARS and normalized != "janus":
        raise RuntimeError(
            f"bundled worker prompt exceeds {WORKER_SYSTEM_MAX_CHARS} chars: {normalized}"
        )
    return prompt


def is_read_only_request(text: str | None) -> bool:
    lowered = str(text or "").lower()
    return (
        any(word in lowered for word in READ_ONLY_REQUEST_WORDS)
        and not any(word in lowered for word in MUTATING_REQUEST_WORDS)
    )


def worker_spawn_pressure(snapshot: dict, *, max_model_queue: int =
                          MAX_MODEL_QUEUE_FOR_SPAWN) -> str | None:
    """현재 로컬 생성 queue가 worker fan-out을 더 받을 수 있는지 판정한다."""
    if snapshot.get("closed"):
        return "scheduler_closed"
    model = snapshot["resources"][scheduler_mod.ResourceClass.MODEL_GENERATION.value]
    if int(model.get("queued", 0)) >= max_model_queue:
        return "model_queue_backpressure"
    return None


def worker_spawn_fit(
    policy: str, user_task: str | None, *, allow_autonomous_workers: bool = False,
) -> str | None:
    """1-slot 로컬 모델에서 첫 worker가 중복 구현 비용을 만들지 않게 한다.

    생성이 직렬인 1-slot 환경에서는 역할과 무관하게 worker가 추가 prefill/generation을
    만든다. 사용자가 명시적으로 위임했거나 profile이 override한 경우에만 허용한다.
    """
    if policy != "autonomous" or not user_task or allow_autonomous_workers:
        return None
    lowered = user_task.lower()
    if any(phrase in lowered for phrase in EXPLICIT_WORKER_PHRASES):
        return None
    if "워커" in lowered and any(action in lowered for action in EXPLICIT_WORKER_KOREAN_ACTIONS):
        return None
    return "autonomous_implementer_overhead"


def effective_worker_step_limit(
    requested: object,
    configured: int,
    dispatch_snapshot: dict,
    scheduler_snapshot: dict,
) -> int:
    """Keep enough dispatch steps for the parent to integrate worker output.

    A worker on a one-slot local model is sequential work, not parallel work. On a
    tight dispatch, letting it consume the ordinary eight-step worker allowance can
    leave the parent unable to inspect partial edits, correct them, or even produce a
    final response. Reserve 60% of the dispatch step budget for the parent in that
    topology. Larger/default dispatches still retain the configured worker cap.

    바닥값은 1이 아니라 MIN_WORKER_STEPS다. 도구를 쓰는 worker는 첫 step에서 tool call만
    내보내므로, 결과를 읽고 답할 두 번째 step이 없으면 반드시 budget_exhausted로 끝난다.
    """
    try:
        requested_limit = max(1, min(int(requested), 50))
    except (TypeError, ValueError):
        requested_limit = 8
    limit = max(MIN_WORKER_STEPS, min(int(configured), requested_limit))
    model = scheduler_snapshot["resources"][
        scheduler_mod.ResourceClass.MODEL_GENERATION.value
    ]
    if int(model.get("cap", 1)) > 1:
        return limit

    dispatch_limit = int(dispatch_snapshot["limits"]["step_limit"])
    dispatch_used = int(dispatch_snapshot["usage"]["steps"])
    # 예비분은 **남은 몫**에서 뗀다. 전체에서 떼면 부모가 이미 쓴 step을 두 번 세는 셈이라,
    # 대화가 조금만 길어져도 worker 몫이 0으로 떨어져 바닥값에 눌러앉는다.
    remaining = max(0, dispatch_limit - dispatch_used)
    parent_reserve = (
        remaining * SINGLE_SLOT_PARENT_RESERVE_NUMERATOR
        + SINGLE_SLOT_PARENT_RESERVE_DENOMINATOR - 1
    ) // SINGLE_SLOT_PARENT_RESERVE_DENOMINATOR
    worker_room = max(MIN_WORKER_STEPS, remaining - parent_reserve)
    return min(limit, worker_room)


def effective_worker_role(
    policy: str,
    requested_role: str,
    dispatch_step_limit: int,
    scheduler_snapshot: dict,
) -> tuple[str, str | None]:
    """Turn a forced sequential implementer into a scout on tight local runs.

    ``fixed_one`` is useful as a policy/control experiment, but on one generation
    slot a second implementer duplicates the parent's full edit loop. A read-only
    scout still satisfies the one-worker topology while leaving one owner for edits.
    The adaptation is explicit in telemetry and tool results.
    """
    model = scheduler_snapshot["resources"][
        scheduler_mod.ResourceClass.MODEL_GENERATION.value
    ]
    if (
        policy == "fixed_one"
        and requested_role == "implementer"
        and int(model.get("cap", 1)) == 1
        and int(dispatch_step_limit) <= TIGHT_DISPATCH_STEP_LIMIT
    ):
        return "researcher", "single_slot_tight_dispatch_scout"
    return requested_role, None


def resolve_local_model(name: str) -> str:
    pattern = LOCAL_MODELS.get(name)
    if pattern is None:
        raise spec_mod.SpecError(
            f"모르는 모델 {name!r} (등록됨: {sorted(LOCAL_MODELS)})"
        )
    hits = glob.glob(os.path.expanduser(pattern))
    if not hits:
        raise spec_mod.SpecError(
            f"{name!r}의 로컬 파일을 찾을 수 없습니다: {pattern}\n"
            "  먼저 받으세요: hf download orcarouter/Qwen3.8-27B-Uncensored-MLX --include '4-bit/*'"
        )
    return hits[0]


def make_client() -> OpenAI:
    # ponytail: local-only. 클라우드 provider가 실제로 필요해지면 spec에 provider 필드 재추가.
    # 모듈 함수로 둔 이유: 테스트가 monkeypatch로 FakeClient를 꽂는다.
    return OpenAI(
        base_url=MLX_BASE_URL, api_key="none",
        timeout=MODEL_REQUEST_TIMEOUT_SECONDS,
    )


# ─────────────────────────── 클리핑 (구 trace.py에서 구출) ───────────────────────────

MAX_STR = 4000
MAX_LIST = 50
MODEL_REQUEST_TIMEOUT_SECONDS = 1_200.0


def _clip(v):
    """저장·전송분을 자른다 — 원문이 27B 출력이면 수십 KB가 우습다."""
    if isinstance(v, str):
        return v if len(v) <= MAX_STR else v[:MAX_STR] + f"… (+{len(v) - MAX_STR}자)"
    if isinstance(v, dict):
        return {k: _clip(x) for k, x in v.items()}
    if isinstance(v, list):
        clipped = [_clip(x) for x in v[:MAX_LIST]]
        if len(v) > MAX_LIST:
            clipped.append(f"… (+{len(v) - MAX_LIST}개)")
        return clipped
    return v


ORCH_ID = "orchestrator"  # 실행 간 고정 — A/B 비교가 node_id로 매칭된다


class Orchestration:
    """WS 연결 하나 = 오케스트레이터 대화 하나.

    send(dict)                     : 스레드 안전 WS 송신 (서버가 제공)
    approver(node_id, tool, args, context): 블로킹 승인 브리지 (서버가 제공)
    """

    def __init__(self, spec: dict, *, send: Callable[[dict], None],
                 approver: Callable[[str, str, dict, WorkspaceContext], bool] | None,
                 workspace_context: WorkspaceContext,
                 task_id: str | None = None, session_id: str | None = None,
                 clock: Callable[[], int] | None = None,
                 scheduler: scheduler_mod.ResourceScheduler | None = None,
                 priority: int | None = None,
                 queue_timeout: float | None = None,
                 budget: dict | None = None,
                 budget_usage: dict | None = None,
                 on_skill_loaded: Callable[[str, str, int], None] | None = None):
        self.spec = spec
        self.send = send
        self.client = make_client()
        self.model = resolve_local_model(spec["model"])
        self.tools = list(spec.get("tools") or [])
        self.max_steps = spec.get("max_steps", 15)
        self.worker_policy = spec.get("worker_policy", "autonomous")
        self.allow_autonomous_workers = bool(spec.get("allow_autonomous_workers", False))
        self.worker_roles = set(spec.get("worker_roles") or WORKER_ROLES)
        self.worker_role_sequence = list(spec.get("worker_role_sequence") or [])
        self.worker_enabled = self.worker_policy != "none"
        if task_id is not None and task_id != workspace_context.task_id:
            raise ValueError("task_id와 WorkspaceContext.task_id가 다릅니다")
        self.workspace_context = workspace_context
        self.active_workspace_context: WorkspaceContext | None = None
        self.scheduler = scheduler or scheduler_mod.default_scheduler()
        # max_steps는 budget이 dispatch step_limit을 안 주었을 때의 기본값으로 남긴다 —
        # "빠듯한 dispatch" 판정이 이 값을 본다.
        self.budget = budget_mod.normalize_budget(budget, max_steps=self.max_steps)
        # 다만 한 턴의 루프 상한으로 **덮어쓰지는** 않는다. dispatch step 예산은 세션 전체에
        # 누적되므로, 누적치를 그대로 루프 상한으로 쓰면 한 턴이 60번까지 돌 수 있다.
        self.max_steps = min(
            int(self.max_steps), int(self.budget["dispatch"]["step_limit"])
        )
        self.priority = int(
            self.budget["queue"]["priority"] if priority is None else priority
        )
        self.queue_timeout = (
            self.budget["queue"]["timeout_ms"] / 1000
            if queue_timeout is None else queue_timeout
        )
        self.dispatch_budget = budget_mod.BudgetTracker(
            "dispatch", self.budget["dispatch"], initial_usage=budget_usage
        )
        self.budget_exhausted_reason: str | None = None

        self.cancel = threading.Event()
        self.worker_cancels: dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self.node_events: dict[str, list] = {}
        self.node_usage: dict[str, dict] = {}
        self.spans: list[dict] = []          # [0]=오케스트레이터, 이후 워커 스폰 순
        self.worker_seq = 0
        self.active_workers = 0
        self.worker_requests: dict[str, dict] = {}
        self.worker_records: dict[str, dict] = {}
        telemetry_kwargs = {
            "task_id": workspace_context.task_id,
            "workspace_id": workspace_context.workspace_id,
            "session_id": session_id,
        }
        if clock is not None:
            telemetry_kwargs["clock"] = clock
        self.telemetry = telemetry_mod.ExecutionTelemetry(**telemetry_kwargs)
        self.current_dispatch_id: str | None = None
        self.last_dispatch_id: str | None = None
        self.first_message: str | None = None
        self.last_text = ""
        self.cancelled_turn = False
        self.turn_failed = False  # 턴이 예외로 죽음 — 저장본이 success로 거짓말하지 않게
        self.current_user_text = ""
        self.skill_snapshots = list(spec.get("skills") or [])
        self.loaded_skill_versions: set[str] = {
            str(item["skill_version_id"])
            for item in self.skill_snapshots
            if item.get("loaded_at")
        }
        self.on_skill_loaded = on_skill_loaded

        # 승인 매핑: auto → 전부 허용, ask → 브리지, 브리지 없음 → 거부.
        # 위험 도구의 실제 게이트는 tools.dispatch다 — 여기는 정책 선택일 뿐.
        approval = spec.get("approval", "auto")
        if approval == "auto":
            self._approve_for = lambda nid, context: (lambda name, args: True)
        elif approver is not None:
            self._approve_for = lambda nid, context: (
                lambda name, args: approver(nid, name, args, context)
            )
        else:
            self._approve_for = lambda nid, context: (lambda name, args: False)

        self.create_worker = self._make_create_worker()
        self.worker_control_tools = self._make_worker_control_tools()
        registry = dict(T.REGISTRY)
        if self.worker_enabled:
            registry[self.create_worker["name"]] = self.create_worker
            registry.update({tool["name"]: tool for tool in self.worker_control_tools})
        self.skill_tools = self._make_skill_tools()
        registry.update({tool["name"]: tool for tool in self.skill_tools})
        runtime_tools = (
            self.tools
            + (["create_worker"] + [tool["name"] for tool in self.worker_control_tools]
               if self.worker_enabled else [])
            + [tool["name"] for tool in self.skill_tools]
        )
        profile_prompt = str(spec.get("system_prompt") or "").strip()
        base_prompt = persona_prompt("janus", custom_prompt=profile_prompt)
        context_preamble = str(spec.get("context_preamble") or "").strip()
        if context_preamble:
            base_prompt = f"{base_prompt}\n\n{context_preamble}"
        catalog = self._skill_catalog_prompt()
        if catalog:
            base_prompt = f"{base_prompt}\n\n{catalog}"
        context_policy = spec.get("context_policy") or {}
        self.session = agent_mod.Session(
            agent_mod.build_system_prompt(
                base_prompt,
                runtime_tools, registry=registry),
            registry=registry,
            context_max_chars=int(context_policy.get("max_chars", 24_000)),
            context_recent_blocks=int(context_policy.get("recent_blocks", 8)),
            summary_max_chars=int(context_policy.get("summary_max_chars", 4_000)),
        )

    # ── 스팬/이벤트 ──

    def _skill_catalog_prompt(self) -> str:
        if not self.skill_snapshots:
            return ""
        lines = [
            "Janus skills are available on demand. Load an auto skill only when its description "
            "matches the current task. Load a manual skill only when the user explicitly names it.",
        ]
        for item in self.skill_snapshots:
            qualified = f"{item['namespace']}:{item['name']}"
            lines.append(
                f"- {qualified} [{item['activation_mode']}]: {item.get('description') or 'No description'}"
            )
        return "\n".join(lines)

    def _find_skill(self, requested: str) -> tuple[dict | None, str | None]:
        needle = requested.strip().lower()
        qualified = [
            item for item in self.skill_snapshots
            if f"{item['namespace']}:{item['name']}".lower() == needle
        ]
        if qualified:
            return qualified[0], None
        named = [item for item in self.skill_snapshots if str(item["name"]).lower() == needle]
        if len(named) == 1:
            return named[0], None
        if len(named) > 1:
            options = [f"{item['namespace']}:{item['name']}" for item in named]
            return None, f"동명 스킬입니다. namespace를 포함하세요: {options}"
        return None, f"활성화되지 않은 스킬입니다: {requested}"

    def _make_skill_tools(self) -> list[dict]:
        if not self.skill_snapshots:
            return []

        def load_skill(name: str, reason: str = "", **_) -> dict:
            item, error = self._find_skill(name)
            if item is None:
                return {"error": error}
            if item["activation_mode"] == "manual":
                names = {
                    str(item["name"]).lower(),
                    f"{item['namespace']}:{item['name']}".lower(),
                    f"/{item['name']}".lower(),
                }
                user_text = self.current_user_text.lower()
                explicitly_named = any(
                    re.search(
                        rf"(?<![a-z0-9_.-]){re.escape(candidate)}(?![a-z0-9_.-])",
                        user_text,
                    )
                    for candidate in names
                )
                if not explicitly_named:
                    return {"error": "수동 스킬은 사용자가 이름을 명시한 턴에서만 불러올 수 있습니다"}
            version_id = str(item["skill_version_id"])
            if version_id in self.loaded_skill_versions:
                return {
                    "name": item["name"], "already_loaded": True,
                    "instructions": "이 스킬은 현재 세션에 이미 로딩되었습니다.",
                    "resources": [],
                }
            compiled = item.get("compiled") or {}
            required = set((compiled.get("capabilities") or {}).get("required") or [])
            available = set(self.tools)
            if self.worker_enabled:
                available.add("create_worker")
            missing = sorted(required - available)
            if missing:
                return {"error": f"스킬에 필요한 capability가 AgentProfile에 없습니다: {missing}"}
            instructions = str(compiled.get("instructions") or "")
            instructions = instructions.replace("{{input}}", self.current_user_text)
            instructions = instructions.replace("{{workspace_root}}", str(self.workspace_context.root))
            instructions = instructions.replace("{{session_id}}", str(self.telemetry.session_id or ""))
            prompt_tokens = max(1, len(instructions) // 4)
            if self.on_skill_loaded is not None:
                try:
                    self.on_skill_loaded(version_id, reason[:1000], prompt_tokens)
                except Exception as callback_error:
                    return {"error": f"스킬 로딩 상태를 저장하지 못했습니다: {callback_error}"}
            self.loaded_skill_versions.add(version_id)
            self.send({
                "type": "skill_loaded", "skill_id": item["skill_id"],
                "skill_version_id": version_id, "name": item["name"],
                "namespace": item["namespace"], "reason": reason[:1000],
                "prompt_tokens": prompt_tokens,
            })
            resources = [
                resource["path"] for resource in compiled.get("resources") or []
                if not resource.get("binary")
            ]
            return {
                "name": item["name"], "namespace": item["namespace"],
                "version": item["version"], "instructions": instructions,
                "execution": compiled.get("execution") or {}, "resources": resources,
            }

        def read_skill_resource(name: str, path: str, **_) -> dict:
            item, error = self._find_skill(name)
            if item is None:
                return {"error": error}
            if str(item["skill_version_id"]) not in self.loaded_skill_versions:
                return {"error": "load_skill로 스킬을 먼저 불러오세요"}
            resources = (item.get("compiled") or {}).get("resources") or []
            matches = [resource for resource in resources if resource.get("path") == path]
            if not matches:
                return {"error": f"스킬 리소스가 없습니다: {path}"}
            resource = matches[0]
            if resource.get("binary") or resource.get("content") is None:
                return {"error": f"바이너리 스킬 리소스는 읽을 수 없습니다: {path}"}
            return {"name": item["name"], "path": path, "content": resource["content"]}

        return [
            T._t(
                "load_skill", load_skill,
                lambda value: (
                    f"# Skill {value['namespace']}:{value['name']} v{value['version']}\n"
                    f"{value['instructions']}\n\n"
                    f"Resources: {value['resources']}"
                ),
                T._obj(
                    ["name"],
                    name={"type": "string", "description": "Skill name or namespace:name."},
                    reason={"type": "string", "description": "Why this skill matches the task."},
                ),
                "Load one enabled Janus skill only when it matches the current task.",
                "Load the smallest relevant skill. Manual skills require an explicit user request.",
                resource_class="cpu_tool", render_chars=16_000,
            ),
            T._t(
                "read_skill_resource", read_skill_resource,
                lambda value: value["content"],
                T._obj(
                    ["name", "path"],
                    name={"type": "string", "description": "Loaded skill name."},
                    path={"type": "string", "description": "Exact resource path listed by load_skill."},
                ),
                "Read a text resource from an already loaded Janus skill.",
                "Read only resources needed for the current step.",
                resource_class="io_tool", render_chars=16_000,
            ),
        ]

    def _concurrent_worker_limit(self) -> int:
        """이번 턴에 사용자가 명시적으로 요청한 worker 수를 Dispatch 스냅샷보다 우선한다.

        Dispatch 예산은 세션의 **첫** 메시지로 한 번 정해진 뒤 대화 내내 유지된다.
        대화 도중 "워커 두개"라고 해도 그 요청은 스냅샷에 없으므로, 명시 요청까지
        첫 메시지 기준으로 막으면 사용자가 시킨 일이 조용히 거부된다.
        total_limit은 그대로 지킨다 — 상한을 넘기자는 게 아니라 스냅샷의 시점 오류만 보정한다.
        """
        configured = int(self.budget["workers"]["concurrent_limit"])
        requested = adaptive_mod.requested_worker_count(
            {"objective": self.current_user_text}
        )
        if not requested:
            return configured
        return max(configured, min(requested, int(self.budget["workers"]["total_limit"])))

    def _sink(self, node_id: str, kind: str, data: dict, *,
              dispatch_id: str | None = None) -> None:
        clipped = {k: _clip(v) for k, v in data.items()}
        measured = self.telemetry.record_event(
            kind, node_id=node_id,
            dispatch_id=(self.current_dispatch_id if dispatch_id is None else dispatch_id),
            worker_id=None if node_id == ORCH_ID else node_id, **clipped,
        )
        ev = {"type": "agent_event", **measured}
        with self.lock:
            self.node_events.setdefault(node_id, []).append(ev)
            if kind == "usage":
                u = self.node_usage.setdefault(
                    node_id, {"prompt_tokens": 0, "completion_tokens": 0})
                u["prompt_tokens"] += data.get("prompt_tokens", 0)
                u["completion_tokens"] += data.get("completion_tokens", 0)
        self.send(ev)

    def _open_span(self, node_id: str, *, label: str | None,
                   parent_id: str | None, input: dict) -> dict:
        span = {"id": uuid.uuid4().hex[:12], "node_id": node_id, "status": "running",
                "started_ms": self.telemetry.elapsed_ms(), "input": _clip(input),
                "parent_id": parent_id, "label": label,
                "task_id": self.telemetry.task_id,
                "workspace_id": self.telemetry.workspace_id,
                "session_id": self.telemetry.session_id,
                "dispatch_id": self.current_dispatch_id,
                "worker_id": None if node_id == ORCH_ID else node_id}
        with self.lock:
            self.spans.append(span)
        self.send({"type": "span_start", "span": dict(span)})
        return span

    def _close_span(self, span: dict, status: str, output: dict) -> None:
        with self.lock:
            span["status"] = status
            span["duration_ms"] = round(
                self.telemetry.elapsed_ms() - span["started_ms"], 3
            )
            span["output"] = _clip(output)
            span["events"] = list(self.node_events.get(span["node_id"], []))
            span["usage"] = self.node_usage.get(span["node_id"])
        self.send({"type": "span_end", "span": dict(span)})

    # ── create_worker 스킬 ──

    def _make_create_worker(self) -> dict:
        def handler(name: str = "", system_prompt: str = "", task: str = "",
                    tools: list | None = None, max_steps: int = 8,
                    role: str = "implementer", context: str = "") -> dict:
            workspace_context = self.active_workspace_context
            if workspace_context is None:
                return {"error": "active WorkspaceContext가 없습니다"}

            requested_role = str(role).lower().strip()
            if requested_role not in WORKER_ROLES:
                return {"error": f"알 수 없는 worker role: {requested_role}"}
            # In autonomous mode the adaptive role sequence is a recommended
            # topology, not an allow-list. A user can explicitly request a
            # different valid role (for example an implementer after a
            # transient connection failure selected a researcher-first retry).
            if (
                requested_role not in self.worker_roles
                and self.worker_policy != "autonomous"
            ):
                return {
                    "error": f"adaptive policy가 worker role을 허용하지 않습니다: {requested_role}",
                    "reason": "worker_role_not_allowed",
                    "allowed_roles": sorted(self.worker_roles),
                }
            scheduler_state = self.scheduler.snapshot()
            model_state = scheduler_state["resources"][
                scheduler_mod.ResourceClass.MODEL_GENERATION.value
            ]
            # 역할은 권한 프로필이다. 명시한 implementer를 최적화 명목으로
            # read-only researcher로 바꾸면 필요한 편집/셸 도구가 사라진다.
            role, role_adaptation = requested_role, None
            # The role sequence describes useful topology to the orchestrator;
            # it must not silently replace an explicit, policy-allowed role.
            # Doing so turned implementation workers into one-step read-only
            # scouts after the parent had already completed discovery.
            # 부분집합 규칙: 워커 도구 ⊆ 오케스트레이터의 spec.tools.
            # 조사·계획·검증 역할은 결과를 수정하지 못하도록 읽기 전용 교집합만 받는다.
            requested_tools = list(dict.fromkeys(str(t) for t in (tools or [])))
            candidates = requested_tools or list(self.tools)
            allowed = [tool for tool in candidates if tool in self.tools]
            if role in READ_ONLY_WORKER_ROLES:
                allowed = [tool for tool in allowed if tool in T.READ_ONLY]

            custom_system = str(system_prompt).strip()
            raw_system = persona_prompt(role, custom_prompt=custom_system)
            if role == "implementer":
                raw_system += (
                    "\n\nAfter applying the requested edits, return a concise factual result "
                    "immediately. If run_bash is not in your tools, do not search for a shell, "
                    "Python executable, or test runner; leave independent verification to the "
                    "orchestrator. Do not broaden the original contract."
                )
            raw_task = str(task) or "(no task)"
            if role_adaptation is not None:
                raw_system = (
                    "You are a read-only scout for a single-slot local coding agent. "
                    "Inspect only the files needed for the delegated task. Return concise, "
                    "specific evidence: current definitions, required edits, invariants, and "
                    "paths that must not change. Never attempt a write or edit tool, never "
                    "broaden the contract, and finish immediately after the investigation."
                )
                raw_task = (
                    "Investigate the delegated task without changing the workspace. Return a "
                    "concise implementation handoff for the parent.\n\nOriginal delegated task:\n"
                    + raw_task
                )
            raw_context = str(context or "")
            prepared_system = raw_system[:WORKER_SYSTEM_MAX_CHARS]
            prepared_task = raw_task[:WORKER_TASK_MAX_CHARS]
            prepared_context = raw_context[:WORKER_CONTEXT_MAX_CHARS]
            if prepared_context:
                prepared_task += "\n\nRelevant context (only what this subtask needs):\n" + prepared_context

            fingerprint = hashlib.sha256(json.dumps(
                {"name": str(name), "requested_role": requested_role, "role": role,
                 "system": prepared_system,
                 "task": prepared_task, "tools": allowed},
                ensure_ascii=False, sort_keys=True,
            ).encode("utf-8")).hexdigest()[:20]
            rejection: str | None = None
            reused: dict | None = None
            with self.lock:
                if self.worker_policy == "none":
                    rejection = "worker_policy_none"
                elif ((existing := self.worker_requests.get(fingerprint)) is not None
                      and existing["status"] in {"completed", "completed_partial"}):
                    reused = dict(existing)
                elif self.worker_policy == "fixed_one" and self.worker_seq >= 1:
                    rejection = "worker_policy_fixed_one"
                elif self.worker_seq >= int(self.budget["workers"]["total_limit"]):
                    rejection = "worker_total_budget"
                elif self.active_workers >= self._concurrent_worker_limit():
                    rejection = "worker_concurrent_budget"
                elif existing is not None and existing["status"] in {
                    "queued", "running", "waiting_approval", "stopping"
                }:
                    rejection = "duplicate_worker_running"
                elif (fit := worker_spawn_fit(
                    self.worker_policy, self.current_user_text,
                    allow_autonomous_workers=self.allow_autonomous_workers,
                )) is not None:
                    rejection = fit
                elif (pressure := worker_spawn_pressure(scheduler_state)) is not None:
                    rejection = pressure
                else:
                    self.worker_seq += 1
                    seq = self.worker_seq
                    self.active_workers += 1
                    concurrent = self.active_workers
                    self.dispatch_budget.record_worker_start(concurrent)
                    slug = re.sub(
                        r"[^a-z0-9]+", "-", str(name).lower()
                    ).strip("-") or "worker"
                    wid = f"w{seq}-{slug}"
                    self.worker_requests[fingerprint] = {
                        "status": "running", "worker": wid, "role": role,
                    }
            if reused is not None:
                self._sink(ORCH_ID, "worker_result_reused", {
                    "worker": reused["worker"], "role": role,
                    "fingerprint": fingerprint,
                })
                return {
                    "worker": reused["worker"], "role": role,
                    "result": reused["result"], "reused": True,
                }
            if rejection is not None:
                self._sink(ORCH_ID, "worker_spawn_suppressed", {
                    "reason": rejection, "name": str(name), "role": role,
                    "fingerprint": fingerprint,
                    "model_generation": model_state,
                })
                messages = {
                    "worker_policy_none": "worker policy가 none이라 worker를 만들 수 없습니다",
                    "duplicate_worker_running": "같은 subtask worker가 이미 실행 중입니다",
                    "worker_policy_fixed_one": "worker policy가 fixed_one이라 추가 worker를 만들 수 없습니다",
                    "worker_total_budget": "worker total budget을 소진했습니다",
                    "worker_concurrent_budget": "worker concurrent budget을 소진했습니다",
                    "autonomous_implementer_overhead": (
                        "단일 model slot에서 중복 implementer 비용이 예상돼 spawn을 억제했습니다"
                    ),
                    "model_queue_backpressure": "model queue 압력 때문에 worker spawn을 억제했습니다",
                    "scheduler_closed": "scheduler가 종료돼 worker spawn을 억제했습니다",
                }
                if rejection in {
                    "worker_policy_fixed_one", "autonomous_implementer_overhead",
                    "duplicate_worker_running", "model_queue_backpressure",
                }:
                    previous = next(iter(self.worker_requests.values()), None)
                    prior = str((previous or {}).get("result") or "").strip()
                    guidance = (
                        f"WORKER NOT CREATED: spawn suppressed ({rejection}). "
                        "Do not say that this worker was created, deployed, or started. "
                        "Do not call create_worker again. Inspect the current workspace and "
                        "complete/integrate the task directly, then explicitly report that the "
                        "worker request was suppressed."
                    )
                    if prior:
                        guidance = prior + "\n\n" + guidance
                    return {
                        "worker": (previous or {}).get("worker"),
                        "role": role, "created": False, "result": guidance,
                        "suppressed": True, "reason": rejection,
                    }
                return {"error": messages[rejection], "reason": rejection}
            # extra_tools를 안 넘기므로 워커는 create_worker를 절대 못 받는다 (깊이 1).
            dispatch_snapshot = self.dispatch_budget.snapshot()
            steps = effective_worker_step_limit(
                max_steps,
                int(self.budget["worker"]["step_limit"]),
                dispatch_snapshot,
                scheduler_state,
            )
            if role_adaptation is not None:
                # One local generation can issue several parallel read calls. Waiting for a
                # second/third scout generation is sequential overhead; the parent owns edits.
                steps = 1

            cancel = threading.Event()
            worker_limits = dict(self.budget["worker"])
            worker_limits["step_limit"] = steps
            worker_budget = budget_mod.BudgetTracker(f"worker:{wid}", worker_limits)
            self.worker_cancels[wid] = cancel
            # span_start를 본 UI/headless harness가 즉시 stop을 보내도 놓치지 않도록
            # cancel handle을 공개한 뒤 span 이벤트를 보낸다.
            span = self._open_span(wid, label=str(name) or wid,
                                   parent_id=self.spans[0]["id"] if self.spans else None,
                                   input={"task": prepared_task, "tools": allowed,
                                          "role": role, "requested_role": requested_role,
                                          "role_adaptation": role_adaptation,
                                          "context_chars": len(prepared_context)})
            if role_adaptation is not None:
                self._sink(wid, "worker_role_adapted", {
                    "requested_role": requested_role,
                    "effective_role": role,
                    "reason": role_adaptation,
                    "model_generation_cap": model_state.get("cap", 1),
                    "dispatch_step_limit": self.budget["dispatch"]["step_limit"],
                })
            self._sink(wid, "worker_step_budget_reserved", {
                "requested_steps": max_steps,
                "effective_steps": steps,
                "dispatch_steps_used": dispatch_snapshot["usage"]["steps"],
                "dispatch_step_limit": dispatch_snapshot["limits"]["step_limit"],
                "model_generation_cap": model_state.get("cap", 1),
            })
            self._sink(wid, "worker_context_prepared", {
                "role": role,
                "system_chars": len(prepared_system),
                "task_chars": len(prepared_task),
                "context_chars": len(prepared_context),
                "requested_tools": requested_tools,
                "allowed_tools": allowed,
                "truncated": (
                    len(raw_system) > WORKER_SYSTEM_MAX_CHARS
                    or len(raw_task) > WORKER_TASK_MAX_CHARS
                    or len(raw_context) > WORKER_CONTEXT_MAX_CHARS
                ),
            })
            record = {
                "worker": wid, "name": str(name) or wid,
                "role": role, "requested_role": requested_role,
                "role_adaptation": role_adaptation, "status": "queued",
                "result": "", "error": None, "tools": list(allowed),
                "task": prepared_task, "system_prompt": prepared_system,
                "workspace_context": workspace_context, "max_steps": steps,
                "cancel": cancel, "idle": threading.Event(),
                "launched": threading.Event(), "session": agent_mod.Session(
                    agent_mod.build_system_prompt(prepared_system, allowed),
                    registry=T.REGISTRY,
                ),
                "worker_budget": worker_budget, "fingerprint": fingerprint,
                "span": span, "followups": [], "changed_paths": set(),
                "dispatch_id": self.current_dispatch_id,
            }
            with self.lock:
                self.worker_records[wid] = record
            threading.Thread(
                target=lambda: self._run_worker_record(record),
                name=f"janus-{wid}", daemon=True,
            ).start()
            # 완료가 아니라 모델 큐 등록까지만 기다린다. 이 배리어가 없으면 부모가
            # 다음 생성 슬롯을 먼저 잡아 워커가 시작도 못 한 채 뒤로 밀린다.
            record["launched"].wait(1.0)
            return {
                "worker": wid, "role": role, "requested_role": requested_role,
                "role_adaptation": role_adaptation, "status": record["status"],
                "created": True, "tools": allowed,
                "message": (
                    f"Worker {wid} was spawned in the background. Use wait_worker or "
                    "worker_status before integrating its result."
                ),
            }

        return T._t(
            "create_worker", handler,
            lambda v: str(v.get("result") or v.get("message")
                          or v.get("warning") or v.get("error") or ""),
            T._obj(["name", "task"],
                   name={"type": "string", "maxLength": 40,
                         "description": "Short worker name."},
                   system_prompt={
                       "type": "string", "maxLength": 300,
                       "description": (
                           "Optional task-specific emphasis only. The selected persona and "
                           "skills are loaded automatically; never restate them."
                       ),
                   },
                   task={
                       "type": "string", "maxLength": 500,
                       "description": (
                           "Concrete subtask; reference the parent Task for existing details."
                       ),
                   },
                   role={"type": "string",
                         "enum": [
                             "scout", "planner", "prototyper", "implementer",
                             "verifier", "operator", "researcher",
                         ],
                         "description": (
                             "Worker persona. Scout, planner, researcher, and verifier "
                             "are forced read-only. Researcher is a legacy alias for scout."
                         )},
                   context={"type": "string", "maxLength": 500,
                            "description": "Only the minimal context needed by this subtask."},
                   tools={"type": "array", "items": {"type": "string"},
                          "description": (
                              "Optional restrictive subset. Omit or pass [] to inherit "
                              "role-appropriate tools from the parent."
                          )},
                   max_steps={"type": "number", "description": "Step budget (default 8)."}),
            "Spawn a background worker for a separable subtask and return its id immediately.",
            "Spawn only for a separable subtask. Pass minimal context and the smallest "
            "tool restriction only when needed. After spawning, use wait_worker before "
            "integrating its result. Duplicate work and queue pressure are suppressed.",
            resource_class="cpu_tool",
        )

    def _set_worker_status(self, record: dict, status: str, **extra) -> None:
        with self.lock:
            record["status"] = status
            record.update(extra)
            if request := self.worker_requests.get(record["fingerprint"]):
                request.update(status=status, **extra)
        self._sink(
            record["worker"], "worker_state", {"status": status, **extra},
            dispatch_id=record.get("dispatch_id"),
        )

    @staticmethod
    def _worker_view(record: dict) -> dict:
        return {
            "worker": record["worker"], "name": record["name"],
            "role": record["role"], "requested_role": record["requested_role"],
            "status": record["status"], "result": record.get("result") or "",
            "error": record.get("error"), "tools": list(record.get("tools") or []),
            "queued_followups": len(record.get("followups") or []),
        }

    def _run_worker_record(self, record: dict) -> None:
        wid = record["worker"]
        budget = record["worker_budget"]
        budget.begin_active()
        current_task = record["task"]
        text = ""
        write_calls: dict[str, str] = {}

        def emit(kind: str, **data) -> None:
            if kind == "resource_queue_wait" and data.get("resource") == "model_generation":
                record["launched"].set()
                self._set_worker_status(record, "queued")
            elif kind in {"resource_lease_acquired", "model_generation_start"}:
                if data.get("resource", "model_generation") == "model_generation":
                    record["launched"].set()
                    self._set_worker_status(record, "running")
            call_id = str(data.get("call_id") or "")
            if kind == "tool_start" and data.get("name") in {"write_file", "edit_file"}:
                path = str((data.get("args") or {}).get("path") or "").strip()
                if call_id and path:
                    write_calls[call_id] = path
            elif kind == "tool_run_end" and data.get("status") == "success":
                if path := write_calls.get(call_id):
                    record["changed_paths"].add(path)
            self._sink(wid, kind, data, dispatch_id=record.get("dispatch_id"))

        def approve(name: str, args: dict) -> bool:
            self._set_worker_status(record, "waiting_approval", tool=name)
            try:
                return self._approve_for(wid, record["workspace_context"])(name, args)
            finally:
                if not record["cancel"].is_set():
                    self._set_worker_status(record, "running")

        try:
            while True:
                self._set_worker_status(record, "queued")
                text, _ = agent_mod.run(
                    client=self.client, model=self.model,
                    system_prompt=record["system_prompt"], task=current_task,
                    tool_names=record["tools"],
                    workspace_context=record["workspace_context"],
                    approve=approve, emit=emit, max_steps=record["max_steps"],
                    cancel=record["cancel"], scheduler=self.scheduler,
                    priority=self.priority, queue_timeout=self.queue_timeout,
                    budget_trackers=[budget, self.dispatch_budget],
                    thinking=False, session=record["session"],
                )
                with self.lock:
                    current_task = record["followups"].pop(0) if record["followups"] else None
                if current_task is None or record["cancel"].is_set():
                    break

            if budget.exhausted_reason:
                touched = ", ".join(sorted(record["changed_paths"])) or "none recorded"
                note = (
                    f"Worker reached its focused local budget at {budget.exhausted_reason}. "
                    f"Successful write targets: {touched}. Do not spawn another worker; "
                    "validate the existing changes and make only the necessary correction."
                )
                result = (str(text).strip() + "\n\n" + note).strip()
                self._set_worker_status(
                    record, "completed_partial", result=result,
                    warning=budget.exhausted_reason,
                )
                self._close_span(record["span"], "error", {
                    "error": budget.exhausted_reason, "partial_result": text,
                    "budget": budget.snapshot(),
                })
            elif record["cancel"].is_set():
                self._set_worker_status(
                    record, "cancelled", result=text, error="사용자가 워커를 중단함",
                )
                self._close_span(record["span"], "error", {
                    "error": "사용자가 워커를 중단함",
                })
            else:
                result = str(text or "")
                self._set_worker_status(record, "completed", result=result)
                self._close_span(record["span"], "success", {
                    "result": result, "role": record["role"],
                })
        except Exception as error:
            record["launched"].set()
            message = f"{type(error).__name__}: {error}"
            self._set_worker_status(record, "failed", error=message)
            self._close_span(record["span"], "error", {"error": message})
        finally:
            budget.end_active()
            record["idle"].set()
            with self.lock:
                self.active_workers = max(0, self.active_workers - 1)
            self.worker_cancels.pop(wid, None)

    def _make_worker_control_tools(self) -> list[dict]:
        def lookup(worker: str) -> tuple[dict | None, dict | None]:
            record = self.worker_records.get(str(worker))
            return (record, None) if record else (None, {"error": f"알 수 없는 worker: {worker}"})

        def status(worker: str = "") -> dict:
            if worker:
                record, error = lookup(worker)
                return error or self._worker_view(record)
            return {"workers": [self._worker_view(r) for r in self.worker_records.values()]}

        def wait(worker: str, timeout_seconds: float = 30) -> dict:
            record, error = lookup(worker)
            if error:
                return error
            finished = record["idle"].wait(max(0.0, min(float(timeout_seconds), 60.0)))
            view = self._worker_view(record)
            view["finished"] = finished
            if not finished:
                view["message"] = "Worker is still active; call wait_worker again."
            return view

        def send(worker: str, message: str) -> dict:
            record, error = lookup(worker)
            if error:
                return error
            followup = str(message).strip()
            if not followup:
                return {"error": "message가 비어 있습니다"}
            with self.lock:
                if record["status"] in {"queued", "running", "waiting_approval"}:
                    record["followups"].append(followup)
                    record["idle"].clear()
                    return {"worker": worker, "status": record["status"], "queued": True}
                if record["status"] not in {"completed", "completed_partial"}:
                    return {"error": f"worker가 후속 작업을 받을 수 없습니다: {record['status']}"}
                record.update(
                    task=followup, cancel=threading.Event(), error=None,
                    dispatch_id=self.current_dispatch_id, idle=threading.Event(),
                    launched=threading.Event(), worker_budget=budget_mod.BudgetTracker(
                        f"worker:{worker}:followup", dict(self.budget["worker"]),
                    ),
                )
                self.worker_cancels[worker] = record["cancel"]
                self.active_workers += 1
                parent_id = self.spans[0]["id"] if self.spans else None
            record["span"] = self._open_span(
                worker, label=record["name"], parent_id=parent_id,
                input={"task": followup, "tools": record["tools"],
                       "role": record["role"], "followup": True},
            )
            self._set_worker_status(record, "queued")
            threading.Thread(
                target=lambda: self._run_worker_record(record),
                name=f"janus-{worker}-followup", daemon=True,
            ).start()
            record["launched"].wait(1.0)
            return {"worker": worker, "status": record["status"], "queued": True}

        def stop(worker: str) -> dict:
            record, error = lookup(worker)
            if error:
                return error
            if record["status"] in {"completed", "completed_partial", "failed", "cancelled"}:
                return {"worker": worker, "status": record["status"], "stopped": False}
            record["cancel"].set()
            self._set_worker_status(record, "stopping")
            return {"worker": worker, "status": "stopping", "stopped": True}

        render = lambda value: json.dumps(value, ensure_ascii=False)
        return [
            T._t("worker_status", status, render,
                 T._obj([], worker={"type": "string", "description": "Worker id; omit for all."}),
                 "Get background worker state and result without blocking.",
                 "Use for a quick progress check.", resource_class="cpu_tool"),
            T._t("wait_worker", wait, render,
                 T._obj(["worker"], worker={"type": "string"},
                        timeout_seconds={"type": "number", "description": "0-60 seconds."}),
                 "Wait briefly for a background worker and return its state or result.",
                 "Wait for spawned workers before integrating results.", resource_class="cpu_tool"),
            T._t("send_worker", send, render,
                 T._obj(["worker", "message"], worker={"type": "string"},
                        message={"type": "string", "maxLength": 1000}),
                 "Send a focused follow-up to an existing worker session.",
                 "Use for correction or clarification, not unrelated work.", resource_class="cpu_tool"),
            T._t("stop_worker", stop, render,
                 T._obj(["worker"], worker={"type": "string"}),
                 "Stop a queued or running background worker.",
                 "Stop only when its work is no longer needed.", resource_class="cpu_tool"),
        ]

    # ── 턴 실행 ──

    def turn(self, text: str, *, dispatch_id: str | None = None) -> None:
        """블로킹 — asyncio.to_thread로 호출된다. ReAct 한 턴."""
        self.cancel.clear()
        self.cancelled_turn = False
        self.turn_failed = False
        dispatch_id = self.telemetry.begin_turn(dispatch_id)
        self.current_dispatch_id = dispatch_id
        self.last_dispatch_id = dispatch_id
        self.current_user_text = text
        context = self.workspace_context.for_dispatch(dispatch_id)
        self.active_workspace_context = context
        self.dispatch_budget.begin_active()
        turn_tools = list(self.tools)
        if is_read_only_request(text):
            turn_tools = [tool for tool in turn_tools if tool in T.READ_ONLY]
            removed = sorted(set(self.tools) - set(turn_tools))
            if removed:
                self._sink(ORCH_ID, "parent_tools_restricted", {
                    "mode": "read_only", "removed_tools": removed,
                })
        if self.first_message is None:
            self.first_message = text
            self._open_span(ORCH_ID, label=self.spec.get("name"), parent_id=None,
                            input={"task": text})
        try:
            last, _ = agent_mod.run(
                client=self.client, model=self.model,
                system_prompt=self.spec.get("system_prompt") or "",  # session이 이미 보유
                task=text,
                tool_names=(
                    turn_tools
                    + (["create_worker"] + [tool["name"] for tool in self.worker_control_tools]
                       if self.worker_enabled else [])
                    + [tool["name"] for tool in self.skill_tools]
                ),
                workspace_context=context,
                approve=self._approve_for(ORCH_ID, context),
                emit=lambda kind, **d: self._sink(ORCH_ID, kind, d),
                max_steps=self.max_steps,
                cancel=self.cancel,
                extra_tools=(
                    ([self.create_worker] + self.worker_control_tools
                     if self.worker_enabled else []) + self.skill_tools
                ),
                session=self.session,
                scheduler=self.scheduler,
                priority=self.priority,
                queue_timeout=self.queue_timeout,
                budget_trackers=[self.dispatch_budget],
            )
            if last:
                self.last_text = last
            if self.cancel.is_set():
                self.cancelled_turn = True
        except Exception:
            self.turn_failed = True
            raise
        finally:
            self.dispatch_budget.end_active()
            self.budget_exhausted_reason = self.dispatch_budget.exhausted_reason
            status = ("error" if self.turn_failed else
                      "cancelled" if self.cancelled_turn else "success")
            self.telemetry.end_turn(dispatch_id, status=status)
            self.current_dispatch_id = None
            self.active_workspace_context = None
        # turn_end는 서버가 저장을 마친 뒤 보낸다 — 여기서 보내면 히스토리 갱신이 빈손

    # ── 취소 ──

    def cancel_all(self) -> None:
        """현재 턴 중단 (오케스트레이터 + 라이브 워커 전부). 세션은 유지된다."""
        # ponytail: cancel == stop-turn; "대화 리셋"이 필요해지면 그건 새 WS 연결이다.
        self.cancel.set()
        for ev in list(self.worker_cancels.values()):
            ev.set()

    def stop_worker(self, node_id: str) -> None:
        ev = self.worker_cancels.get(node_id)
        if ev is not None:
            ev.set()
            if record := self.worker_records.get(node_id):
                self._set_worker_status(record, "stopping")

    # ── 저장 스냅샷 ──

    def snapshot_spans(self) -> list[dict]:
        """저장용 사본 — 오케스트레이터 스팬을 채워 영원한 running이 남지 않게 마감."""
        with self.lock:
            spans = [dict(s) for s in self.spans]
            for s in spans:
                if s["node_id"] == ORCH_ID:
                    s["status"] = ("error" if self.cancelled_turn or self.turn_failed
                                   else "success")
                    s["duration_ms"] = round(
                        self.telemetry.elapsed_ms() - s["started_ms"], 3
                    )
                    s["output"] = _clip({"reply": self.last_text})
                    s["events"] = list(self.node_events.get(ORCH_ID, []))
                    s["usage"] = self.node_usage.get(ORCH_ID)
                elif s["status"] == "running":
                    # 저장 시점에 아직 도는 워커 — 이벤트만이라도 남긴다
                    s["events"] = list(self.node_events.get(s["node_id"], []))
                    s["usage"] = self.node_usage.get(s["node_id"])
        return spans

    def snapshot_telemetry(self) -> dict:
        return self.telemetry.snapshot(
            usage=self.node_usage,
            worker_count=self.worker_seq,
        )

    def snapshot_budget(self) -> dict:
        return self.dispatch_budget.snapshot()
