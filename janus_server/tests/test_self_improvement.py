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


def test_ordinary_korean_adverbs_do_not_become_permanent_rules():
    """"먼저 이 파일 좀 봐줘"가 프로젝트 영구 규칙이 되면 안 된다.

    완료된 턴마다 preamble에 실려 나가는 자리라, 평범한 부사 하나가 24,000자
    컨텍스트를 잠식했다.
    """
    task = {"id": "task_1"}
    events = [{
        "kind": "transcript", "session_id": "session_1",
        "payload": {"role": "user", "content": "먼저 이 파일 좀 봐줘"},
    }]

    candidates = extract_candidates(task=task, events=events, verification_runs=[])

    assert candidates == []


def test_prohibitions_are_recorded_as_avoidance_not_preference():
    """스키마의 avoidance는 생산 경로가 없어 성공에서만 배웠다."""
    task = {"id": "task_1"}
    events = [{
        "kind": "transcript", "session_id": "session_1",
        "payload": {"role": "user", "content": "마이그레이션 파일은 절대 수정하지 마"},
    }]

    candidates = extract_candidates(task=task, events=events, verification_runs=[])

    assert [item["kind"] for item in candidates] == ["avoidance"]
    assert candidates[0]["title"] == "하지 말아야 할 것"


def test_rescanning_the_same_evidence_does_not_inflate_confidence(tmp_path: Path):
    """완료된 턴마다 전체 이벤트를 재스캔한다 — 같은 근거는 관측이 아니다."""
    store = DomainStore(tmp_path / "janus.sqlite3")
    project = store.create_project(name="Janus", repo_path=str(tmp_path / "repo"))
    content = "앞으로 UI 수정 후에는 항상 앱을 재실행해줘"
    key = fingerprint("preference", content)

    def upsert(evidence: str) -> dict:
        return store.upsert_project_learning(
            project_id=project["id"], kind="preference", title="사용자 작업 방식",
            content=content, fingerprint=key, confidence=.72, evidence=evidence,
        )

    first = upsert("task:one session:s1")
    for _ in range(7):  # 같은 Task에서 턴이 일곱 번 더 완료됐다
        repeated = upsert("task:one session:s1")

    assert repeated["evidence_count"] == first["evidence_count"] == 1
    assert repeated["confidence"] == first["confidence"]

    # 진짜 새 관측은 여전히 신뢰도를 올린다.
    fresh = upsert("task:two session:s2")
    assert fresh["evidence_count"] == 2
    assert fresh["confidence"] > first["confidence"]


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
