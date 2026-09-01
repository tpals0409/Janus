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
from collections.abc import Callable
from pathlib import Path

from openai import OpenAI

from . import adaptive as adaptive_mod
from . import agent as agent_mod
from . import budget as budget_mod
from . import intent as intent_mod
from . import ownership as ownership_mod
from . import scheduler as scheduler_mod
from . import spec as spec_mod
from . import telemetry as telemetry_mod
from . import tools as T
from . import verification as verification_mod
from .workspace import WorkspaceContext

# UI의 짧은 이름 -> 로컬에 실제로 존재하는 스냅샷 경로.
#
# 절대 repo ID("orcarouter/Qwen3.8-...")를 보내면 안 된다. mlx_vlm.server는 로드되지
# 않은 모델 id를 받으면 HuggingFace에서 **리포 전체를**(모든 quant, ~80GB) 내려받기
# 시작하고, 그동안 요청은 응답 없이 매달린다. 로컬 경로만 넘긴다.
# 폴백 전용 — 평소에는 Electron이 준 JANUS_LOCAL_MODEL_PATH를 쓴다.
# repo마다 스냅샷 안 배치가 다르다(mlx-community는 루트, orcarouter는 4-bit/).
LOCAL_MODELS = {
    "qwen3.8-27b": "~/.cache/huggingface/hub/"
                   "models--mlx-community--Qwen3.8-27B-4bit/snapshots/*",
    "qwen3.8-27b-uncensored": "~/.cache/huggingface/hub/"
                              "models--orcarouter--Qwen3.8-27B-Uncensored-MLX/snapshots/*/4-bit",
}

MLX_BASE_URL = "http://localhost:8080/v1"
WORKER_SYSTEM_MAX_CHARS = 8_000
WORKER_TASK_MAX_CHARS = 6_000
WORKER_CONTEXT_MAX_CHARS = 4_000
# 반환 방향 핸드오프 예산 — 워커 보고가 오케스트레이터 컨텍스트로 돌아올 때의
# 상한. 스폰 방향(system/task/context)과 대칭으로, 장황한 보고가 가장 비싼
# 컨텍스트를 오염하지 못하게 엔진이 강제한다. 전문은 이벤트·성과 스토어에 보존.
WORKER_RESULT_MAX_CHARS = 4_000
WORKER_ROLES = {
    "scout", "researcher", "planner", "prototyper",
    "implementer", "verifier", "operator",
}
READ_ONLY_WORKER_ROLES = {"scout", "researcher", "planner", "verifier"}
# write 워컈 파일 소유권: 이 도구 중 하나라도 갖는 비읽기 역할은 임대를 요구한다.
# run_bash가 포함되는 이유는 셸이 사실상의 쓰기 경로이기 때문이다 (임대는 선언된
# writer 간 겹침을 차단하고, 셸 내부 쓰기는 여전히 승인 게이트에 의존한다).
_WRITE_CAPABILITY_TOOLS = frozenset({"write_file", "edit_file", "run_bash"})
WRITE_ROOT_PARTITION = "*"  # 소유 파티션 미선언 write 워커 = 워크스페이스 전체 배타
# 이 상태에 도달한 워커 기록은 더 이상 갱신되지 않는다 — 회수·훅·view가 같은 집합을 쓴다.
TERMINAL_WORKER_STATUSES = frozenset(
    {"completed", "completed_partial", "failed", "cancelled"}
)
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
    """호환 위임 — 판정 규칙과 어휘의 원천은 intent 모듈이다."""
    return intent_mod.is_read_only_request(text)


def worker_status_violation(old: str | None, new: str) -> str | None:
    """종료 상태에서의 불법 재전이를 판정한다 (로그 전용, 실행은 막지 않는다).

    라이브 상태 간 전이는 전부 합법이다. 종료 후 유일한 합법 재진입은
    send_worker 후속의 completed/completed_partial → queued 재기동이다.
    늦게 도착한 스레드가 종료 기록을 덮는 레이스(completed → failed 등)를
    텔레메트리에서 보이게 하는 것이 목적이다.
    """
    if old is None or old == new or old not in TERMINAL_WORKER_STATUSES:
        return None
    if new == "queued" and old in {"completed", "completed_partial"}:
        return None
    return f"{old}->{new}"


def worker_spawn_pressure(snapshot: dict, *, max_model_queue: int =
                          MAX_MODEL_QUEUE_FOR_SPAWN) -> str | None:
    """현재 로컬 생성 queue가 worker fan-out을 더 받을 수 있는지 판정한다."""
    if snapshot.get("closed"):
        return "scheduler_closed"
    model = snapshot["resources"][scheduler_mod.ResourceClass.MODEL_GENERATION.value]
    if int(model.get("queued", 0)) >= max_model_queue:
        return "model_queue_backpressure"
    return None


# 억제에는 두 종류가 있다. 일시적인 것(슬롯이 잠깐 찼다, 같은 일이 이미 돌고 있다)에
# "직접 하라"고 시키면 위임이 조용히 사라진다 — 워커 격리를 두는 이유 자체가 무너진다.
# 정책이 구조적으로 워커를 금지할 때만 오케스트레이터가 직접 한다.
TEMPORARY_SUPPRESSIONS = frozenset({"model_queue_backpressure", "duplicate_worker_running"})


def suppression_guidance(rejection: str) -> str:
    """스폰이 억제됐을 때 오케스트레이터에게 돌려줄 지침."""
    if rejection == "model_queue_backpressure":
        follow_up = (
            "This is temporary. Do not implement the work yourself. Call wait_worker "
            "for the running worker, then create this worker again once the slot frees."
        )
    elif rejection == "duplicate_worker_running":
        follow_up = (
            "The same subtask is already running. Do not implement it again yourself. "
            "Call wait_worker and integrate its result."
        )
    else:
        follow_up = (
            "This worker policy structurally forbids another worker, so complete the "
            "task directly, then explicitly report that the worker request was suppressed."
        )
    return (
        f"WORKER NOT CREATED: spawn suppressed ({rejection}). "
        "Do not say that this worker was created, deployed, or started. " + follow_up
    )


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
        return "scout", "single_slot_tight_dispatch_scout"
    return requested_role, None


