from pathlib import Path

from janus_server.domain import DomainStore
from janus_server.self_improvement import extract_candidates, fingerprint


def test_extracts_verified_command_and_explicit_preference():
    task = {"id": "task_1"}
    events = [{
        "kind": "transcript", "session_id": "session_1",
        "payload": {"role": "user", "content": "앞으로 UI 수정 후에는 항상 앱을 재실행해줘"},
    }]
    runs = [{"id": "verify_1", "status": "passed", "command": "pnpm test"}]

    candidates = extract_candidates(task=task, events=events, verification_runs=runs)

    assert {item["kind"] for item in candidates} == {"verification", "preference"}
    assert any("pnpm test" in item["content"] for item in candidates)


def test_project_learning_deduplicates_and_can_pause(tmp_path: Path):
    store = DomainStore(tmp_path / "janus.sqlite3")
    project = store.create_project(name="Janus", repo_path=str(tmp_path / "repo"))
    content = "이 프로젝트의 검증에 `pnpm test`를 사용한다."
    key = fingerprint("verification", content)

    first = store.upsert_project_learning(
        project_id=project["id"], kind="verification", title="검증 명령",
        content=content, fingerprint=key, confidence=.9, evidence="task:one",
    )
    second = store.upsert_project_learning(
        project_id=project["id"], kind="verification", title="검증 명령",
        content=content, fingerprint=key, confidence=.9, evidence="task:two",
    )

    assert first["id"] == second["id"]
    assert second["evidence_count"] == 2
    assert second["confidence"] > first["confidence"]
    paused = store.set_project_learning_status(second["id"], "paused")
    assert paused["status"] == "paused"
    assert store.list_project_learnings(project["id"], active_only=True) == []
