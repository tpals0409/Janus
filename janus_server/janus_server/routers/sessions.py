"""Janus sessions 라우터 — shared.py에서 분리되었다."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from contextlib import suppress
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .. import adaptive, cli_runner, recovery, runtime, self_improvement, shared
from .. import domain as D
from .. import scheduler as scheduler_mod
from ..shared import (
    _agent_profile_json,
    _dispatch_json,
    _learning_json,
    _model_profile_json,
    _origin_allowed,
    _session_approval_key,
    _skill_json,
    _skill_summary,
    _token_valid,
    get_domain_store,
)
from ..workspace import WorkspaceContext

router = APIRouter()

def _task_runtime_spec(
    store: D.DomainStore, agent_profile_id: str, *, budget: dict | None = None,
    adaptive_decision: dict | None = None, profile_snapshot: dict | None = None,
) -> dict:
    profile = profile_snapshot or _agent_profile_json(store.get_agent_profile(agent_profile_id))
    if not profile:
        profile = _agent_profile_json(store.get_agent_profile(agent_profile_id))
    model = _model_profile_json(store.get_model_profile(profile["model_profile_id"]))
    decision = adaptive_decision or {}
    effective = decision.get("effective") or {}
    return {
        "name": profile["name"],
        "description": profile["description"],
        "model": model["model_key"],
        "provider": model["provider"],
        "system_prompt": profile["system_prompt"],
        "tools": profile["tools"],
        "approval": profile["approval"],
        "worker_policy": effective.get("worker_policy", profile["worker_policy"]),
        "worker_roles": effective.get("worker_roles", ["scout", "implementer", "verifier"]),
        "worker_role_sequence": effective.get("worker_role_sequence", []),
        "allow_autonomous_workers": bool(effective.get("allow_autonomous_workers", False)),
        "max_steps": profile["max_steps"],
        "budget": budget or profile["budget"],
        "context_policy": profile["context_policy"],
    }



def _record_completed_turn_learnings(store: D.DomainStore, task_id: str) -> list[dict]:
    task = store.get_task(task_id)
    sessions = store.list_sessions(task_id)
    events = [
        event for session in sessions
        for event in store.list_session_events(session["id"])
    ]
    candidates = self_improvement.extract_candidates(
        task=task, events=events,
        verification_runs=store.list_verification_runs(task_id),
    )
    return [
        _learning_json(store.upsert_project_learning(
            project_id=task["project_id"], fingerprint=self_improvement.fingerprint(
                candidate["kind"], candidate["content"]
            ), **candidate,
        ))
        for candidate in candidates
    ]



def _context_item(
    item_id: str, label: str, source: str, content: str, *, status: str = "included",
    detail: dict | None = None,
) -> dict:
    return {
        "id": item_id,
        "label": label,
        "source": source,
        "status": status,
        "content": content,
        "chars": len(content),
        "estimated_tokens": max(1, (len(content) + 3) // 4) if content else 0,
        "detail": detail or {},
    }



def _pending_review_feedback(store: D.DomainStore, task_id: str) -> list[dict]:
    """최신 판정이 '변경 요청'일 때만 미해결 코멘트를 피드백으로 취급한다."""
    decisions = store.list_review_decisions(task_id)
    if not decisions or decisions[-1]["decision"] != "request_changes":
        return []
    return [
        item for item in store.list_review_comments(task_id)
        if item["resolved_at"] is None
    ]


def _task_context_snapshot(
    spec: dict, dispatch: dict, workspace: dict, skills: list[dict],
    events: list[dict] | None = None, task: dict | None = None,
    learnings: list[dict] | None = None,
    review_feedback: list[dict] | None = None,
) -> dict:
    policy = D.normalize_context_policy(spec.get("context_policy"))
    items = [_context_item(
        "agent_prompt", "시스템 프롬프트", "AgentProfile",
        str(spec.get("system_prompt") or "You are an orchestrator."),
    )]
    preamble: list[str] = []
    objective = str(dispatch.get("objective_snapshot") or "")
    acceptance = str(dispatch.get("acceptance_snapshot") or "")
    workspace_root = str(workspace.get("root_path") or "")
    task_parts = (
        ("task_objective", "작업 목표", "Task", objective, "include_task_objective", "Task objective"),
        ("acceptance", "수용 검증", "Dispatch", acceptance, "include_acceptance", "Acceptance command"),
        ("workspace_root", "작업 공간", "Workspace", workspace_root, "include_workspace_root", "Workspace root"),
    )
    for item_id, label, source, content, policy_key, prompt_label in task_parts:
        included = bool(policy[policy_key]) and bool(content)
        items.append(_context_item(
            item_id, label, source, content,
            status="included" if included else "excluded",
            detail={"policy_key": policy_key},
        ))
        if included:
            preamble.append(f"{prompt_label}:\n{content}")

    workflow_stage = str((task or {}).get("workflow_stage") or "direct")
    workflow_prompt = ""
    if workflow_stage == "mockup":
        workflow_prompt = (
            "WORKFLOW STAGE: FRONTEND MOCKUP ONLY\n"
            "Create the smallest usable visual mockup for the requested feature using "
            "dummy or local fixture data. Reuse the existing UI patterns. Do not modify "
            "backend code, database schemas, APIs, model runtime, packaging, or infrastructure. "
            "Do not add speculative abstractions or dependencies. Once the mockup can be "
            "previewed, stop and ask the user to approve it before implementation."
        )
        mockup_feedback = str((task or {}).get("mockup_feedback") or "").strip()
        if mockup_feedback:
            workflow_prompt += (
                "\nThe user rejected the previous mockup. Revise only the mockup using this "
                f"feedback, then request review again:\n{mockup_feedback}"
            )
    elif workflow_stage == "implementation":
        workflow_prompt = (
            "WORKFLOW STAGE: APPROVED MOCKUP IMPLEMENTATION\n"
            "The user approved the frontend mockup. Treat its visible states and interactions "
            "as the scope. Derive only the minimal data/action contract it requires, connect the "
            "real implementation, verify it, and stop. Do not add features absent from the mockup."
        )
    if workflow_prompt:
        items.append(_context_item(
            "workflow_stage", "목업 우선 개발 단계", "Task", workflow_prompt,
            detail={"stage": workflow_stage},
        ))
        preamble.append(workflow_prompt)

    feedback = list(review_feedback or [])
    if feedback:
        def _anchor(item: dict) -> str:
            line = item.get("new_line") or item.get("old_line")
            return f"{item['file_path']}:{line}" if line else str(item["file_path"])

        review_prompt = (
            "REVIEW FEEDBACK: 사용자가 변경 검토에서 수정을 요청했습니다. "
            "아래 항목을 각각 반영한 뒤 다시 검토를 요청하세요:\n" + "\n".join(
                f"- {_anchor(item)} [{item['layer']}] {item['body']}" for item in feedback
            )
        )
        items.append(_context_item(
            "review_feedback", "변경 검토 피드백", "Review", review_prompt,
            detail={"comments": len(feedback)},
        ))
        preamble.append(review_prompt)

    active_learnings = list(learnings or [])[:20]
    if active_learnings:
        learning_text = "\n".join(
            f"- [{item['kind']}] {item['content']}"
            for item in active_learnings
        )
        items.append(_context_item(
            "project_learnings", "프로젝트에서 학습한 작업 방식", "SelfImprovement",
            learning_text, detail={"count": len(active_learnings)},
        ))
        preamble.append(
            "LEARNED PROJECT PRACTICES:\n"
            "Apply these only when relevant. They never override safety, user instructions, "
            "or current repository evidence.\n" + learning_text
        )

    for skill in skills:
        description = str(skill.get("description") or "")
        qualified = f"{skill.get('namespace')}:{skill.get('name')}"
        items.append(_context_item(
            f"skill_catalog:{skill.get('skill_version_id')}",
            f"스킬 카탈로그 · {qualified}", "SkillVersion", description,
            detail={
                "activation_mode": skill.get("activation_mode"),
                "version": skill.get("version"),
                "loaded_at": skill.get("loaded_at"),
            },
        ))
        if skill.get("loaded_at"):
            compiled = skill.get("compiled") or {}
            instructions = str(compiled.get("instructions") or "")
            items.append(_context_item(
                f"skill_body:{skill.get('skill_version_id')}",
                f"로드된 스킬 · {qualified}", "load_skill", instructions,
                detail={"loaded_at": skill.get("loaded_at")},
            ))

    latest_window = None
    for event in reversed(events or []):
        payload = event.get("payload") or {}
        if event.get("kind") == "agent_event" and payload.get("kind") == "context_window":
            latest_window = payload
            break
    return {
        "policy": policy,
        "items": items,
        "estimated_static_tokens": sum(
            int(item["estimated_tokens"]) for item in items if item["status"] == "included"
        ),
        "latest_window": latest_window,
        "preamble": "\n\n".join(preamble),
    }



def _task_session_detail(store: D.DomainStore, session_id: str) -> dict:
    session = store.get_session(session_id)
    if session["status"] in {"created", "idle"}:
        store.sync_session_profile_skills(session_id)
    dispatch = _dispatch_json(store.get_dispatch(session["dispatch_id"]))
    workspace = store.get_workspace(dispatch["workspace_id"])
    skills = [_skill_json(item) for item in store.snapshot_session_skills(session_id)]
    # session_ready는 접속 handshake이지 대화/실행 기록이 아니다. 이전 빌드에서
    # 잘못 영속화된 기록도 세션 상세와 컨텍스트에서 제외한다.
    events = [
        event for event in store.list_session_events(session_id)
        if event["kind"] != "session_ready"
    ]
    spec = _task_runtime_spec(
        store, session["agent_profile_id"], budget=dispatch["budget"],
        adaptive_decision=dispatch["adaptive_decision"],
        profile_snapshot=dispatch["agent_profile_snapshot"],
    )
    task = store.get_task(session["task_id"])
    learnings = store.list_project_learnings(task["project_id"], active_only=True, limit=20)
    context = _task_context_snapshot(
        spec, dispatch, workspace, skills, events, task, learnings,
        review_feedback=_pending_review_feedback(store, session["task_id"]),
    )
    context.pop("preamble", None)
    return {
        **session,
        "dispatch": dispatch,
        "workspace_id": workspace["id"],
        "workspace_root": workspace["root_path"],
        "skills": [_skill_summary(item) for item in skills],
        "approval_scopes": store.list_workspace_approval_scopes(workspace["id"]),
        "context": context,
        "events": events,
    }



def _cancel_live_task_runtimes(task_id: str, *, except_session_id: str | None = None) -> None:
    with shared._TASK_RUNTIMES_LOCK:
        live = [
            orch for session_id, orch in shared._TASK_RUNTIMES.items()
            if session_id != except_session_id and orch.workspace_context.task_id == task_id
        ]
    for orch in live:
        orch.cancel_all()



@router.post("/tasks/{task_id}/sessions")
def start_task_session(task_id: str, body: dict):
    store = get_domain_store()
    workspace = store.get_task_workspace(task_id)
    if workspace is None:
        raise D.Conflict("Task Workspace를 먼저 준비하세요")
    task = store.get_task(task_id)
    project = store.get_project(task["project_id"])
    profile_id = str(
        body.get("agent_profile_id")
        or project.get("default_agent_profile_id")
        or "agent_default"
    )
    try:
        profile = _agent_profile_json(store.get_agent_profile(profile_id))
        previous = store.latest_dispatch(task_id)
        decision = adaptive.decide(
            task=task,
            base_profile=profile,
            scheduler_snapshot=scheduler_mod.default_scheduler().snapshot(),
            previous_dispatch=previous,
            verification_runs=store.list_verification_runs(task_id),
        )
        queue_override = {
            key: int(body[source])
            for key, source in (("priority", "priority"), ("timeout_ms", "queue_timeout_ms"))
            if source in body
        }
        effective_budget = decision["effective"]["budget"]
        if queue_override:
            effective_budget = D.merge_budget(
                effective_budget, {"queue": queue_override}
            )
            decision["effective"]["budget"] = effective_budget
        execution = store.create_execution(
            task_id=task_id,
            workspace_id=workspace["id"],
            agent_profile_id=profile_id,
            budget_override=effective_budget,
            adaptive_decision=decision,
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    _cancel_live_task_runtimes(task_id)
    return _task_session_detail(store, execution["session"]["id"])



@router.get("/tasks/{task_id}/sessions")
def list_task_sessions(task_id: str):
    store = get_domain_store()
    store.get_task(task_id)
    return [
        _task_session_detail(store, item["id"])
        for item in store.list_sessions(task_id)
    ]



@router.get("/tasks/{task_id}/sessions/latest")
def latest_task_session(task_id: str):
    store = get_domain_store()
    store.get_task(task_id)
    sessions = store.list_sessions(task_id)
    if not sessions:
        raise D.NotFound(f"Task의 AgentSession이 없습니다: {task_id}")
    return _task_session_detail(store, sessions[0]["id"])



@router.get("/sessions/{session_id}")
def get_agent_session(session_id: str):
    return _task_session_detail(get_domain_store(), session_id)



@router.post("/sessions/{session_id}/resume")
def resume_agent_session(session_id: str):
    store = get_domain_store()
    detail = _task_session_detail(store, session_id)
    latest = store.latest_dispatch(detail["task_id"])
    if latest is None or latest["id"] != detail["dispatch_id"]:
        raise D.StaleDispatch(f"오래된 Dispatch의 Session입니다: {detail['dispatch_id']}")
    if detail["status"] not in {"created", "idle"}:
        raise D.Conflict(f"resume할 수 없는 AgentSession 상태: {detail['status']}")
    if detail["dispatch"]["status"] not in {"queued", "needs_you"}:
        raise D.Conflict(f"resume할 수 없는 Dispatch 상태: {detail['dispatch']['status']}")
    return detail



@router.post("/sessions/{session_id}/stop")
def stop_agent_session(session_id: str):
    session = get_domain_store().get_session(session_id)
    _cancel_live_task_runtimes(session["task_id"])
    return _task_session_detail(
        get_domain_store(),
        get_domain_store().stop_execution(session_id)["id"],
    )



@router.delete("/sessions/{session_id}/approvals/{scope}")
def revoke_agent_session_approval(session_id: str, scope: str, workspace_id: str):
    store = get_domain_store()
    session = store.get_session(session_id)
    dispatch = store.get_dispatch(session["dispatch_id"])
    if dispatch["workspace_id"] != workspace_id:
        raise D.Conflict("AgentSession과 작업 공간이 일치하지 않습니다")
    store.revoke_workspace_approval_scope(workspace_id, scope)
    return {"session_id": session_id, "workspace_id": workspace_id, "scope": scope}



@router.websocket("/tasks/{task_id}/sessions/{session_id}")
async def run_task_session(ws: WebSocket, task_id: str, session_id: str):
    """Stream one persisted AgentSession inside its Task Workspace.

    The Dispatch row is the ownership fence. Every runtime event is appended to
    SQLite only while that Dispatch is the latest non-terminal attempt for the Task.
    Reconnecting reconstructs the model transcript from persisted `transcript` events.
    """
    origin = ws.headers.get("origin")
    protocols = {
        p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()
    }
    if not _origin_allowed(origin) or "janus" not in protocols or not any(
        _token_valid(p) for p in protocols if p != "janus"
    ):
        await ws.close(code=1008)
        return

    # 인증 게이트 통과 즉시 핸드셰이크를 완료한다 — 도메인 검증 실패는 accept 뒤
    # 코드 1008로 닫는다. 클라이언트는 인증 실패와 세션 오류를 구분할 수 있다.
    await ws.accept(subprotocol="janus")

    store = get_domain_store()
    try:
        session = store.get_session(session_id)
        if session["task_id"] != task_id:
            raise D.Conflict("AgentSession이 다른 Task에 속합니다")
        dispatch = _dispatch_json(store.get_dispatch(session["dispatch_id"]))
        latest = store.latest_dispatch(task_id)
        if latest is None or latest["id"] != dispatch["id"]:
            raise D.StaleDispatch(f"오래된 Dispatch의 Session입니다: {dispatch['id']}")
        if session["status"] not in {"created", "idle"}:
            raise D.Conflict(f"연결할 수 없는 AgentSession 상태: {session['status']}")
        store.sync_session_profile_skills(session_id)
        workspace = store.get_workspace(dispatch["workspace_id"])
        if workspace["state"] != "ready" or not workspace["root_path"]:
            raise D.Conflict("ready Workspace가 있어야 Session을 실행할 수 있습니다")
        spec = _task_runtime_spec(
            store, session["agent_profile_id"], budget=dispatch["budget"],
            adaptive_decision=dispatch["adaptive_decision"],
            profile_snapshot=dispatch["agent_profile_snapshot"],
        )
        spec["skills"] = [
            _skill_json(item) for item in store.snapshot_session_skills(session_id)
        ]
        active_learnings = store.list_project_learnings(
            store.get_task(task_id)["project_id"], active_only=True, limit=20,
        )
        context_snapshot = _task_context_snapshot(
            spec, dispatch, workspace, spec["skills"], store.list_session_events(session_id),
            store.get_task(task_id),
            active_learnings,
            review_feedback=_pending_review_feedback(store, task_id),
        )
        spec["context_preamble"] = context_snapshot["preamble"]
        store.mark_project_learnings_applied([item["id"] for item in active_learnings])
        # 이전 실행(크래시 포함)에서 남긴 워커 성과 — 새 세션의 첫 턴에 회수 노트로 주입된다.
        persisted_worker_outcomes = store.list_worker_outcomes(task_id, limit=8)
    except D.DomainError:
        await ws.close(code=1008)
        return

    loop = asyncio.get_running_loop()
    pending_lock = shared._PENDING_APPROVALS_LOCK
    with pending_lock:
        pending = shared._PENDING_APPROVALS.setdefault(session_id, {})
    # 승인 기억은 작업 단위 — 이전 시도(다른 세션)에서 허용한 것도 유효하다.
    approved_scopes: set[tuple[str, str]] = {
        (str(item["scope"]), str(item["workspace_id"]))
        for item in store.list_workspace_approval_scopes(workspace["id"])
    }
    turn_task: asyncio.Task | None = None
    queued_texts: list[str] = []
    orch: runtime.Orchestration | None = None
    stop_requested = False
    stale_notified = threading.Event()
    transcript_events = [
        item["payload"] for item in store.list_session_events(session_id)
        if item["kind"] == "transcript"
    ]
    transcript_count = len(transcript_events)
    context = WorkspaceContext(
        root=Path(workspace["root_path"]),
        task_id=task_id,
        workspace_id=workspace["id"],
        dispatch_id=dispatch["id"],
    )

    async def _safe_send(payload: dict) -> None:
        try:
            await ws.send_json(payload)
        except Exception:
            pass

    def _direct_send(payload: dict) -> None:
        with suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(_safe_send(payload), loop)

    def _payload_with_ids(event: dict) -> dict:
        return {
            **event,
            "task_id": task_id,
            "workspace_id": workspace["id"],
            "dispatch_id": dispatch["id"],
            "session_id": session_id,
        }

    def send(event: dict) -> None:
        """Persist before delivery and reject any event that lost Dispatch ownership."""
        payload = _payload_with_ids(event)
        try:
            store.append_session_event(
                session_id,
                kind=str(payload.get("type") or "runtime"),
                payload=payload,
                task_id=task_id,
                dispatch_id=dispatch["id"],
                workspace_id=workspace["id"],
                require_latest=True,
            )
        except D.StaleDispatch:
            if not stale_notified.is_set():
                stale_notified.set()
                if orch is not None:
                    orch.cancel_all()
                _direct_send(_payload_with_ids({
                    "type": "stale_dispatch",
                    "error": "더 최신 Dispatch가 이 Task의 실행 권한을 소유합니다",
                }))
            return
        _direct_send(payload)

    def persist_final(event: dict) -> dict | None:
        """Persist a terminal event only if this is still the latest Dispatch."""
        payload = _payload_with_ids(event)
        try:
            store.append_session_event(
                session_id,
                kind=str(payload.get("type") or "runtime"),
                payload=payload,
                task_id=task_id,
                dispatch_id=dispatch["id"],
                workspace_id=workspace["id"],
                require_latest=True,
                require_active=False,
            )
        except D.StaleDispatch:
            return None
        return payload

    async def send_final(event: dict) -> None:
        """Flush earlier worker sends, then deliver the persisted terminal event."""
        payload = persist_final(event)
        if payload is None:
            return
        await asyncio.sleep(0)
        await _safe_send(payload)

    def approver(
        node_id: str, tool: str, args: dict, approval_context: WorkspaceContext
    ) -> bool:
        scope_key = _session_approval_key(tool, approval_context)
        with pending_lock:
            if scope_key is not None and scope_key in approved_scopes:
                return True
        req_id = uuid.uuid4().hex[:12]
        event = threading.Event()
        request = {
            "type": "approval_request",
            "id": req_id,
            "node_id": node_id,
            "tool": tool,
            "args": args,
            "deadline_epoch_ms": int((time.time() + shared.APPROVAL_TIMEOUT) * 1000),
            "rememberable": scope_key is not None,
            "approval_scope": scope_key[0] if scope_key is not None else None,
            **approval_context.identifiers(),
        }
        with pending_lock:
            pending[req_id] = [event, False, scope_key, request]
        send(request)
        if not event.wait(timeout=shared.APPROVAL_TIMEOUT):
            with pending_lock:
                pending.pop(req_id, None)
                if not pending:
                    shared._PENDING_APPROVALS.pop(session_id, None)
            return False
        with pending_lock:
            slot = pending.pop(req_id, None)
            if not pending:
                shared._PENDING_APPROVALS.pop(session_id, None)
        return bool(slot and slot[1])

    def skill_loaded(skill_version_id: str, reason: str, prompt_tokens: int) -> None:
        store.mark_session_skill_loaded(
            session_id, skill_version_id, reason=reason, prompt_tokens=prompt_tokens,
        )

    def persist_worker_outcome(view: dict) -> None:
        store.record_worker_outcome(view)

    def ensure_orchestration() -> runtime.Orchestration:
        nonlocal orch
        if orch is None:
            if cli_runner.is_cli_provider(spec.get("provider")):
                # 구독형 CLI 실행기 — Janus 오케스트레이터 대신 CLI가 턴을 돈다.
                orch = cli_runner.CliOrchestration(
                    spec, send=send, workspace_context=context,
                    task_id=task_id, session_id=session_id,
                )
                orch.restore_transcript(transcript_events)
            else:
                orch = runtime.Orchestration(
                    spec,
                    send=send,
                    approver=approver,
                    workspace_context=context,
                    task_id=task_id,
                    session_id=session_id,
                    budget=spec["budget"],
                    budget_usage=dispatch["usage"],
                    on_skill_loaded=skill_loaded,
                    on_worker_outcome=persist_worker_outcome,
                    persisted_worker_outcomes=persisted_worker_outcomes,
                )
                orch.session.events = [dict(item) for item in transcript_events]
            with shared._TASK_RUNTIMES_LOCK:
                existing = shared._TASK_RUNTIMES.get(session_id)
                if existing is not None and existing is not orch:
                    raise D.Conflict("AgentSession이 이미 다른 연결에서 실행 중입니다")
                shared._TASK_RUNTIMES[session_id] = orch
        return orch

    def persist_transcript(current: runtime.Orchestration) -> None:
        nonlocal transcript_count
        new_events = current.session.events[transcript_count:]
        for event in new_events:
            store.append_session_event(
                session_id,
                kind="transcript",
                payload=dict(event),
                task_id=task_id,
                dispatch_id=dispatch["id"],
                workspace_id=workspace["id"],
                require_latest=True,
            )
        transcript_count += len(new_events)

    async def do_turn(text: str) -> None:
        nonlocal stop_requested
        current: runtime.Orchestration | None = None
        failure: str | None = None
        activated = False
        try:
            store.activate_session_turn(session_id)
            activated = True
            current = ensure_orchestration()
            send({"type": "run_start", "agent_profile_id": session["agent_profile_id"]})
            runtime_done = threading.Event()
            runtime_errors: list[BaseException] = []

            def run_persisted_turn() -> None:
                try:
                    current.turn(text, dispatch_id=dispatch["id"])
                except BaseException as error:
                    runtime_errors.append(error)
                finally:
                    runtime_done.set()

            threading.Thread(target=run_persisted_turn, daemon=True).start()
            while not runtime_done.is_set():
                try:
                    await asyncio.sleep(0.02)
                except asyncio.CancelledError:
                    # The renderer socket owns delivery only. ASGI cancellation
                    # of its connection scope must not settle the persistent
                    # Dispatch before the model thread has finished.
                    task = asyncio.current_task()
                    if task is not None:
                        task.uncancel()
            if runtime_errors:
                raise runtime_errors[0]
            persist_transcript(current)
            if current.budget_exhausted_reason:
                failure = f"budget exhausted: {current.budget_exhausted_reason}"
                send({"type": "run_error", "error": failure})
        except D.StaleDispatch:
            stale_notified.set()
            if current is not None:
                current.cancel_all()
            _direct_send(_payload_with_ids({
                "type": "stale_dispatch",
                "error": "더 최신 Dispatch가 이 Task의 실행 권한을 소유합니다",
            }))
            return
        except Exception as error:
            failure = f"{type(error).__name__}: {error}"
            recovery_hint = recovery.classify_failure(error)
            if current is not None:
                current.turn_failed = True
            if activated:
                send({"type": "run_error", "error": failure, "recovery": recovery_hint})
            else:
                _direct_send(_payload_with_ids({
                    "type": "run_error", "error": failure, "recovery": recovery_hint,
                }))
        finally:
            if stale_notified.is_set() or not activated:
                return  # noqa: B012 — 지연 세션은 transcript를 건드리지 않고 조용히 종료
            try:
                if current is not None and len(current.session.events) > transcript_count:
                    persist_transcript(current)
                if current is not None:
                    budget_snapshot = current.snapshot_budget()
                    store.record_dispatch_budget(
                        dispatch["id"],
                        usage=budget_snapshot["usage"],
                        exhausted_reason=budget_snapshot["exhausted_reason"],
                    )
                persisted = store.get_session(session_id)
                if stop_requested or persisted["status"] == "stopped":
                    if persisted["status"] != "stopped":
                        store.stop_execution(session_id)
                    await send_final({"type": "session_stopped"})
                else:
                    turn_outcome = (
                        current.snapshot_turn_outcome() if current is not None
                        else {"outcome": "partial", "summary": "", "evidence": []}
                    )
                    store.settle_session_turn(
                        session_id, failed=failure is not None, error=failure,
                        outcome=str(turn_outcome["outcome"]),
                    )
                    if failure is None and turn_outcome["outcome"] == "completed":
                        learned = _record_completed_turn_learnings(store, task_id)
                        if learned:
                            await send_final({
                                "type": "learning_updated",
                                "count": len(learned),
                                "items": learned[:5],
                            })
                await send_final({
                    "type": "turn_end",
                    "cancelled": bool(current and current.cancelled_turn),
                    "session_status": store.get_session(session_id)["status"],
                    "outcome": (
                        current.snapshot_turn_outcome() if current is not None else None
                    ),
                })
                if stop_requested:
                    await asyncio.sleep(0)
                    await ws.close(code=1000)
            except D.StaleDispatch:
                stale_notified.set()
                _direct_send(_payload_with_ids({
                    "type": "stale_dispatch",
                    "error": "더 최신 Dispatch가 이 Task의 실행 권한을 소유합니다",
                }))

    def _start_turn(text: str) -> None:
        nonlocal turn_task
        turn_task = asyncio.create_task(do_turn(text))
        turn_task.add_done_callback(_drain_queued_turns)

    def _drain_queued_turns(_task: asyncio.Task) -> None:
        if queued_texts and not stop_requested and not stale_notified.is_set():
            _start_turn(queued_texts.pop(0))

    await _safe_send(_payload_with_ids({
        "type": "session_ready",
        "agent_profile_id": session["agent_profile_id"],
        "resumed_events": transcript_count,
        "session_status": session["status"],
    }))

    # 재연결한 클라이언트에게 아직 답이 없는 승인 요청을 다시 보여준다.
    with pending_lock:
        outstanding = [slot[3] for slot in pending.values() if len(slot) > 3]
    for request in outstanding:
        await _safe_send(request)

    try:
        while True:
            message = await ws.receive_json()
            kind = message.get("type")
            if kind in {"message", "resume"}:
                text = str(message.get("text") or "").strip()
                if not text or stop_requested:
                    continue
                if turn_task and not turn_task.done():
                    # 실행 중 지시는 버리지 않는다 — 이 턴이 끝나면 순서대로 실행한다.
                    queued_texts.append(text)
                    await _safe_send(_payload_with_ids({
                        "type": "turn_queued", "text": text,
                        "position": len(queued_texts),
                    }))
                    continue
                _start_turn(text)
            elif kind == "approval_response":
                granted_scope = None
                with pending_lock:
                    slot = pending.get(message.get("id"))
                    approved = bool(message.get("approved"))
                    remember = approved and message.get("scope") == "session_workspace"
                    if slot and remember and slot[2] is not None:
                        try:
                            store.grant_session_approval_scope(
                                session_id, slot[2][1], slot[2][0],
                            )
                        except D.DomainError:
                            approved = False
                        if approved:
                            approved_scopes.add(slot[2])
                            granted_scope = slot[2]
                            matching = [
                                item for item in pending.values() if item[2] == slot[2]
                            ]
                        else:
                            matching = [slot]
                    else:
                        matching = [slot] if slot else []
                    for item in matching:
                        item[1] = approved
                        item[0].set()
                if granted_scope is not None:
                    await _safe_send(_payload_with_ids({
                        "type": "approval_scope_granted",
                        "scope": granted_scope[0],
                        "workspace_id": granted_scope[1],
                    }))
            elif kind == "approval_scope_revoke":
                scope = str(message.get("scope") or "")
                scope_key = (scope, workspace["id"])
                store.revoke_workspace_approval_scope(workspace["id"], scope)
                approved_scopes.discard(scope_key)
                await _safe_send(_payload_with_ids({
                    "type": "approval_scope_revoked",
                    "scope": scope,
                    "workspace_id": workspace["id"],
                }))
            elif kind == "cancel":
                if orch is not None:
                    orch.cancel_all()
                with pending_lock:
                    slots = list(pending.values())
                for slot in slots:
                    slot[1] = False
                    slot[0].set()
            elif kind == "stop_worker" and orch is not None:
                orch.stop_worker(str(message.get("node_id") or ""))
            elif kind == "stop":
                stop_requested = True
                queued_texts.clear()
                if orch is not None:
                    orch.cancel_all()
                with pending_lock:
                    slots = list(pending.values())
                for slot in slots:
                    slot[1] = False
                    slot[0].set()
                if turn_task is None or turn_task.done():
                    store.stop_execution(session_id)
                    await send_final({"type": "session_stopped"})
                    break
    except WebSocketDisconnect:
        pass
    except Exception as error:
        await _safe_send(_payload_with_ids({
            "type": "run_error", "error": f"{type(error).__name__}: {error}"
        }))
    finally:
        # A persisted Task turn belongs to its Dispatch, not to one renderer
        # socket. Window reloads, navigation, and transient WebSocket reconnects
        # must not throw away model work that is already running. Events keep
        # being persisted by send(); only explicit cancel/stop or server shutdown
        # may cancel the orchestration.
        with pending_lock:
            slots = list(pending.values())
        for slot in slots:
            slot[1] = False
            slot[0].set()
        if turn_task and not turn_task.done():
            with suppress(asyncio.CancelledError):
                await asyncio.shield(turn_task)
        with shared._TASK_RUNTIMES_LOCK:
            if orch is not None and shared._TASK_RUNTIMES.get(session_id) is orch:
                shared._TASK_RUNTIMES.pop(session_id, None)