def resolve_local_model(name: str) -> str:
    # Electron이 이미 해석한 경로가 있으면 그게 이긴다 — 캐시 루트를 양쪽이 따로 계산하면
    # HF_HOME을 쓰는 사용자에게 "다운로드는 됐는데 앱은 없다고 한다"가 생긴다.
    resolved = os.environ.get("JANUS_LOCAL_MODEL_PATH")
    if resolved and os.path.isdir(resolved):
        return resolved
    pattern = LOCAL_MODELS.get(name)
    if pattern is None:
        raise spec_mod.SpecError(
            f"모르는 모델 {name!r} (등록됨: {sorted(LOCAL_MODELS)})"
        )
    hits = glob.glob(os.path.expanduser(pattern))
    if not hits:
        raise spec_mod.SpecError(
            f"{name!r}의 로컬 파일을 찾을 수 없습니다: {pattern}\n"
            "  Janus 설정 화면의 '로컬 모델'에서 내려받으세요."
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
                 on_skill_loaded: Callable[[str, str, int], None] | None = None,
                 on_worker_outcome: Callable[[dict], None] | None = None,
                 on_outcomes_delivered: Callable[[list[str]], None] | None = None,
                 persisted_worker_outcomes: list[dict] | None = None,
                 prior_spawn_counts: dict | None = None):
        self.spec = spec
        self.send = send
        self.client = make_client()
        self.model = resolve_local_model(spec["model"])
        self.tools = list(spec.get("tools") or [])
        # 이번 턴에 실제로 허용된 도구. read-only 축소가 걸린 턴에만 채워지고,
        # create_worker가 워커 도구를 여기에 가둔다 (턴 종료 시 None으로 복귀).
        self.turn_tools: list[str] | None = None
        # finish_turn(completed)의 완료 게이트. 비어 있으면 게이트 없이 모델의
        # 자기 신고를 그대로 인정한다 (Task 밖 세션·명령 미설정 프로젝트).
        self.acceptance_command = str(spec.get("acceptance_command") or "").strip()
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
        # 재접속·재시작이 스폰 상한을 되돌리지 않도록 영속 기록에서 복원한다.
        # 예산 usage와 같은 스코프다 — 여기가 0으로 시작하면 role_limit이 막으려던
        # 재스폰이 브라우저를 새로고침할 때마다 새로 열린다.
        prior = prior_spawn_counts or {}
        self.worker_seq = int(prior.get("total") or 0)
        # 같은 역할 재스폰 상한 강제용 카운터 — 세션 수명 기준(total_limit과 동일 스코프).
        # send_worker 후속은 재스폰이 아니므로 가산하지 않는다.
        self.role_spawn_counts: dict[str, int] = {
            str(role): int(count)
            for role, count in (prior.get("by_role") or {}).items()
        }
        self.active_workers = 0
        self.worker_requests: dict[str, dict] = {}
        self.worker_records: dict[str, dict] = {}
        # 같은 worktree를 공유하는 병렬 write 워커의 동일 파일 쓰기를 차단하는
        # 엔진 소유 배타 임대 (구 DAG 엔진의 파일 소유권 불변식을 현재 플로우로 이식).
        self.write_ownership = ownership_mod.FileOwnershipTable()
        # 워커 성과의 턴 경계 영속화 시임. 서버가 도메인 스토어 연결을 꽂으면
        # 크래시 이후에도 결과 복원이 가능해진다 — 런타임은 저장소를 모른다.
        self.on_worker_outcome = on_worker_outcome
        # 회수 노트로 실제 주입한 성과를 저장소에서 소비 처리하는 시임. 없으면
        # 메모리에서만 지워져 다음 접속이 같은 행을 다시 읽는다.
        self.on_outcomes_delivered = on_outcomes_delivered
        # 이전 실행에서 SQLite로 남긴 워커 성과 — 첫 턴 시작에서 한 번만 회수 노트로
        # 소비되고 메모리에서 비워진다 (같은 기록의 이중 주입 방지).
        self.persisted_worker_outcomes = list(persisted_worker_outcomes or [])
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
        self.turn_outcome: dict | None = None
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
        self.finish_turn = self._make_finish_turn_tool()
        registry = dict(T.REGISTRY)
        registry[self.finish_turn["name"]] = self.finish_turn
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
            + [self.finish_turn["name"]]
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
            def fail(message: str) -> dict:
                self.send({
                    "type": "skill_load_failed", "requested": name,
                    "reason": message,
                })
                return {"error": message}

            item, error = self._find_skill(name)
            if item is None:
                return fail(str(error))
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
                    return fail("수동 스킬은 사용자가 이름을 명시한 턴에서만 불러올 수 있습니다")
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
                return fail(f"스킬에 필요한 capability가 AgentProfile에 없습니다: {missing}")
            instructions = str(compiled.get("instructions") or "")
            instructions = instructions.replace("{{input}}", self.current_user_text)
            instructions = instructions.replace("{{workspace_root}}", str(self.workspace_context.root))
            instructions = instructions.replace("{{session_id}}", str(self.telemetry.session_id or ""))
            # 세션이 실측 usage로 보정한 chars/token 비율을 그대로 쓴다
            prompt_tokens = max(
                1, int(len(instructions) / self.session.chars_per_token)
            )
            if self.on_skill_loaded is not None:
                try:
                    self.on_skill_loaded(version_id, reason[:1000], prompt_tokens)
                except Exception as callback_error:
                    return fail(f"스킬 로딩 상태를 저장하지 못했습니다: {callback_error}")
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
                    node_id, {"prompt_tokens": 0, "completion_tokens": 0,
                              "cached_tokens": 0})
                u["prompt_tokens"] += data.get("prompt_tokens", 0)
                u["completion_tokens"] += data.get("completion_tokens", 0)
                # APC 적중 누적 — prompt_tokens 대비 비율이 실측 캐시 적중률
                u["cached_tokens"] = (
                    u.get("cached_tokens", 0) + data.get("cached_tokens", 0)
                )
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

    def _run_acceptance_gate(self) -> dict | None:
        """완료 신고를 Task의 acceptance command로 검증한다.

        모델이 스스로 "completed"라고 선언하는 것만으로 턴이 완료로 굳으면
        verification은 UI 버튼에만 존재하고 에이전트 계약에는 없는 것과 같다.
        여기서 실제 exit code를 받아온다. 명령이 없으면 None을 돌려 게이트를
        건너뛴다 — 없는 근거를 있는 척하지 않는다.
        """
        if not self.acceptance_command:
            return None
        context = self.active_workspace_context or self.workspace_context
        try:
            return verification_mod.run(
                self.acceptance_command, context,
                scheduler=self.scheduler, priority=self.priority,
                cancel=self.cancel, queue_timeout=self.queue_timeout,
                emit=lambda kind, **data: self._sink(ORCH_ID, kind, data),
            )
        except Exception as error:
            # 게이트가 못 돌았다는 사실 자체가 결과다 — 통과로 처리하지 않는다.
            return {
                "command": self.acceptance_command, "exit_code": None,
                "stdout": "", "stderr": "",
                "error": f"{type(error).__name__}: {error}",
            }

    def _make_finish_turn_tool(self) -> dict:
        def handler(
            outcome: str, summary: str, evidence: list[str] | None = None, **_,
        ) -> dict:
            normalized = str(outcome).strip().lower()
            allowed = {"completed", "partial", "input_required", "mockup_review"}
            if normalized not in allowed:
                return {"error": f"지원하지 않는 turn outcome입니다: {normalized}"}
            record = {
                "outcome": normalized,
                "summary": str(summary).strip()[:1000],
                "evidence": [str(item)[:500] for item in (evidence or [])[:10]],
            }
            gate: dict | None = None
            if normalized == "completed":
                gate = self._run_acceptance_gate()
            if gate is not None and gate.get("exit_code") != 0:
                # 자기 신고를 뒤집는다. summary는 모델이 쓴 그대로 두고 근거만
                # 사실로 바꾼다 — 사용자가 무엇이 주장이고 무엇이 측정인지 본다.
                detail = str(
                    gate.get("error") or gate.get("stderr") or gate.get("stdout") or ""
                ).strip()[:300]
                record["outcome"] = "partial"
                record["evidence"] = ([
                    f"acceptance 실패: {self.acceptance_command} "
                    f"(exit={gate.get('exit_code')})",
                    *([detail] if detail else []),
                ] + record["evidence"])[:10]
                # summary가 그대로 사용자에게 보이는 최종 답변이 된다(agent.run의
                # terminal 처리). 완료 주장만 남으면 화면은 성공, 기록은 partial인
                # 거짓 상태가 된다.
                record["summary"] = (
                    f"[검증 실패] acceptance command `{self.acceptance_command}`가 "
                    f"exit {gate.get('exit_code')}로 끝나 완료로 인정되지 않았습니다.\n\n"
                    f"에이전트 보고: {record['summary']}"
                )[:1000]
                self.turn_outcome = record
                self._sink(ORCH_ID, "acceptance_gate", {
                    "command": self.acceptance_command,
                    "exit_code": gate.get("exit_code"), "passed": False,
                    "claimed": normalized,
                })
                return {
                    **record,
                    "recorded": True,
                    "acceptance": {
                        "command": self.acceptance_command,
                        "exit_code": gate.get("exit_code"), "passed": False,
                        "detail": detail,
                    },
                    "instruction": (
                        "acceptance command가 실패해 completed 신고가 partial로 "
                        "내려갔습니다. 사용자에게 무엇이 남았는지 사실대로 보고하세요."
                    ),
                }
            self.turn_outcome = record
            if gate is not None:
                self._sink(ORCH_ID, "acceptance_gate", {
                    "command": self.acceptance_command,
                    "exit_code": gate.get("exit_code"), "passed": True,
                    "claimed": normalized,
                })
            return {
                **record,
                "recorded": True,
                **({"acceptance": {
                    "command": self.acceptance_command, "exit_code": 0, "passed": True,
                }} if gate is not None else {}),
                "instruction": "이제 사용자에게 간결한 최종 답변을 하고 도구 호출을 멈추세요.",
            }

        return T._t(
            "finish_turn", handler,
            lambda value: json.dumps(value, ensure_ascii=False),
            T._obj(
                ["outcome", "summary"],
                outcome={
                    "type": "string",
                    "enum": ["completed", "partial", "input_required", "mockup_review"],
                    "description": "Durable outcome for the current Task turn.",
                },
                summary={
                    "type": "string", "maxLength": 1000,
                    "description": "Concise factual result or required user decision.",
                },
                evidence={
                    "type": "array", "maxItems": 10,
                    "items": {"type": "string", "maxLength": 500},
                    "description": "Changed files, commands, or other fresh evidence.",
                },
            ),
            "Record the Task outcome immediately before the final user-facing answer.",
            "Call exactly once at the completion boundary. Use completed only with fresh "
            "evidence, input_required only for a concrete user decision, and mockup_review "
            "only after producing a reviewable mockup. When the Task declares an "
            "acceptance command, completed runs it and is downgraded to partial if it "
            "fails — claiming completion you cannot back up only wastes a turn.",
            # 턴 종결자는 절대 굶으면 안 된다 — cpu_tool(cap 2)에서는 블로킹
            # 워커 대기 두 건 뒤에 큐 타임아웃까지 밀린다.
            resource_class="io_tool", render_chars=4000, terminal=True,
        )

    def snapshot_turn_outcome(self) -> dict:
        return dict(self.turn_outcome or {
            "outcome": "partial", "summary": "finish_turn was not called", "evidence": [],
        })

    def _make_create_worker(self) -> dict:
        def handler(name: str = "", system_prompt: str = "", task: str = "",
                    tools: list | None = None, max_steps: int = 8,
                    role: str = "implementer", context: str = "",
                    owned_paths: list | None = None, **unknown) -> dict:
            if unknown:
                # 27B가 objective/allowed_scope 같은 구조화 계약 필드를 반복적으로
                # 발명한다. TypeError의 내부 함수명 노이즈 대신, 허용 필드와 함께
                # "계약은 task 본문에 쓰라"는 즉시 교정 가능한 거부를 돌려준다.
                return {
                    "error": (
                        f"unknown fields: {sorted(unknown)}. Accepted fields are "
                        "name, system_prompt, task, tools, max_steps, role, "
                        "context, owned_paths. Put the delegation contract "
                        "(objective, scope, done-when) inside the task text."
                    ),
                    "reason": "invalid_worker_fields",
                }
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
            # 부분집합의 상한은 self.tools가 아니라 **이번 턴에 허용된** 도구다.
            # 아니면 read-only 턴에서 쓰기 워커를 스폰해 턴 가드를 우회한다.
            parent_tools = self.tools if self.turn_tools is None else self.turn_tools
            candidates = requested_tools or list(parent_tools)
            allowed = [tool for tool in candidates if tool in parent_tools]
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

            # write 워커 소유권 임대 준비. 유효성은 예산 소비 전에 여기서 검사해
            # 안전하지 않은 입력이 스폰 회계를 오염하지 않게 한다.
            write_capable = (
                role not in READ_ONLY_WORKER_ROLES
                and bool(_WRITE_CAPABILITY_TOOLS.intersection(allowed))
            )
            spawn_lease = None
            try:
                partitions = tuple(dict.fromkeys(
                    ownership_mod.normalize_partition(str(item))
                    for item in (owned_paths or []) if str(item).strip()
                ))
            except ownership_mod.InvalidPartition as error:
                return {
                    "error": f"owned_paths에 안전하지 않은 경로가 있습니다: {error}",
                    "reason": "invalid_write_partition",
                }
            if write_capable and not partitions:
                partitions = (WRITE_ROOT_PARTITION,)

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
                elif (
                    int(self.role_spawn_counts.get(role, 0))
                    >= int(self.budget["workers"].get("role_limit", 3))
                ):
                    # 페르소나가 권하는 "재시도 2회 후 보고"를 엔진이 강제한다 —
                    # 지문만 바꾼 무한 재디스패치는 여기서 끊긴다.
                    rejection = "worker_role_budget"
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
                    # 임대 획득은 스폰 수락과 같은 임계영역에서 원자적으로 — 충돌 시
                    # seq·active_workers·fingerprint 회계를 소비하지 않는다.
                    if write_capable:
                        try:
                            spawn_lease = self.write_ownership.acquire(
                                f"wlease-{uuid.uuid4().hex[:8]}", partitions)
                        except ownership_mod.OwnershipConflict:
                            rejection = "write_partition_conflict"
                    if rejection is None:
                        self.worker_seq += 1
                        self.role_spawn_counts[role] = (
                            int(self.role_spawn_counts.get(role, 0)) + 1
                        )
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
                    "write_partition_conflict": (
                        "다른 write 워커가 겹치는 파일 소유권 임대를 보유 중입니다"
                    ),
                    "invalid_write_partition": "owned_paths에 안전하지 않은 경로가 있습니다",
                    "worker_role_budget": (
                        "같은 역할의 worker 재스폰 상한(workers.role_limit)을 소진했습니다"
                    ),
                }
                if rejection in {
                    "worker_policy_fixed_one", "autonomous_implementer_overhead",
                    "duplicate_worker_running", "model_queue_backpressure",
                }:
                    previous = next(iter(self.worker_requests.values()), None)
                    prior = str((previous or {}).get("result") or "").strip()
                    guidance = suppression_guidance(rejection)
                    if prior:
                        guidance = prior + "\n\n" + guidance
                    return {
                        "worker": (previous or {}).get("worker"),
                        "role": role, "created": False, "result": guidance,
                        "suppressed": True, "reason": rejection,
                    }
                payload = {"error": messages[rejection], "reason": rejection}
                if rejection == "write_partition_conflict":
                    payload["held"] = self.write_ownership.snapshot()
                    payload["result"] = (
                        "WORKER NOT CREATED: another write worker holds an overlapping "
                        "file lease. Call wait_worker for the holding worker and integrate "
                        "its result first, or re-spawn with disjoint owned_paths "
                        "(workspace-relative files or directories this worker will modify). "
                        "Do not implement the work yourself."
                    )
                elif rejection == "worker_role_budget":
                    payload["counts"] = {
                        "role": role,
                        "spawned": int(self.role_spawn_counts.get(role, 0)),
                        "role_limit": int(self.budget["workers"].get("role_limit", 3)),
                        "total_spawns": self.worker_seq,
                        "total_limit": int(self.budget["workers"]["total_limit"]),
                    }
                    payload["result"] = (
                        "WORKER NOT CREATED: this dispatch exhausted its re-spawn "
                        "allowance for this role. Do not spawn the same role again. "
                        "Either delegate the remaining work once under a different "
                        "allowed role with a smaller, split task, or report the "
                        "repeated failure to the user and call finish_turn. Do not "
                        "implement the work yourself."
                    )
                return payload
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
            try:
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
                    "write_lease": spawn_lease,
                    "owned_partitions": (
                        list(partitions) if spawn_lease is not None else []
                    ),
                }
                with self.lock:
                    self.worker_records[wid] = record
                threading.Thread(
                    target=lambda: self._run_worker_record(record),
                    name=f"janus-{wid}", daemon=True,
                ).start()
            except BaseException:
                # 스레드 기동 전 실패 시 임대를 즉시 반납해 다음 writer가 막히지 않게 한다.
                if spawn_lease is not None:
                    spawn_lease.release()
                # 스폰 회계도 함께 되돌린다. 임대만 반납하면 active_workers가 영영
                # 줄지 않아(감소는 _run_worker_record의 finally에만 있다) 동시성
                # 슬롯이 소실되고, 남은 fingerprint가 이후 같은 스폰을 전부
                # duplicate_worker_running으로 막는다.
                with self.lock:
                    self.worker_seq = max(0, self.worker_seq - 1)
                    self.role_spawn_counts[role] = max(
                        0, int(self.role_spawn_counts.get(role, 0)) - 1
                    )
                    self.active_workers = max(0, self.active_workers - 1)
                    self.worker_requests.pop(fingerprint, None)
                    self.worker_records.pop(wid, None)
                self.worker_cancels.pop(wid, None)
                try:
                    self._sink(ORCH_ID, "worker_spawn_rolled_back", {
                        "worker": wid, "role": role, "fingerprint": fingerprint,
                    })
                except Exception:
                    pass  # 여기서 죽으면 원래 실패 원인이 가려진다
                raise
            if spawn_lease is not None:
                self._sink(wid, "worker_write_lease_acquired", {
                    "owner": spawn_lease.owner,
                    "partitions": list(record["owned_partitions"]),
                })
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
                   owned_paths={"type": "array", "items": {"type": "string"},
                                "description": (
                                    "Workspace-relative files/directories this worker will "
                                    "modify. Write workers hold exclusive leases; omitting it "
                                    "leases the whole workspace, so parallel writers must "
                                    "declare disjoint paths."
                                )},
                   max_steps={"type": "number", "description": "Step budget (default 8)."}),
            "Spawn a background worker for a separable subtask and return its id immediately.",
            "Spawn only for a separable subtask. Pass minimal context and the smallest "
            "tool restriction only when needed. After spawning, use wait_worker before "
            "integrating its result. Duplicate work and queue pressure are suppressed. "
            "Workers that modify files hold an exclusive write lease on owned_paths; an "
            "overlapping lease rejects the spawn instead of racing on the same files.",
            resource_class="cpu_tool",
        )

    def _set_worker_status(self, record: dict, status: str, **extra) -> None:
        with self.lock:
            violation = worker_status_violation(record.get("status"), status)
            record["status"] = status
            record.update(extra)
            if request := self.worker_requests.get(record["fingerprint"]):
                request.update(status=status, **extra)
        if violation:
            self._sink(
                record["worker"], "worker_state_conflict",
                {"transition": violation},
                dispatch_id=record.get("dispatch_id"),
            )
        self._sink(
            record["worker"], "worker_state", {"status": status, **extra},
            dispatch_id=record.get("dispatch_id"),
        )
        if status in TERMINAL_WORKER_STATUSES and not record.get("outcome_recorded"):
            with self.lock:
                record["outcome_recorded"] = True
            if self.on_worker_outcome is not None:
                try:
                    view = self._worker_view(record)
                    # 영속 계약은 전문을 보존한다 — 절단은 모델 컨텍스트 전용.
                    view["result"] = str(record.get("result") or "")
                    # 영속 계약에 필요한 실행 식별자를 훅 페이로드에 보강한다.
                    view.update({
                        "task_id": self.telemetry.task_id,
                        "workspace_id": self.telemetry.workspace_id,
                        "session_id": self.telemetry.session_id,
                        "dispatch_id": record.get("dispatch_id"),
                    })
                    self.on_worker_outcome(view)
                except Exception as error:
                    # 관측 실패가 실행을 죽이지 않는다 — 단, 흔적은 남긴다.
                    self._sink(record["worker"], "worker_outcome_hook_failed",
                               {"error": f"{type(error).__name__}: {error}"})

    def _mark_worker_delivered(self, record: dict) -> None:
        """부모가 결과를 실제로 받아냄(wait/status/stop/send) — 회수 노트에서 제외."""
        with self.lock:
            record["delivered"] = True

    def _worker_view(self, record: dict) -> dict:
        status = record["status"]
        terminal = status in TERMINAL_WORKER_STATUSES
        recovery_action = (
            "integrate_result" if status == "completed"
            else "validate_partial_changes" if status == "completed_partial"
            else "continue_in_parent" if status in {"failed", "cancelled"}
            else "wait_or_stop"
        )
        raw_result = str(record.get("result") or "")
        result = raw_result
        if len(raw_result) > WORKER_RESULT_MAX_CHARS:
            # 머리(요약·계획)와 꼬리(결론·검증)를 남기고 가운데를 접는다.
            head, tail = raw_result[:3_000], raw_result[-800:]
            elided = len(raw_result) - len(head) - len(tail)
            result = (
                head + f"\n… [{elided} chars elided — full report is preserved "
                "in the event log and worker outcome store] …\n" + tail
            )
        return {
            "worker": record["worker"], "name": record["name"],
            "role": record["role"], "requested_role": record["requested_role"],
            "status": status, "terminal": terminal, "recovery_action": recovery_action,
            "result": result,
            "result_chars": len(raw_result),
            "result_truncated": len(raw_result) > WORKER_RESULT_MAX_CHARS,
            "error": record.get("error"), "tools": list(record.get("tools") or []),
            "queued_followups": len(record.get("followups") or []),
            # 워커 스레드가 emit에서 add하는 set이다. 락 없이 정렬하면 부모의
            # worker_status 호출이 "Set changed size during iteration"으로 죽는다.
            "changed_paths": self._changed_paths_snapshot(record),
            "owned_partitions": sorted(record.get("owned_partitions") or []),
            "recovery_limits": (
                {"file_reads": 1, "validation_commands": 1}
                if status == "completed_partial" else None
            ),
            "recovery_instruction": (
                "Use only the assigned workspace. Read each changed path at most once, "
                "run at most one targeted validation command, then call finish_turn. "
                "Do not inspect the source repository or repeat discovery."
                if status == "completed_partial" else None
            ),
        }

    def _parent_write_guards(self) -> list[dict]:
        """부모의 경로 지정 쓰기를 워커와 같은 소유권 테이블에 걸리게 한다.

        소유권 테이블은 create_worker에서만 참조돼, 정작 가장 활발한 writer인
        오케스트레이터는 면제였다. 워커가 src/를 임대한 채 도는 동안 부모가
        src/foo.py를 그냥 고칠 수 있으면 "같은 파일 동시 쓰기 불가"는 불변식이
        아니다. run_bash는 경로를 선언하지 않아 여기서 판정할 수 없다.
        """
        guarded = []
        for name in ("write_file", "edit_file"):
            base = T.REGISTRY.get(name)
            if base is None or name not in self.tools:
                continue

            def handler(_base=base, _name=name, **kwargs):
                path = str(kwargs.get("path") or "").strip()
                if path and (holder := self._write_conflict(path)) is not None:
                    return {
                        "error": (
                            f"{path}는 워커 {holder}가 쓰기 임대 중입니다. "
                            "wait_worker로 결과를 받아 통합한 뒤 수정하세요."
                        ),
                        "reason": "write_partition_conflict",
                    }
                return _base["handler"](**kwargs)

            guarded.append({**base, "handler": handler})
        return guarded

    def _write_conflict(self, path: str) -> str | None:
        """이 경로를 소유한 다른 워커의 id. 없으면 None."""
        try:
            held = self.write_ownership.snapshot()
        except Exception:
            return None
        def holds(partitions: list[str]) -> bool:
            try:
                return ownership_mod.owns_path(partitions, path)
            except ownership_mod.InvalidPartition:
                return False

        owner = next(
            (name for name, partitions in held.items() if holds(partitions)), None
        )
        if owner is None:
            return None
        with self.lock:
            for record in self.worker_records.values():
                lease = record.get("write_lease")
                if lease is not None and lease.owner == owner:
                    return str(record.get("worker") or owner)
        return owner

    def _changed_paths_snapshot(self, record: dict) -> list[str]:
        """워커가 쓰는 changed_paths를 락 안에서 안전하게 복사한다."""
        with self.lock:
            return sorted(record.get("changed_paths") or [])

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
                    # 부모가 worker_status/회수 노트에서 이 set을 정렬한다 —
                    # 같은 락 안에서 갱신해야 순회 중 변경으로 죽지 않는다.
                    with self.lock:
                        record["changed_paths"].add(path)
            self._sink(wid, kind, data, dispatch_id=record.get("dispatch_id"))

        def approve(name: str, args: dict) -> bool:
            self._set_worker_status(record, "waiting_approval", tool=name)
            try:
                return self._approve_for(wid, record["workspace_context"])(name, args)
            finally:
                # 승인 대기는 최대 300초다. 그 사이 다른 병렬 승인이 걸렸거나
                # 예산이 소진돼 상태가 바뀌었을 수 있다 — 무조건 running으로
                # 되돌리면 그 사실을 덮어써 UI와 기록이 거짓이 된다.
                if (not record["cancel"].is_set()
                        and record["status"] == "waiting_approval"):
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
                touched = (
                    ", ".join(self._changed_paths_snapshot(record))
                    or "none recorded"
                )
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
            if record.get("write_lease") is not None:
                # 취소·예산 소진·예외 어느 종료 경로로도 임대를 반납한다.
                record["write_lease"].release()
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
                if error:
                    return error
                if record["status"] in TERMINAL_WORKER_STATUSES:
                    self._mark_worker_delivered(record)
                return self._worker_view(record)
            views = []
            for r in self.worker_records.values():
                if r["status"] in TERMINAL_WORKER_STATUSES:
                    self._mark_worker_delivered(r)
                views.append(self._worker_view(r))
            return {"workers": views}

        def wait(worker: str, timeout_seconds: float = 30) -> dict:
            record, error = lookup(worker)
            if error:
                return error
            finished = record["idle"].wait(max(0.0, min(float(timeout_seconds), 60.0)))
            view = self._worker_view(record)
            view["finished"] = finished
            if finished and record["status"] in TERMINAL_WORKER_STATUSES:
                self._mark_worker_delivered(record)
            if not finished:
                view["message"] = (
                    "Worker is still active. Inspect with worker_status, wait once more if "
                    "there is fresh progress, or stop it and continue in the parent."
                )
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
                # 후속도 살아있는 워커 한 명이다 — 스폰과 같은 동시성 상한을 받는다.
                if self.active_workers >= self._concurrent_worker_limit():
                    return {
                        "error": "동시 실행 중인 워커가 상한입니다. 기존 워커를 정리한 뒤 다시 보내세요.",
                        "reason": "worker_concurrent_budget",
                    }
                # 예산은 워커 단위로 누적한다. 매 후속마다 새 트래커를 풀 한도로
                # 만들면 role_limit이 막으려던 무한 재디스패치가 send_worker로
                # 그대로 열린다.
                carried = dict(record["worker_budget"].snapshot()["usage"])
                followup_budget = budget_mod.BudgetTracker(
                    f"worker:{worker}:followup", dict(self.budget["worker"]),
                    initial_usage=carried,
                )
                if not followup_budget.available() or not (
                    followup_budget.exhaust_if_step_limit_reached()
                ):
                    return {
                        "error": (
                            f"worker {worker}의 예산이 소진됐습니다"
                            f"({followup_budget.exhausted_reason}). 후속 대신 새 워커를 "
                            "만들거나 부모가 직접 마무리하세요."
                        ),
                        "reason": "worker_budget_exhausted",
                    }
                # write 워커는 임대를 첫 실행의 finally에서 이미 반납했다. 다시 잡지
                # 않으면 "같은 파일 동시 쓰기 불가" 불변식 밖에서 재실행된다.
                followup_lease = None
                if record.get("owned_partitions"):
                    try:
                        followup_lease = self.write_ownership.acquire(
                            f"wlease-{uuid.uuid4().hex[:8]}",
                            tuple(record["owned_partitions"]),
                        )
                    except ownership_mod.OwnershipConflict:
                        return {
                            "error": (
                                "다른 워커가 같은 경로를 쓰고 있어 후속을 시작할 수 "
                                "없습니다."
                            ),
                            "reason": "write_partition_conflict",
                        }
                # 후속 작업은 새 종료 경계다 — 이전 성과는 전달 완료로 정리하고
                # 훅·회수 상태를 초기화해 다음 종료에서 다시 기록되게 한다.
                record.pop("delivered", None)
                record.pop("quiesce", None)
                record.update(
                    task=followup, cancel=threading.Event(), error=None,
                    outcome_recorded=False,
                    dispatch_id=self.current_dispatch_id, idle=threading.Event(),
                    launched=threading.Event(), worker_budget=followup_budget,
                    write_lease=followup_lease,
                )
                self.worker_cancels[worker] = record["cancel"]
                self.active_workers += 1
                parent_id = self.spans[0]["id"] if self.spans else None
            try:
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
            except BaseException:
                # 스폰 경로와 같은 이유로 회계·임대를 되돌린다 — 스레드가 안 떴으면
                # _run_worker_record의 finally가 영영 돌지 않는다.
                if followup_lease is not None:
                    followup_lease.release()
                    record["write_lease"] = None
                with self.lock:
                    self.active_workers = max(0, self.active_workers - 1)
                raise
            record["launched"].wait(1.0)
            return {"worker": worker, "status": record["status"], "queued": True}

        def stop(worker: str) -> dict:
            record, error = lookup(worker)
            if error:
                return error
            if record["status"] in TERMINAL_WORKER_STATUSES:
                self._mark_worker_delivered(record)
                return {"worker": worker, "status": record["status"], "stopped": False}
            # 부모가 명시적으로 버리기로 한 워커 — 회수 노트의 대상이 아니다.
            self._mark_worker_delivered(record)
            record["cancel"].set()
            self._set_worker_status(record, "stopping")
            return {"worker": worker, "status": "stopping", "stopped": True}

        def render(value: object) -> str:
            return json.dumps(value, ensure_ascii=False)

        # 제어 도구는 로컬 자원을 쓰지 않는다. cpu_tool(cap 2)에 두면 wait_worker가
        # 최대 60초·승인 대기가 최대 300초 동안 슬롯을 쥔 채 자고, 그동안 탈출구인
        # stop_worker와 턴 종결자 finish_turn까지 큐 타임아웃(300초)에 걸린다.
        return [
            T._t("worker_status", status, render,
                 T._obj([], worker={"type": "string", "description": "Worker id; omit for all."}),
                 "Get background worker state and result without blocking.",
                 "Use for a quick progress check.", resource_class="io_tool"),
            T._t("wait_worker", wait, render,
                 T._obj(["worker"], worker={"type": "string"},
                        timeout_seconds={"type": "number", "description": "0-60 seconds."}),
                 "Wait briefly for a background worker and return its state or result.",
                 "Wait for spawned workers before integrating results.", resource_class="io_tool"),
            T._t("send_worker", send, render,
                 T._obj(["worker", "message"], worker={"type": "string"},
                        message={"type": "string", "maxLength": 1000}),
                 "Send a focused follow-up to an existing worker session.",
                 "Use for correction or clarification, not unrelated work.", resource_class="io_tool"),
            T._t("stop_worker", stop, render,
                 T._obj(["worker"], worker={"type": "string"}),
                 "Stop a queued or running background worker.",
                 "Stop only when its work is no longer needed.", resource_class="io_tool"),
        ]

    def _undelivered_terminal_workers(self) -> list[dict]:
        """부모에게 전달되지 않은 종료(또는 강제 종료 예정) 워커 기록.

        recovery_notes는 같은 기록을 최대 3턴까지만 재노출하기 위한 계수다 —
        통합도 폐기 선언도 없는 채 컨텍스트를 영구 점유하지 않게 한다.
        """
        with self.lock:
            return [
                record for record in self.worker_records.values()
                if not record.get("delivered")
                and int(record.get("recovery_notes") or 0) < 3
                and (record["status"] in TERMINAL_WORKER_STATUSES
                     or record.get("quiesce"))
            ]

    def _format_recovery_digest(self, pending: list[dict]) -> str:
        if not pending:
            return ""
        lines = [
            "[janus runtime] Uncollected worker records from earlier turns "
            "(operational data, not user speech). Integrate them or state why "
            "they are discarded before starting new work:",
        ]
        for record in pending[:8]:
            snap = record.get("quiesce") or {}
            if record["status"] in TERMINAL_WORKER_STATUSES:
                shown_status = record["status"]
            else:  # 아직 종료 스레드가 뒤늦게 따라오는 중 — 스냅샷으로 표기
                shown_status = (
                    f"{snap.get('status', record['status'])}"
                    f"({snap.get('reason', 'quiesced')})"
                )
            parts = [f"- {record['worker']} [{record['role']} · {shown_status}]"]
            if task := " ".join(str(record.get("task") or "").split())[:80]:
                parts.append(f'task="{task}"')
            # quiesce 스냅샷이 없는 기록은 아직 워커 스레드가 살아 있을 수 있다.
            changed = snap.get("changed_paths") or self._changed_paths_snapshot(record)
            if changed:
                parts.append(f"changed=[{', '.join(changed)}]")
            if result := " ".join(str(record.get("result") or "").split())[:200]:
                parts.append(f'result="{result}"')
            lines.append(" ".join(parts))
        if len(pending) > 8:
            lines.append(f"- … 외 {len(pending) - 8}건 생략")
        return "\n".join(lines)

    @staticmethod
    def _format_persisted_digest(rows: list[dict]) -> str:
        """SQLite에서 복원한 워커 성과를 첫 턴 회수 노트로 변환한다."""
        if not rows:
            return ""
        lines = [
            "[janus runtime] Persisted worker outcomes from an earlier run "
            "(operational data, not user speech). Integrate them or state why "
            "they are discarded before starting new work:",
        ]
        for row in rows[:8]:
            parts = [
                f"- {row.get('worker_id')} "
                f"[{row.get('role')} · {row.get('status')}(persisted)]"
            ]
            if name := str(row.get("name") or "").strip():
                parts.append(f'name="{name}"')
            if changed := list(row.get("changed_paths") or []):
                parts.append(f"changed=[{', '.join(changed)}]")
            if result := " ".join(str(row.get("result") or "").split())[:200]:
                parts.append(f'result="{result}"')
            lines.append(" ".join(parts))
        return "\n".join(lines)

    def _quiesce_turn_workers(self, dispatch_id: str, wait_seconds: float = 2.0) -> list[dict]:
        """Prevent workers from outliving the parent turn that owns them."""
        active_statuses = {"queued", "running", "waiting_approval", "stopping"}
        with self.lock:
            active = [
                record for record in self.worker_records.values()
                if record.get("dispatch_id") == dispatch_id
                and record.get("status") in active_statuses
            ]
        if not active:
            return []
        snapshots = [self._worker_view(record) for record in active]
        for record in active:
            with self.lock:
                # 강제 종료 전 성과 스냅샷 — 다음 턴 회수 노트의 근거가 된다.
                record["quiesce"] = {
                    "status": record["status"],
                    "changed_paths": sorted(record.get("changed_paths") or []),
                    "result": str(record.get("result") or "")[:500],
                    "reason": "parent_turn_ended",
                }
            record["cancel"].set()
            self._set_worker_status(record, "stopping", recovery="parent_turn_ended")
        # 워커마다 같은 예산을 준다. 공유 deadline은 첫 워커가 다 쓰면 나머지가
        # 0초를 받아 사실상 대기 없이 버려졌다.
        for record in active:
            record["idle"].wait(max(0.0, wait_seconds))
        # cancel은 스텝 경계에서만 확인된다 — run_bash(최대 120초) 안의 워커는
        # 여기서 안 멈춘다. 버려진 사실을 이벤트에 남겨야 사후 추적이 된다.
        abandoned = [
            self._worker_view(record)["worker"] for record in active
            if not record["idle"].is_set()
        ]
        self._sink(ORCH_ID, "worker_turn_quiesced", {
            "workers": [item["worker"] for item in snapshots],
            "abandoned": abandoned,
            "reason": "parent_turn_ended_before_workers_settled",
        }, dispatch_id=dispatch_id)
        if self.turn_outcome is None or self.turn_outcome.get("outcome") == "completed":
            self.turn_outcome = {
                "outcome": "partial",
                "summary": "상위 에이전트가 워커 결과를 수집하기 전에 턴을 종료했습니다.",
                "evidence": [
                    f"{item['worker']}: {item['status']} -> stopping"
                    for item in snapshots
                ],
            }
        return snapshots

    # ── 턴 실행 ──

    def turn(self, text: str, *, dispatch_id: str | None = None) -> None:
        """블로킹 — asyncio.to_thread로 호출된다. ReAct 한 턴."""
        self.cancel.clear()
        self.cancelled_turn = False
        self.turn_failed = False
        self.turn_outcome = None
        persisted_note = ""
        if self.persisted_worker_outcomes:
            persisted_note = self._format_persisted_digest(
                self.persisted_worker_outcomes)
            delivered_ids = [
                str(item.get("id")) for item in self.persisted_worker_outcomes
                if item.get("id")
            ]
            self.persisted_worker_outcomes = []  # 첫 턴에 한 번만 소비한다
            # 메모리에서만 비우면 다음 WS 접속이 같은 행을 다시 읽어 온다 —
            # 새로고침할 때마다 이미 통합한 작업을 다시 통합하라고 받았다.
            if delivered_ids and self.on_outcomes_delivered is not None:
                try:
                    self.on_outcomes_delivered(delivered_ids)
                except Exception as error:
                    self._sink(ORCH_ID, "worker_outcome_delivery_mark_failed",
                               {"error": f"{type(error).__name__}: {error}"})
        pending_recovery = self._undelivered_terminal_workers()
        recovered_parts = [
            part for part in (
                persisted_note,
                self._format_recovery_digest(pending_recovery),
            ) if part
        ]
        recovered_note = "\n\n".join(recovered_parts)
        dispatch_id = self.telemetry.begin_turn(dispatch_id)
        self.current_dispatch_id = dispatch_id
        self.last_dispatch_id = dispatch_id
        self.current_user_text = text
        context = self.workspace_context.for_dispatch(dispatch_id)
        self.active_workspace_context = context
        self.dispatch_budget.begin_active()
        if recovered_note:
            # 런타임 운영 노트를 명시적 봉투로 세션에 주입한다. user kind 재사용은
            # UI와 메시지 조립 경로를 그대로 두기 위한 선택이다 — 봉투 자체가
            # "사용자 말이 아님"을 선언하므로 대화 이력의 정직성은 유지된다.
            self.session.append("user", content=recovered_note)
            self._sink(ORCH_ID, "worker_recovery_injected",
                       {"chars": len(recovered_note)})
            with self.lock:
                for record in pending_recovery:
                    record["recovery_notes"] = (
                        int(record.get("recovery_notes") or 0) + 1
                    )
        turn_tools = list(self.tools)
        task_text = text
        if is_read_only_request(text):
            turn_tools = [tool for tool in turn_tools if tool in T.READ_ONLY]
            # create_worker가 self.tools(축소 전)로 워커 도구를 계산하므로, 여기서
            # 남기지 않으면 read-only 턴에서 쓰기 워커를 스폰해 가드를 우회한다.
            self.turn_tools = list(turn_tools)
            removed = sorted(set(self.tools) - set(turn_tools))
            if removed:
                self._sink(ORCH_ID, "parent_tools_restricted", {
                    "mode": "read_only", "removed_tools": removed,
                })
                # 축소는 어휘 판정이라 오판할 수 있다. 조용히 도구만 빼면 모델이
                # 편집을 한 척하거나 이유 없이 실패한다 — 축소 사실을 모델에게
                # 알려 오판이 사용자에게 보고되는 실패로 바뀌게 한다.
                task_text = text + (
                    "\n\n[janus runtime] This turn provides read-only tools because "
                    "the request wording reads as investigation-only. If the request "
                    "actually requires modifying files, do not attempt or simulate "
                    "edits — reply that write tools were withheld for this turn and "
                    "ask the user to restate the request with the intended change."
                )
        if self.first_message is None:
            self.first_message = text
            self._open_span(ORCH_ID, label=self.spec.get("name"), parent_id=None,
                            input={"task": text})
        try:
            last, _ = agent_mod.run(
                client=self.client, model=self.model,
                system_prompt=self.spec.get("system_prompt") or "",  # session이 이미 보유
                task=task_text,
                tool_names=(
                    turn_tools
                    + (["create_worker"] + [tool["name"] for tool in self.worker_control_tools]
                       if self.worker_enabled else [])
                    + [tool["name"] for tool in self.skill_tools]
                    + [self.finish_turn["name"]]
                ),
                workspace_context=context,
                approve=self._approve_for(ORCH_ID, context),
                emit=lambda kind, **d: self._sink(ORCH_ID, kind, d),
                max_steps=self.max_steps,
                cancel=self.cancel,
                extra_tools=(
                    ([self.create_worker] + self.worker_control_tools
                     if self.worker_enabled else []) + self.skill_tools
                    + [self.finish_turn]
                    # 이름이 같으면 레지스트리를 덮어쓴다 — 부모의 write_file/
                    # edit_file도 워커와 같은 소유권 테이블을 지나게 한다.
                    + (self._parent_write_guards() if self.worker_enabled else [])
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
            # 성공 경로에만 두면 모델 서버 크래시로 턴이 죽었을 때 워커가 살아남아
            # 사용자가 diff를 보는 동안에도 workspace에 쓴다.
            try:
                self._quiesce_turn_workers(dispatch_id)
            except Exception as error:  # 원래 예외를 가리지 않는다
                self._sink(ORCH_ID, "worker_quiesce_failed",
                           {"error": f"{type(error).__name__}: {error}"},
                           dispatch_id=dispatch_id)
            self.turn_tools = None
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
