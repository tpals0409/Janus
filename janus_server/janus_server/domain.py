"""P1 ADE 영속 도메인 모델과 SQLite 저장소.

기존 agent YAML/run JSON은 P1 전환 중 호환 입력으로 남겨두고, 새 Project/Task/
Workspace/Dispatch/AgentSession/Profile 상태의 단일 진실 원천은 이 DB다.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .budget import empty_usage, merge_budget, normalize_budget

CURRENT_SCHEMA_VERSION = 19

DEFAULT_CONTEXT_POLICY = {
    "max_chars": 24_000,
    "recent_blocks": 8,
    "summary_max_chars": 4_000,
    "include_task_objective": True,
    "include_acceptance": True,
    "include_workspace_root": True,
}

TASK_STATUSES = frozenset({"todo", "preparing", "working", "needs_you", "review", "failed"})
TASK_TRANSITIONS = {
    "todo": {"preparing", "working"},
    "preparing": {"todo", "working", "failed"},
    "working": {"todo", "needs_you", "review", "failed"},
    "needs_you": {"todo", "working", "review", "failed"},
    "review": {"working", "todo"},
    "failed": {"todo", "preparing", "working"},
}
WORKSPACE_STATES = frozenset({"preparing", "ready", "failed", "archived"})
WORKSPACE_TRANSITIONS = {
    "preparing": {"ready", "failed", "archived"},
    "ready": {"failed", "archived"},
    "failed": {"preparing", "archived"},
    "archived": {"preparing"},
}
DISPATCH_STATUSES = frozenset({"queued", "running", "needs_you", "completed", "failed", "cancelled"})
DISPATCH_TRANSITIONS = {
    "queued": {"running", "cancelled", "failed"},
    "running": {"needs_you", "completed", "failed", "cancelled"},
    "needs_you": {"running", "failed", "cancelled"},
    "completed": set(),
    "failed": set(),
    "cancelled": set(),
}
SESSION_STATUSES = frozenset({"created", "running", "idle", "stopped", "failed"})
SESSION_TRANSITIONS = {
    "created": {"running", "stopped", "failed"},
    "running": {"idle", "stopped", "failed"},
    "idle": {"running", "stopped", "failed"},
    "stopped": set(),
    "failed": set(),
}


class DomainError(RuntimeError):
    pass


class NotFound(DomainError):
    pass


class Conflict(DomainError):
    pass


class StaleDispatch(Conflict):
    """A newer attempt owns the Task, so this Dispatch may not mutate it."""


class InvalidTransition(DomainError):
    pass


class MigrationError(DomainError):
    pass


class ClosingConnection(sqlite3.Connection):
    """`with store._connect()` read 경로도 file descriptor를 즉시 닫는다."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _versioned_skill_metadata(item: dict) -> dict:
    """Use metadata captured in the pinned version, not mutable skill-head fields."""
    value = dict(item)
    try:
        compiled = json.loads(str(value.get("compiled_json") or "{}"))
    except (json.JSONDecodeError, TypeError, ValueError):
        compiled = {}
    if isinstance(compiled, dict):
        value["name"] = str(compiled.get("name") or value.get("name") or "")
        value["description"] = str(
            compiled.get("description") or value.get("description") or ""
        )
    return value


def normalize_context_policy(value: dict | None) -> dict:
    policy = {**DEFAULT_CONTEXT_POLICY, **(value or {})}
    try:
        policy["max_chars"] = int(policy["max_chars"])
        policy["recent_blocks"] = int(policy["recent_blocks"])
        policy["summary_max_chars"] = int(policy["summary_max_chars"])
    except (TypeError, ValueError) as error:
        raise Conflict("컨텍스트 용량 정책은 정수여야 합니다") from error
    if not 8_000 <= policy["max_chars"] <= 200_000:
        raise Conflict("max_chars는 8,000~200,000 범위여야 합니다")
    if not 1 <= policy["recent_blocks"] <= 64:
        raise Conflict("recent_blocks는 1~64 범위여야 합니다")
    if not 500 <= policy["summary_max_chars"] <= 16_000:
        raise Conflict("summary_max_chars는 500~16,000 범위여야 합니다")
    if policy["summary_max_chars"] >= policy["max_chars"]:
        raise Conflict("summary_max_chars는 max_chars보다 작아야 합니다")
    for key in ("include_task_objective", "include_acceptance", "include_workspace_root"):
        if not isinstance(policy[key], bool):
            raise Conflict(f"{key}는 boolean이어야 합니다")
    return policy


def agent_profile_snapshot(profile: dict) -> dict:
    return {
        "id": profile["id"],
        "name": profile["name"],
        "description": profile["description"],
        "system_prompt": profile["system_prompt"],
        "tools": json.loads(profile["tools_json"]),
        "approval": profile["approval"],
        "worker_policy": profile["worker_policy"],
        "max_steps": int(profile["max_steps"]),
        "model_profile_id": profile["model_profile_id"],
        "budget": json.loads(profile["budget_json"]),
        "context_policy": normalize_context_policy(json.loads(
            profile.get("context_policy_json") or "{}"
        )),
    }


MIGRATION_1 = """
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    repo_path TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE model_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider TEXT NOT NULL CHECK(provider = 'local'),
    model_key TEXT NOT NULL,
    quantization TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(provider, model_key, quantization)
);

CREATE TABLE agent_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    system_prompt TEXT NOT NULL,
    tools_json TEXT NOT NULL DEFAULT '[]',
    approval TEXT NOT NULL CHECK(approval IN ('auto', 'ask')),
    worker_policy TEXT NOT NULL CHECK(worker_policy IN ('none', 'fixed_one', 'autonomous')),
    max_steps INTEGER NOT NULL CHECK(max_steps BETWEEN 1 AND 100),
    model_profile_id TEXT NOT NULL REFERENCES model_profiles(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    objective TEXT NOT NULL CHECK(length(trim(objective)) > 0),
    acceptance_command TEXT NOT NULL CHECK(length(trim(acceptance_command)) > 0),
    base_ref TEXT NOT NULL CHECK(length(trim(base_ref)) > 0),
    status TEXT NOT NULL CHECK(status IN ('todo','preparing','working','needs_you','review','failed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE workspaces (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    repo_path TEXT NOT NULL,
    root_path TEXT,
    base_ref TEXT NOT NULL,
    branch_name TEXT,
    state TEXT NOT NULL CHECK(state IN ('preparing','ready','failed','archived')),
    error TEXT,
    owned INTEGER NOT NULL DEFAULT 1 CHECK(owned IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE dispatches (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    agent_profile_id TEXT NOT NULL REFERENCES agent_profiles(id) ON DELETE RESTRICT,
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    status TEXT NOT NULL CHECK(status IN ('queued','running','needs_you','completed','failed','cancelled')),
    objective_snapshot TEXT NOT NULL,
    acceptance_snapshot TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    UNIQUE(task_id, attempt)
);

CREATE TABLE agent_sessions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    dispatch_id TEXT NOT NULL REFERENCES dispatches(id) ON DELETE CASCADE,
    agent_profile_id TEXT NOT NULL REFERENCES agent_profiles(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK(status IN ('created','running','idle','stopped','failed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    stopped_at TEXT,
    error TEXT
);

CREATE TABLE session_events (
    session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    task_id TEXT NOT NULL,
    dispatch_id TEXT NOT NULL,
    workspace_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(session_id, seq)
);

CREATE INDEX idx_tasks_project_status ON tasks(project_id, status);
CREATE INDEX idx_dispatches_task_status ON dispatches(task_id, status);
CREATE INDEX idx_sessions_dispatch ON agent_sessions(dispatch_id);
CREATE INDEX idx_session_events_dispatch ON session_events(dispatch_id, seq);
"""

MIGRATION_2 = """
ALTER TABLE workspaces ADD COLUMN progress TEXT NOT NULL DEFAULT 'queued';
"""

MIGRATION_3 = """
ALTER TABLE agent_profiles ADD COLUMN budget_json TEXT NOT NULL DEFAULT
'{"dispatch":{"token_limit":32768,"time_limit_ms":900000,"step_limit":30},"worker":{"token_limit":8192,"time_limit_ms":300000,"step_limit":8},"workers":{"total_limit":4,"concurrent_limit":2},"queue":{"timeout_ms":300000,"priority":0}}';
ALTER TABLE dispatches ADD COLUMN budget_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE dispatches ADD COLUMN usage_json TEXT NOT NULL DEFAULT
'{"prompt_tokens":0,"completion_tokens":0,"steps":0,"active_time_ms":0.0,"workers_started":0,"peak_concurrent_workers":0}';
ALTER TABLE dispatches ADD COLUMN budget_exhausted_reason TEXT;
"""

MIGRATION_4 = """
ALTER TABLE projects ADD COLUMN verification_commands_json TEXT NOT NULL DEFAULT '[]';

CREATE TABLE verification_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    dispatch_id TEXT REFERENCES dispatches(id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK(kind IN ('acceptance','test','lint','typecheck','custom')),
    command TEXT NOT NULL CHECK(length(trim(command)) > 0),
    trigger TEXT NOT NULL CHECK(trigger IN ('manual','agent')),
    agent_claim TEXT CHECK(agent_claim IN ('passed','failed','unknown')),
    status TEXT NOT NULL CHECK(status IN ('queued','running','passed','failed','error','cancelled')),
    head_commit TEXT NOT NULL,
    exit_code INTEGER,
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    duration_ms REAL,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT
);
CREATE INDEX idx_verification_runs_task_created
ON verification_runs(task_id, created_at DESC);
"""

MIGRATION_5 = """
ALTER TABLE verification_runs ADD COLUMN revision TEXT NOT NULL DEFAULT '';

CREATE TABLE review_comments (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    revision TEXT NOT NULL,
    layer TEXT NOT NULL CHECK(layer IN ('committed','staged','unstaged','untracked')),
    file_path TEXT NOT NULL,
    old_line INTEGER,
    new_line INTEGER,
    hunk_header TEXT,
    body TEXT NOT NULL CHECK(length(trim(body)) > 0),
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX idx_review_comments_task ON review_comments(task_id, created_at);

CREATE TABLE review_decisions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    revision TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('accept','request_changes','discard')),
    comment_ids_json TEXT NOT NULL DEFAULT '[]',
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX idx_review_decisions_task ON review_decisions(task_id, created_at);
"""

MIGRATION_6 = """
CREATE TABLE task_shipments (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK(action IN ('commit','push')),
    commit_sha TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    remote TEXT,
    status TEXT NOT NULL CHECK(status IN ('completed','failed')),
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_task_shipments_task ON task_shipments(task_id, created_at);
"""

MIGRATION_7 = """
CREATE TABLE evaluation_experiments (
    id TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK(role IN ('baseline','candidate')),
    label TEXT NOT NULL CHECK(length(trim(label)) > 0),
    source TEXT NOT NULL CHECK(source IN ('import','runner')),
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled')),
    agent_profile_id TEXT REFERENCES agent_profiles(id) ON DELETE SET NULL,
    profile_snapshot_json TEXT NOT NULL DEFAULT '{}',
    config_json TEXT NOT NULL DEFAULT '{}',
    conditions_json TEXT NOT NULL DEFAULT '{}',
    report_json TEXT,
    result_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT
);

CREATE TABLE evaluation_comparisons (
    id TEXT PRIMARY KEY,
    baseline_experiment_id TEXT NOT NULL REFERENCES evaluation_experiments(id) ON DELETE CASCADE,
    candidate_experiment_id TEXT NOT NULL REFERENCES evaluation_experiments(id) ON DELETE CASCADE,
    thresholds_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(baseline_experiment_id <> candidate_experiment_id)
);
CREATE INDEX idx_evaluation_experiments_created ON evaluation_experiments(created_at DESC);
CREATE INDEX idx_evaluation_comparisons_created ON evaluation_comparisons(created_at DESC);
"""

MIGRATION_8 = """
ALTER TABLE dispatches ADD COLUMN adaptive_decision_json TEXT NOT NULL DEFAULT '{}';
"""

MIGRATION_9 = """
ALTER TABLE projects ADD COLUMN default_agent_profile_id TEXT REFERENCES agent_profiles(id) ON DELETE SET NULL;
ALTER TABLE projects ADD COLUMN promoted_comparison_id TEXT REFERENCES evaluation_comparisons(id) ON DELETE SET NULL;
ALTER TABLE projects ADD COLUMN profile_promoted_at TEXT;
"""

MIGRATION_10 = """
CREATE TABLE task_pull_requests (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id) ON DELETE CASCADE,
    number INTEGER,
    url TEXT,
    state TEXT NOT NULL CHECK(state IN ('creating','open','closed','merged','error')),
    title TEXT NOT NULL,
    head_branch TEXT NOT NULL,
    base_branch TEXT NOT NULL,
    draft INTEGER NOT NULL DEFAULT 0 CHECK(draft IN (0,1)),
    merge_state TEXT,
    review_decision TEXT,
    checks_json TEXT NOT NULL DEFAULT '[]',
    runs_json TEXT NOT NULL DEFAULT '[]',
    failed_logs_json TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    merged_at TEXT,
    closed_at TEXT
);
CREATE INDEX idx_task_pull_requests_state ON task_pull_requests(state,updated_at);
"""

MIGRATION_11 = """
CREATE TABLE task_terminals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    pane_id TEXT NOT NULL,
    cwd TEXT NOT NULL,
    shell TEXT NOT NULL,
    pid INTEGER,
    state TEXT NOT NULL CHECK(state IN ('running','exited','stopped')),
    exit_code INTEGER,
    buffer TEXT NOT NULL DEFAULT '',
    output_offset INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ended_at TEXT,
    UNIQUE(task_id,pane_id)
);
CREATE INDEX idx_task_terminals_task_state ON task_terminals(task_id,state);
"""

MIGRATION_12 = """
CREATE TABLE skills (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL CHECK(length(trim(namespace)) > 0),
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    description TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL CHECK(source_kind IN ('janus','codex','claude','github','local','project')),
    source_locator TEXT NOT NULL,
    source_subpath TEXT NOT NULL DEFAULT '',
    source_key TEXT NOT NULL UNIQUE,
    trust_state TEXT NOT NULL DEFAULT 'untrusted' CHECK(trust_state IN ('untrusted','trusted','blocked')),
    latest_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT,
    UNIQUE(namespace,name)
);

CREATE TABLE skill_versions (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK(version >= 1),
    content_hash TEXT NOT NULL CHECK(length(content_hash) = 64),
    source_revision TEXT,
    original_json TEXT NOT NULL,
    compiled_json TEXT NOT NULL,
    report_json TEXT NOT NULL,
    compatibility TEXT NOT NULL CHECK(compatibility IN ('native','partial','adapter_required','blocked')),
    created_at TEXT NOT NULL,
    UNIQUE(skill_id,version),
    UNIQUE(skill_id,content_hash)
);

CREATE TABLE agent_profile_skills (
    agent_profile_id TEXT NOT NULL REFERENCES agent_profiles(id) ON DELETE CASCADE,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    skill_version_id TEXT NOT NULL REFERENCES skill_versions(id) ON DELETE RESTRICT,
    activation_mode TEXT NOT NULL CHECK(activation_mode IN ('off','auto','manual')),
    priority INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(agent_profile_id,skill_id)
);

CREATE TABLE session_skill_snapshots (
    session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE RESTRICT,
    skill_version_id TEXT NOT NULL REFERENCES skill_versions(id) ON DELETE RESTRICT,
    activation_mode TEXT NOT NULL CHECK(activation_mode IN ('auto','manual')),
    loaded_at TEXT,
    load_reason TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0 CHECK(prompt_tokens >= 0),
    PRIMARY KEY(session_id,skill_id)
);

CREATE INDEX idx_skills_source ON skills(source_kind,source_locator);
CREATE INDEX idx_skill_versions_skill ON skill_versions(skill_id,version DESC);
CREATE INDEX idx_agent_profile_skills_profile ON agent_profile_skills(agent_profile_id,activation_mode);
CREATE INDEX idx_session_skill_snapshots_session ON session_skill_snapshots(session_id,loaded_at);
"""

MIGRATION_13 = """
ALTER TABLE agent_profiles ADD COLUMN context_policy_json TEXT NOT NULL DEFAULT
'{"max_chars":24000,"recent_blocks":8,"summary_max_chars":4000,"include_task_objective":true,"include_acceptance":true,"include_workspace_root":true}';
ALTER TABLE dispatches ADD COLUMN agent_profile_snapshot_json TEXT NOT NULL DEFAULT '{}';
"""

# 예전 기본 예산은 대화 하나를 못 버텼다(실측 34k 토큰 > 한도 32,768).
# 사용자가 직접 바꾼 값은 건드리지 않고, 예전 **기본값 그대로인** 프로필만 올린다.
MIGRATION_14 = """
UPDATE agent_profiles SET budget_json = json_set(
    budget_json,
    '$.dispatch.token_limit', 262144,
    '$.dispatch.time_limit_ms', 3600000,
    '$.dispatch.step_limit', 60,
    '$.worker.token_limit', 16384
), updated_at = updated_at
WHERE json_extract(budget_json, '$.dispatch.token_limit') = 32768
  AND json_extract(budget_json, '$.dispatch.time_limit_ms') = 900000
  AND json_extract(budget_json, '$.worker.token_limit') = 8192;
"""

# Worker roles inherit the parent profile's tools. Keep the shipped default profile
# capable of executing ordinary coding commands, including on databases upgraded
# before the seed repair below runs.
MIGRATION_15 = """
UPDATE agent_profiles
SET tools_json = json_insert(tools_json, '$[#]', 'run_bash'),
    updated_at = updated_at
WHERE id = 'agent_default'
  AND NOT EXISTS (
      SELECT 1 FROM json_each(agent_profiles.tools_json)
      WHERE value = 'run_bash'
  );
"""

# Existing tasks keep their established direct workflow. New tasks explicitly opt
# into the mockup-first gate when they are created below.
MIGRATION_16 = """
ALTER TABLE tasks ADD COLUMN workflow_stage TEXT NOT NULL DEFAULT 'direct'
CHECK(workflow_stage IN ('direct','mockup','implementation'));
"""

# v16 accidentally made every newly created task mockup-first. No UI existed to
# opt into that behavior, so unapproved mockup rows are safe to restore to direct.
MIGRATION_17 = """
UPDATE tasks SET workflow_stage='direct' WHERE workflow_stage='mockup';
"""

MIGRATION_18 = """
ALTER TABLE tasks ADD COLUMN mockup_feedback TEXT;

CREATE TABLE session_approval_scopes (
    session_id TEXT NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    scope TEXT NOT NULL CHECK(scope IN ('workspace_write')),
    created_at TEXT NOT NULL,
    PRIMARY KEY(session_id, workspace_id, scope)
);
"""

MIGRATION_19 = """
ALTER TABLE tasks ADD COLUMN attention_reason TEXT
CHECK(attention_reason IS NULL OR attention_reason IN (
    'conversation_idle','mockup_review','input_required'
));
UPDATE tasks SET attention_reason = CASE
    WHEN status='needs_you' AND workflow_stage='mockup' THEN 'mockup_review'
    WHEN status='needs_you' THEN 'conversation_idle'
    ELSE NULL
END;
"""

MIGRATIONS = {
    1: MIGRATION_1, 2: MIGRATION_2, 3: MIGRATION_3, 4: MIGRATION_4,
    5: MIGRATION_5, 6: MIGRATION_6, 7: MIGRATION_7, 8: MIGRATION_8,
    9: MIGRATION_9, 10: MIGRATION_10, 11: MIGRATION_11, 12: MIGRATION_12,
    13: MIGRATION_13, 14: MIGRATION_14, 15: MIGRATION_15, 16: MIGRATION_16,
    17: MIGRATION_17, 18: MIGRATION_18, 19: MIGRATION_19,
}


class DomainStore:
    """요청별 connection과 짧은 transaction을 쓰는 SQLite 저장소."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path, timeout=30, isolation_level=None, factory=ClosingConnection
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row["version"]
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            unknown = sorted(version for version in applied if version > CURRENT_SCHEMA_VERSION)
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if unknown or user_version > CURRENT_SCHEMA_VERSION:
                newest = max([user_version, *unknown])
                raise MigrationError(
                    f"database schema v{newest}은 이 Janus(v{CURRENT_SCHEMA_VERSION})보다 새 버전입니다"
                )
            if applied and applied != set(range(1, max(applied) + 1)):
                raise MigrationError("schema_migrations 이력이 연속적이지 않습니다")
            for version in range(1, CURRENT_SCHEMA_VERSION + 1):
                if version in applied:
                    continue
                sql = MIGRATIONS[version]
                stamp = _now().replace("'", "''")
                connection.executescript(
                    f"BEGIN IMMEDIATE;\n{sql}\n"
                    f"INSERT INTO schema_migrations(version, applied_at) "
                    f"VALUES ({version}, '{stamp}');\n"
                    f"PRAGMA user_version = {version};\nCOMMIT;"
                )
            self._seed_defaults(connection)
        finally:
            connection.close()

    def _seed_defaults(self, connection: sqlite3.Connection) -> None:
        now = _now()
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "INSERT OR IGNORE INTO model_profiles "
                "(id,name,provider,model_key,quantization,config_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    "model_qwen38_27b_4bit", "Qwen3.8 27B 4-bit", "local",
                    "qwen3.8-27b", "4-bit MLX", "{}", now, now,
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO agent_profiles "
                "(id,name,description,system_prompt,tools_json,approval,worker_policy,max_steps,"
                "model_profile_id,budget_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "agent_default", "Janus Local", "Default local coding agent",
                    "You are a local coding agent. Make verified changes in the assigned workspace.",
                    _json(["read_file", "glob", "grep", "write_file", "edit_file", "run_bash"]),
                    "ask", "autonomous", 15, "model_qwen38_27b_4bit",
                    _json(normalize_budget(None)), now, now,
                ),
            )
            default_tools = connection.execute(
                "SELECT tools_json FROM agent_profiles WHERE id='agent_default'"
            ).fetchone()
            if default_tools is not None:
                tools = list(json.loads(default_tools[0] or "[]"))
                if "run_bash" not in tools:
                    tools.append("run_bash")
                    connection.execute(
                        "UPDATE agent_profiles SET tools_json=?,updated_at=? WHERE id='agent_default'",
                        (_json(tools), now),
                    )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    def schema_version(self) -> int:
        with self._connect() as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
            return int(row[0])

    @staticmethod
    def _one(connection: sqlite3.Connection, sql: str, params: tuple, label: str) -> dict:
        row = connection.execute(sql, params).fetchone()
        if row is None:
            raise NotFound(f"없는 {label}: {params[0]}")
        return dict(row)

    def create_project(self, *, name: str, repo_path: str, project_id: str | None = None) -> dict:
        now = _now()
        project_id = project_id or _id("project")
        path = str(Path(repo_path).expanduser().resolve())
        clean_name = name.strip()
        if not clean_name:
            raise Conflict("Project 이름이 필요합니다")
        try:
            with self.transaction(immediate=True) as connection:
                existing = connection.execute(
                    "SELECT * FROM projects WHERE repo_path=?", (path,)
                ).fetchone()
                if existing is not None:
                    existing_id = str(existing["id"])
                    if existing["archived_at"] is not None:
                        connection.execute(
                            "UPDATE projects SET name=?,archived_at=NULL,updated_at=? WHERE id=?",
                            (clean_name, now, existing_id),
                        )
                    project_id = existing_id
                else:
                    connection.execute(
                        "INSERT INTO projects(id,name,repo_path,created_at,updated_at) VALUES (?,?,?,?,?)",
                        (project_id, clean_name, path, now, now),
                    )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"Project 생성 충돌: {error}") from error
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> dict:
        with self._connect() as connection:
            return self._one(connection, "SELECT * FROM projects WHERE id=?", (project_id,), "Project")

    def promote_project_agent_profile(
        self, project_id: str, *, comparison_id: str,
    ) -> dict:
        """Set the measured default and retain the comparison that authorized it."""
        with self.transaction(immediate=True) as connection:
            project = self._one(
                connection, "SELECT * FROM projects WHERE id=?", (project_id,), "Project"
            )
            if project["archived_at"] is not None:
                raise Conflict("archive된 Project의 기본 AgentProfile은 바꿀 수 없습니다")
            comparison = self._one(
                connection, "SELECT * FROM evaluation_comparisons WHERE id=?",
                (comparison_id,), "EvaluationComparison",
            )
            verdict = json.loads(comparison["result_json"]).get("verdict")
            if verdict not in {"improved", "equivalent"}:
                raise Conflict(
                    f"improved/equivalent 비교만 기본값으로 승격할 수 있습니다: {verdict}"
                )
            candidate = self._one(
                connection, "SELECT * FROM evaluation_experiments WHERE id=?",
                (comparison["candidate_experiment_id"],), "EvaluationExperiment",
            )
            agent_profile_id = candidate["agent_profile_id"]
            if not agent_profile_id:
                raise Conflict(
                    "실행한 AgentProfile과 연결된 candidate만 기본값으로 승격할 수 있습니다"
                )
            profile = self._one(
                connection, "SELECT * FROM agent_profiles WHERE id=?",
                (agent_profile_id,), "AgentProfile",
            )
            if profile["archived_at"] is not None:
                raise Conflict("archive된 AgentProfile은 기본값으로 승격할 수 없습니다")
            now = _now()
            connection.execute(
                "UPDATE projects SET default_agent_profile_id=?,promoted_comparison_id=?,"
                "profile_promoted_at=?,updated_at=? WHERE id=?",
                (agent_profile_id, comparison_id, now, now, project_id),
            )
        return self.get_project(project_id)

    def list_projects(self, *, include_archived: bool = False) -> list[dict]:
        where = "" if include_archived else "WHERE archived_at IS NULL"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                f"SELECT * FROM projects {where} ORDER BY created_at"
            )]

    def set_project_verification_commands(
        self, project_id: str, commands: list[dict]
    ) -> dict:
        with self.transaction(immediate=True) as connection:
            self._one(
                connection, "SELECT * FROM projects WHERE id=?", (project_id,), "Project"
            )
            connection.execute(
                "UPDATE projects SET verification_commands_json=?,updated_at=? WHERE id=?",
                (_json(commands), _now(), project_id),
            )
        return self.get_project(project_id)

    def create_verification_run(
        self, *, task_id: str, kind: str, command: str, trigger: str,
        head_commit: str, revision: str, dispatch_id: str | None = None,
        agent_claim: str | None = None, run_id: str | None = None,
    ) -> dict:
        run_id = run_id or _id("verification")
        now = _now()
        try:
            with self.transaction(immediate=True) as connection:
                self._one(connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task")
                connection.execute(
                    "INSERT INTO verification_runs(id,task_id,dispatch_id,kind,command,trigger,"
                    "agent_claim,status,head_commit,revision,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, task_id, dispatch_id, kind, command.strip(), trigger,
                     agent_claim, "queued", head_commit, revision, now),
                )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"Verification run 생성 충돌: {error}") from error
        return self.get_verification_run(run_id)

    def get_verification_run(self, run_id: str) -> dict:
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM verification_runs WHERE id=?",
                (run_id,), "VerificationRun",
            )

    def list_verification_runs(self, task_id: str) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM verification_runs WHERE task_id=? "
                "ORDER BY created_at DESC, id DESC", (task_id,),
            )]

    def start_verification_run(self, run_id: str) -> dict:
        now = _now()
        with self.transaction(immediate=True) as connection:
            run = self._one(
                connection, "SELECT * FROM verification_runs WHERE id=?",
                (run_id,), "VerificationRun",
            )
            if run["status"] != "queued":
                raise Conflict(f"실행할 수 없는 Verification 상태: {run['status']}")
            connection.execute(
                "UPDATE verification_runs SET status='running',started_at=? WHERE id=?",
                (now, run_id),
            )
        return self.get_verification_run(run_id)

    def finish_verification_run(self, run_id: str, result: dict) -> dict:
        exit_code = result.get("exit_code")
        error = result.get("error")
        status = "passed" if exit_code == 0 and not error else (
            "failed" if exit_code is not None else "error"
        )
        with self.transaction(immediate=True) as connection:
            run = self._one(
                connection, "SELECT * FROM verification_runs WHERE id=?",
                (run_id,), "VerificationRun",
            )
            if run["status"] != "running":
                raise Conflict(f"종료할 수 없는 Verification 상태: {run['status']}")
            connection.execute(
                "UPDATE verification_runs SET status=?,exit_code=?,stdout=?,stderr=?,"
                "duration_ms=?,error=?,ended_at=? WHERE id=?",
                (status, exit_code, str(result.get("stdout") or ""),
                 str(result.get("stderr") or ""), result.get("duration_ms"),
                 error, _now(), run_id),
            )
        return self.get_verification_run(run_id)

    def create_review_comment(
        self, *, task_id: str, revision: str, layer: str, file_path: str,
        body: str, old_line: int | None = None, new_line: int | None = None,
        hunk_header: str | None = None, comment_id: str | None = None,
    ) -> dict:
        comment_id = comment_id or _id("comment")
        try:
            with self.transaction(immediate=True) as connection:
                self._one(connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task")
                connection.execute(
                    "INSERT INTO review_comments(id,task_id,revision,layer,file_path,old_line,"
                    "new_line,hunk_header,body,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (comment_id, task_id, revision, layer, file_path, old_line, new_line,
                     hunk_header, body.strip(), _now()),
                )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"Review comment 생성 충돌: {error}") from error
        return self.get_review_comment(comment_id)

    def get_review_comment(self, comment_id: str) -> dict:
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM review_comments WHERE id=?",
                (comment_id,), "ReviewComment",
            )

    def list_review_comments(self, task_id: str) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM review_comments WHERE task_id=? ORDER BY created_at,id",
                (task_id,),
            )]

    def resolve_review_comment(self, comment_id: str, *, resolved: bool) -> dict:
        with self.transaction(immediate=True) as connection:
            self._one(
                connection, "SELECT * FROM review_comments WHERE id=?",
                (comment_id,), "ReviewComment",
            )
            connection.execute(
                "UPDATE review_comments SET resolved_at=? WHERE id=?",
                (_now() if resolved else None, comment_id),
            )
        return self.get_review_comment(comment_id)

    def create_review_decision(
        self, *, task_id: str, revision: str, decision: str,
        comment_ids: list[str], message: str = "", decision_id: str | None = None,
    ) -> dict:
        decision_id = decision_id or _id("decision")
        try:
            with self.transaction(immediate=True) as connection:
                self._one(connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task")
                if comment_ids:
                    marks = ",".join("?" for _ in comment_ids)
                    rows = connection.execute(
                        f"SELECT id FROM review_comments WHERE task_id=? AND id IN ({marks})",
                        (task_id, *comment_ids),
                    ).fetchall()
                    if len(rows) != len(set(comment_ids)):
                        raise Conflict("다른 Task이거나 없는 review comment가 있습니다")
                connection.execute(
                    "INSERT INTO review_decisions(id,task_id,revision,decision,"
                    "comment_ids_json,message,created_at) VALUES (?,?,?,?,?,?,?)",
                    (decision_id, task_id, revision, decision, _json(comment_ids),
                     message.strip(), _now()),
                )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"Review decision 생성 충돌: {error}") from error
        with self._connect() as connection:
            item = self._one(
                connection, "SELECT * FROM review_decisions WHERE id=?",
                (decision_id,), "ReviewDecision",
            )
        item["comment_ids"] = json.loads(item.pop("comment_ids_json"))
        return item

    def list_review_decisions(self, task_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(
                "SELECT * FROM review_decisions WHERE task_id=? ORDER BY created_at,id",
                (task_id,),
            )]
        for item in rows:
            item["comment_ids"] = json.loads(item.pop("comment_ids_json"))
        return rows

    def record_task_shipment(
        self, *, task_id: str, action: str, commit_sha: str,
        branch_name: str, remote: str | None = None, status: str = "completed",
        error: str | None = None, shipment_id: str | None = None,
    ) -> dict:
        shipment_id = shipment_id or _id("shipment")
        try:
            with self.transaction(immediate=True) as connection:
                self._one(connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task")
                connection.execute(
                    "INSERT INTO task_shipments(id,task_id,action,commit_sha,branch_name,"
                    "remote,status,error,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (shipment_id, task_id, action, commit_sha, branch_name, remote,
                     status, error, _now()),
                )
        except sqlite3.IntegrityError as exc:
            raise Conflict(f"Task shipment 기록 충돌: {exc}") from exc
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM task_shipments WHERE id=?",
                (shipment_id,), "TaskShipment",
            )

    def list_task_shipments(self, task_id: str) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM task_shipments WHERE task_id=? ORDER BY created_at,id",
                (task_id,),
            )]

    def record_task_pull_request(
        self, *, task_id: str, title: str, head_branch: str, base_branch: str,
        state: str, details: dict | None = None, error: str | None = None,
    ) -> dict:
        if state not in {"creating", "open", "closed", "merged", "error"}:
            raise Conflict(f"모르는 PullRequest 상태: {state}")
        details = details or {}
        now = _now()
        with self.transaction(immediate=True) as connection:
            self._one(connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task")
            current = connection.execute(
                "SELECT * FROM task_pull_requests WHERE task_id=?", (task_id,)
            ).fetchone()
            if current is None:
                connection.execute(
                    "INSERT INTO task_pull_requests(id,task_id,number,url,state,title,head_branch,"
                    "base_branch,draft,merge_state,review_decision,checks_json,runs_json,"
                    "failed_logs_json,error,created_at,updated_at,merged_at,closed_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        _id("pr"), task_id, details.get("number"), details.get("url"), state,
                        title.strip(), head_branch, base_branch,
                        1 if details.get("draft") else 0, details.get("merge_state"),
                        details.get("review_decision"), _json(details.get("checks") or []),
                        _json(details.get("runs") or []),
                        _json(details.get("failed_logs") or []), error, now, now,
                        details.get("merged_at"), details.get("closed_at"),
                    ),
                )
            else:
                connection.execute(
                    "UPDATE task_pull_requests SET number=COALESCE(?,number),url=COALESCE(?,url),"
                    "state=?,title=?,head_branch=?,base_branch=?,draft=?,merge_state=?,"
                    "review_decision=?,checks_json=?,runs_json=?,failed_logs_json=?,error=?,"
                    "updated_at=?,merged_at=COALESCE(?,merged_at),closed_at=COALESCE(?,closed_at) "
                    "WHERE task_id=?",
                    (
                        details.get("number"), details.get("url"), state, title.strip(),
                        head_branch, base_branch, 1 if details.get("draft") else 0,
                        details.get("merge_state"), details.get("review_decision"),
                        _json(details.get("checks") or []), _json(details.get("runs") or []),
                        _json(details.get("failed_logs") or []), error, now,
                        details.get("merged_at"), details.get("closed_at"), task_id,
                    ),
                )
        item = self.get_task_pull_request(task_id)
        assert item is not None
        return item

    def get_task_pull_request(self, task_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM task_pull_requests WHERE task_id=?", (task_id,)
            ).fetchone()
            return dict(row) if row is not None else None

    def start_task_terminal(
        self, *, terminal_id: str, task_id: str, pane_id: str, cwd: str,
        shell: str, pid: int,
    ) -> dict:
        now = _now()
        with self.transaction(immediate=True) as connection:
            self._one(connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task")
            connection.execute(
                "INSERT INTO task_terminals(id,task_id,pane_id,cwd,shell,pid,state,"
                "created_at,updated_at) VALUES (?,?,?,?,?,?,'running',?,?) "
                "ON CONFLICT(task_id,pane_id) DO UPDATE SET id=excluded.id,cwd=excluded.cwd,"
                "shell=excluded.shell,pid=excluded.pid,state='running',exit_code=NULL,buffer='',"
                "output_offset=0,error=NULL,created_at=excluded.created_at,"
                "updated_at=excluded.updated_at,ended_at=NULL",
                (terminal_id, task_id, pane_id, cwd, shell, pid, now, now),
            )
        return self.get_task_terminal(terminal_id)

    def append_task_terminal_output(
        self, terminal_id: str, *, text: str, output_offset: int,
        max_chars: int = 200_000,
    ) -> dict:
        with self.transaction(immediate=True) as connection:
            current = self._one(
                connection, "SELECT * FROM task_terminals WHERE id=?",
                (terminal_id,), "TaskTerminal",
            )
            combined = (current["buffer"] + str(text))[-max_chars:]
            connection.execute(
                "UPDATE task_terminals SET buffer=?,output_offset=?,updated_at=? WHERE id=?",
                (combined, int(output_offset), _now(), terminal_id),
            )
        return self.get_task_terminal(terminal_id)

    def finish_task_terminal(
        self, terminal_id: str, *, state: str, exit_code: int | None = None,
        error: str | None = None,
    ) -> dict:
        if state not in {"exited", "stopped"}:
            raise Conflict(f"terminal 종료 상태가 올바르지 않습니다: {state}")
        with self.transaction(immediate=True) as connection:
            self._one(
                connection, "SELECT * FROM task_terminals WHERE id=?",
                (terminal_id,), "TaskTerminal",
            )
            now = _now()
            connection.execute(
                "UPDATE task_terminals SET state=?,exit_code=?,error=?,updated_at=?,ended_at=? "
                "WHERE id=?", (state, exit_code, error, now, now, terminal_id),
            )
        return self.get_task_terminal(terminal_id)

    def get_task_terminal(self, terminal_id: str) -> dict:
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM task_terminals WHERE id=?",
                (terminal_id,), "TaskTerminal",
            )

    def list_task_terminals(self, task_id: str) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM task_terminals WHERE task_id=? ORDER BY pane_id", (task_id,)
            )]

    def create_evaluation_experiment(
        self, *, role: str, label: str, source: str,
        agent_profile_id: str | None = None, profile_snapshot: dict | None = None,
        config: dict | None = None, conditions: dict | None = None,
        report: dict | None = None, result_path: str | None = None,
        status: str = "queued", experiment_id: str | None = None,
    ) -> dict:
        experiment_id = experiment_id or _id("evaluation")
        now = _now()
        ended_at = now if status in {"completed", "failed", "cancelled"} else None
        try:
            with self.transaction(immediate=True) as connection:
                if agent_profile_id is not None:
                    self._one(
                        connection, "SELECT * FROM agent_profiles WHERE id=?",
                        (agent_profile_id,), "AgentProfile",
                    )
                connection.execute(
                    "INSERT INTO evaluation_experiments(id,role,label,source,status,"
                    "agent_profile_id,profile_snapshot_json,config_json,conditions_json,"
                    "report_json,result_path,created_at,ended_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (experiment_id, role, label.strip(), source, status, agent_profile_id,
                     _json(profile_snapshot or {}), _json(config or {}),
                     _json(conditions or {}), _json(report) if report is not None else None,
                     result_path, now, ended_at),
                )
        except sqlite3.IntegrityError as exc:
            raise Conflict(f"Evaluation experiment 생성 충돌: {exc}") from exc
        return self.get_evaluation_experiment(experiment_id)

    def get_evaluation_experiment(self, experiment_id: str) -> dict:
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM evaluation_experiments WHERE id=?",
                (experiment_id,), "EvaluationExperiment",
            )

    def list_evaluation_experiments(self) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM evaluation_experiments ORDER BY created_at DESC,id DESC"
            )]

    def start_evaluation_experiment(self, experiment_id: str) -> dict:
        with self.transaction(immediate=True) as connection:
            item = self._one(
                connection, "SELECT * FROM evaluation_experiments WHERE id=?",
                (experiment_id,), "EvaluationExperiment",
            )
            if item["status"] != "queued":
                raise Conflict(f"시작할 수 없는 Evaluation 상태: {item['status']}")
            connection.execute(
                "UPDATE evaluation_experiments SET status='running',started_at=? WHERE id=?",
                (_now(), experiment_id),
            )
        return self.get_evaluation_experiment(experiment_id)

    def finish_evaluation_experiment(
        self, experiment_id: str, *, status: str, report: dict | None = None,
        conditions: dict | None = None, result_path: str | None = None,
        error: str | None = None,
    ) -> dict:
        if status not in {"completed", "failed", "cancelled"}:
            raise Conflict(f"종료 Evaluation 상태가 아닙니다: {status}")
        with self.transaction(immediate=True) as connection:
            item = self._one(
                connection, "SELECT * FROM evaluation_experiments WHERE id=?",
                (experiment_id,), "EvaluationExperiment",
            )
            if item["status"] not in {"queued", "running"}:
                raise Conflict(f"종료할 수 없는 Evaluation 상태: {item['status']}")
            connection.execute(
                "UPDATE evaluation_experiments SET status=?,report_json=COALESCE(?,report_json),"
                "conditions_json=COALESCE(?,conditions_json),result_path=COALESCE(?,result_path),"
                "error=?,ended_at=? WHERE id=?",
                (status, _json(report) if report is not None else None,
                 _json(conditions) if conditions is not None else None,
                 result_path, error, _now(), experiment_id),
            )
        return self.get_evaluation_experiment(experiment_id)

    def create_evaluation_comparison(
        self, *, baseline_experiment_id: str, candidate_experiment_id: str,
        thresholds: dict, result: dict, comparison_id: str | None = None,
    ) -> dict:
        comparison_id = comparison_id or _id("comparison")
        try:
            with self.transaction(immediate=True) as connection:
                for experiment_id in (baseline_experiment_id, candidate_experiment_id):
                    self._one(
                        connection, "SELECT * FROM evaluation_experiments WHERE id=?",
                        (experiment_id,), "EvaluationExperiment",
                    )
                connection.execute(
                    "INSERT INTO evaluation_comparisons(id,baseline_experiment_id,"
                    "candidate_experiment_id,thresholds_json,result_json,created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (comparison_id, baseline_experiment_id, candidate_experiment_id,
                     _json(thresholds), _json(result), _now()),
                )
        except sqlite3.IntegrityError as exc:
            raise Conflict(f"Evaluation comparison 생성 충돌: {exc}") from exc
        return self.get_evaluation_comparison(comparison_id)

    def get_evaluation_comparison(self, comparison_id: str) -> dict:
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM evaluation_comparisons WHERE id=?",
                (comparison_id,), "EvaluationComparison",
            )

    def list_evaluation_comparisons(self) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM evaluation_comparisons ORDER BY created_at DESC,id DESC"
            )]

    def archive_project(self, project_id: str) -> dict:
        now = _now()
        with self.transaction(immediate=True) as connection:
            changed = connection.execute(
                "UPDATE projects SET archived_at=?,updated_at=? "
                "WHERE id=? AND archived_at IS NULL", (now, now, project_id)
            ).rowcount
            if not changed:
                self._one(connection, "SELECT * FROM projects WHERE id=?", (project_id,), "Project")
        return self.get_project(project_id)

    def create_task(
        self, *, project_id: str, title: str, objective: str,
        acceptance_command: str, base_ref: str, workflow_stage: str = "direct",
        task_id: str | None = None,
    ) -> dict:
        if workflow_stage not in {"direct", "mockup"}:
            raise Conflict("Task 생성 workflow_stage는 direct 또는 mockup이어야 합니다")
        now = _now()
        task_id = task_id or _id("task")
        try:
            with self.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO tasks(id,project_id,title,objective,acceptance_command,base_ref,"
                    "status,created_at,updated_at,workflow_stage) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        task_id, project_id, title.strip(), objective.strip(),
                        acceptance_command.strip(), base_ref.strip(), "todo", now, now,
                        workflow_stage,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"Task 생성 충돌: {error}") from error
        return self.get_task(task_id)

    def approve_task_mockup(self, task_id: str) -> dict:
        """Open implementation only after the user accepts the visible mockup."""
        now = _now()
        with self.transaction(immediate=True) as connection:
            task = self._one(
                connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task"
            )
            if task["workflow_stage"] != "mockup" or task["status"] != "needs_you":
                raise Conflict("목업 승인 대기 상태가 아닙니다")
            connection.execute(
                "UPDATE tasks SET workflow_stage='implementation',mockup_feedback=NULL,"
                "updated_at=? WHERE id=?",
                (now, task_id),
            )
        return self.get_task(task_id)

    def reject_task_mockup(self, task_id: str, feedback: str) -> dict:
        """Record actionable feedback without opening the implementation stage."""
        feedback = feedback.strip()
        if not feedback:
            raise Conflict("목업 수정 요청 내용을 입력하세요")
        now = _now()
        with self.transaction(immediate=True) as connection:
            task = self._one(
                connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task"
            )
            if task["workflow_stage"] != "mockup" or task["status"] != "needs_you":
                raise Conflict("목업 수정 요청 대기 상태가 아닙니다")
            connection.execute(
                "UPDATE tasks SET mockup_feedback=?,updated_at=? WHERE id=?",
                (feedback, now, task_id),
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict:
        with self._connect() as connection:
            return self._one(connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task")

    def list_tasks(self, project_id: str, *, include_archived: bool = False) -> list[dict]:
        archived = "" if include_archived else "AND archived_at IS NULL"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                f"SELECT * FROM tasks WHERE project_id=? {archived} ORDER BY created_at",
                (project_id,),
            )]

    def update_task(self, task_id: str, **changes: str) -> dict:
        allowed = {"title", "objective", "acceptance_command", "base_ref"}
        fields = {key: value.strip() for key, value in changes.items() if key in allowed}
        if not fields:
            return self.get_task(task_id)
        assignments = ",".join(f"{key}=?" for key in fields)
        with self.transaction(immediate=True) as connection:
            values = [*fields.values(), _now(), task_id]
            changed = connection.execute(
                f"UPDATE tasks SET {assignments},updated_at=? WHERE id=? AND archived_at IS NULL",
                values,
            ).rowcount
            if not changed:
                self._one(connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task")
        return self.get_task(task_id)

    def transition_task(self, task_id: str, target: str, *, expected: str | None = None) -> dict:
        if target not in TASK_STATUSES:
            raise InvalidTransition(f"모르는 Task 상태: {target}")
        with self.transaction(immediate=True) as connection:
            current = self._one(
                connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task"
            )
            source = current["status"]
            if expected is not None and source != expected:
                raise Conflict(f"Task 상태가 바뀌었습니다: expected={expected}, actual={source}")
            if target == source:
                return current
            if target not in TASK_TRANSITIONS[source]:
                raise InvalidTransition(f"Task 상태 전이 불가: {source} -> {target}")
            connection.execute(
                "UPDATE tasks SET status=?,attention_reason=?,updated_at=? WHERE id=?",
                (target, "input_required" if target == "needs_you" else None, _now(), task_id),
            )
        return self.get_task(task_id)

    def archive_task(self, task_id: str) -> dict:
        now = _now()
        with self.transaction(immediate=True) as connection:
            changed = connection.execute(
                "UPDATE tasks SET archived_at=?,updated_at=? WHERE id=? AND archived_at IS NULL",
                (now, now, task_id),
            ).rowcount
            if not changed:
                self._one(connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task")
        return self.get_task(task_id)

    def create_workspace(
        self, *, task_id: str, repo_path: str, base_ref: str,
        workspace_id: str | None = None,
    ) -> dict:
        now = _now()
        workspace_id = workspace_id or _id("workspace")
        try:
            with self.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO workspaces(id,task_id,repo_path,base_ref,state,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        workspace_id, task_id, str(Path(repo_path).expanduser().resolve()),
                        base_ref, "preparing", now, now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"Workspace 생성 충돌: {error}") from error
        return self.get_workspace(workspace_id)

    def get_workspace(self, workspace_id: str) -> dict:
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM workspaces WHERE id=?", (workspace_id,), "Workspace"
            )

    def get_task_workspace(self, task_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM workspaces WHERE task_id=?", (task_id,)).fetchone()
            return dict(row) if row is not None else None

    def transition_workspace(
        self, workspace_id: str, target: str, *, root_path: str | None = None,
        branch_name: str | None = None, error: str | None = None,
        progress: str | None = None, base_ref: str | None = None,
    ) -> dict:
        if target not in WORKSPACE_STATES:
            raise InvalidTransition(f"모르는 Workspace 상태: {target}")
        with self.transaction(immediate=True) as connection:
            current = self._one(
                connection, "SELECT * FROM workspaces WHERE id=?", (workspace_id,), "Workspace"
            )
            source = current["state"]
            if target != source and target not in WORKSPACE_TRANSITIONS[source]:
                raise InvalidTransition(f"Workspace 상태 전이 불가: {source} -> {target}")
            connection.execute(
                "UPDATE workspaces SET state=?,root_path=COALESCE(?,root_path),"
                "branch_name=COALESCE(?,branch_name),error=?,"
                "progress=COALESCE(?,progress),base_ref=COALESCE(?,base_ref),"
                "updated_at=? WHERE id=?",
                (
                    target, root_path, branch_name, error, progress, base_ref,
                    _now(), workspace_id,
                ),
            )
        return self.get_workspace(workspace_id)

    def update_workspace_preparation(
        self, workspace_id: str, *, progress: str,
        root_path: str | None = None, branch_name: str | None = None,
        error: str | None = None,
    ) -> dict:
        """Persist background preparation progress without changing its state."""
        with self.transaction(immediate=True) as connection:
            current = self._one(
                connection, "SELECT * FROM workspaces WHERE id=?", (workspace_id,), "Workspace"
            )
            if current["state"] != "preparing":
                raise Conflict(
                    f"Workspace가 preparing이 아닙니다: {current['state']}"
                )
            connection.execute(
                "UPDATE workspaces SET progress=?,root_path=COALESCE(?,root_path),"
                "branch_name=COALESCE(?,branch_name),error=?,updated_at=? WHERE id=?",
                (progress, root_path, branch_name, error, _now(), workspace_id),
            )
        return self.get_workspace(workspace_id)

    def import_skill_version(
        self, *, namespace: str, name: str, description: str,
        source_kind: str, source_locator: str, source_subpath: str,
        source_key: str, content_hash: str, original: dict, compiled: dict,
        report: dict, compatibility: str, source_revision: str | None = None,
    ) -> dict:
        """Create or version one imported skill without mutating older artifacts."""
        now = _now()
        skill_id = _id("skill")
        version_id = _id("skill_version")
        try:
            with self.transaction(immediate=True) as connection:
                existing = connection.execute(
                    "SELECT * FROM skills WHERE source_key=?", (source_key,)
                ).fetchone()
                if existing is None:
                    connection.execute(
                        "INSERT INTO skills(id,namespace,name,description,source_kind,"
                        "source_locator,source_subpath,source_key,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            skill_id, namespace.strip(), name.strip(), description.strip(),
                            source_kind, source_locator, source_subpath, source_key, now, now,
                        ),
                    )
                    version = 1
                else:
                    skill_id = str(existing["id"])
                    duplicate = connection.execute(
                        "SELECT id FROM skill_versions WHERE skill_id=? AND content_hash=?",
                        (skill_id, content_hash),
                    ).fetchone()
                    if duplicate is not None:
                        version_id = str(duplicate["id"])
                        connection.execute(
                            "UPDATE skills SET archived_at=NULL,updated_at=? WHERE id=?",
                            (now, skill_id),
                        )
                        return self.get_skill_version(version_id)
                    version = int(connection.execute(
                        "SELECT COALESCE(MAX(version),0)+1 FROM skill_versions WHERE skill_id=?",
                        (skill_id,),
                    ).fetchone()[0])
                    connection.execute(
                        "UPDATE skills SET name=?,description=?,source_locator=?,source_subpath=?,"
                        "archived_at=NULL,updated_at=? WHERE id=?",
                        (name.strip(), description.strip(), source_locator, source_subpath, now, skill_id),
                    )

                connection.execute(
                    "INSERT INTO skill_versions(id,skill_id,version,content_hash,source_revision,"
                    "original_json,compiled_json,report_json,compatibility,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        version_id, skill_id, version, content_hash, source_revision,
                        _json(original), _json(compiled), _json(report), compatibility, now,
                    ),
                )
                connection.execute(
                    "UPDATE skills SET latest_version_id=?,updated_at=? WHERE id=?",
                    (version_id, now, skill_id),
                )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"Skill 가져오기 충돌: {error}") from error
        return self.get_skill_version(version_id)

    def get_skill_version(self, version_id: str) -> dict:
        with self._connect() as connection:
            return self._one(
                connection,
                "SELECT v.*,s.namespace,s.name,s.description,s.source_kind,s.source_locator,"
                "s.source_subpath,s.source_key,s.trust_state,s.archived_at "
                "FROM skill_versions v JOIN skills s ON s.id=v.skill_id WHERE v.id=?",
                (version_id,), "SkillVersion",
            )

    def get_skill(self, skill_id: str) -> dict:
        with self._connect() as connection:
            return self._one(
                connection,
                "SELECT s.*,v.version,v.content_hash,v.source_revision,v.original_json,"
                "v.compiled_json,v.report_json,v.compatibility,v.created_at AS version_created_at "
                "FROM skills s LEFT JOIN skill_versions v ON v.id=s.latest_version_id WHERE s.id=?",
                (skill_id,), "Skill",
            )

    def list_skills(self, *, include_archived: bool = False) -> list[dict]:
        where = "" if include_archived else "WHERE s.archived_at IS NULL"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT s.*,v.version,v.content_hash,v.source_revision,v.compiled_json,"
                "v.report_json,v.compatibility,v.created_at AS version_created_at "
                "FROM skills s LEFT JOIN skill_versions v ON v.id=s.latest_version_id "
                f"{where} ORDER BY s.namespace,s.name"
            )]

    def set_agent_profile_skill(
        self, *, agent_profile_id: str, skill_id: str,
        activation_mode: str, skill_version_id: str | None = None, priority: int = 0,
    ) -> dict:
        if activation_mode not in {"off", "auto", "manual"}:
            raise Conflict("activation_mode는 off, auto, manual 중 하나여야 합니다")
        with self.transaction(immediate=True) as connection:
            self._one(
                connection, "SELECT * FROM agent_profiles WHERE id=?",
                (agent_profile_id,), "AgentProfile",
            )
            skill = self._one(
                connection, "SELECT * FROM skills WHERE id=? AND archived_at IS NULL",
                (skill_id,), "Skill",
            )
            selected_version_id = skill_version_id or skill["latest_version_id"]
            version = self._one(
                connection, "SELECT * FROM skill_versions WHERE id=?",
                (selected_version_id,), "SkillVersion",
            )
            if version["skill_id"] != skill_id:
                raise Conflict("선택한 SkillVersion이 해당 Skill에 속하지 않습니다")
            if activation_mode != "off" and version["compatibility"] in {"blocked", "adapter_required"}:
                raise Conflict(
                    f"{version['compatibility']} SkillVersion은 활성화할 수 없습니다"
                )
            connection.execute(
                "INSERT INTO agent_profile_skills(agent_profile_id,skill_id,skill_version_id,"
                "activation_mode,priority,updated_at) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(agent_profile_id,skill_id) DO UPDATE SET "
                "skill_version_id=excluded.skill_version_id,activation_mode=excluded.activation_mode,"
                "priority=excluded.priority,updated_at=excluded.updated_at",
                (
                    agent_profile_id, skill_id, selected_version_id,
                    activation_mode, int(priority), _now(),
                ),
            )
        return self.get_agent_profile_skill(agent_profile_id, skill_id)

    def get_agent_profile_skill(self, agent_profile_id: str, skill_id: str) -> dict:
        with self._connect() as connection:
            return _versioned_skill_metadata(self._one(
                connection,
                "SELECT a.*,s.namespace,s.name,s.description,v.version,v.content_hash,"
                "v.compiled_json,v.report_json,v.compatibility "
                "FROM agent_profile_skills a JOIN skills s ON s.id=a.skill_id "
                "JOIN skill_versions v ON v.id=a.skill_version_id "
                "WHERE a.agent_profile_id=? AND a.skill_id=?",
                (agent_profile_id, skill_id), "AgentProfileSkill",
            ))

    def list_agent_profile_skills(
        self, agent_profile_id: str, *, active_only: bool = False,
    ) -> list[dict]:
        where = "AND a.activation_mode <> 'off'" if active_only else ""
        with self._connect() as connection:
            self._one(
                connection, "SELECT * FROM agent_profiles WHERE id=?",
                (agent_profile_id,), "AgentProfile",
            )
            return [_versioned_skill_metadata(dict(row)) for row in connection.execute(
                "SELECT a.*,s.namespace,s.name,s.description,s.source_kind,s.source_locator,"
                "s.source_subpath,s.trust_state,v.version,v.content_hash,v.compiled_json,"
                "v.report_json,v.compatibility FROM agent_profile_skills a "
                "JOIN skills s ON s.id=a.skill_id JOIN skill_versions v ON v.id=a.skill_version_id "
                f"WHERE a.agent_profile_id=? {where} ORDER BY a.priority DESC,s.namespace,s.name",
                (agent_profile_id,),
            )]

    def snapshot_session_skills(self, session_id: str) -> list[dict]:
        with self._connect() as connection:
            self._one(
                connection, "SELECT * FROM agent_sessions WHERE id=?",
                (session_id,), "AgentSession",
            )
            return [_versioned_skill_metadata(dict(row)) for row in connection.execute(
                "SELECT ss.*,s.namespace,s.name,s.description,v.version,"
                "v.content_hash,v.compiled_json,v.compatibility "
                "FROM session_skill_snapshots ss JOIN skill_versions v ON v.id=ss.skill_version_id "
                "JOIN skills s ON s.id=v.skill_id WHERE ss.session_id=? "
                "ORDER BY s.namespace,s.name",
                (session_id,),
            )]

    def mark_session_skill_loaded(
        self, session_id: str, skill_version_id: str, *, reason: str, prompt_tokens: int,
    ) -> dict:
        with self.transaction(immediate=True) as connection:
            changed = connection.execute(
                "UPDATE session_skill_snapshots SET loaded_at=COALESCE(loaded_at,?),"
                "load_reason=?,prompt_tokens=? WHERE session_id=? AND skill_version_id=?",
                (_now(), reason[:1000], max(0, int(prompt_tokens)), session_id, skill_version_id),
            ).rowcount
            if not changed:
                raise NotFound(f"없는 Session Skill: {session_id}/{skill_version_id}")
        return next(
            item for item in self.snapshot_session_skills(session_id)
            if item["skill_version_id"] == skill_version_id
        )

    def list_model_profiles(self) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM model_profiles ORDER BY created_at"
            )]

    def get_model_profile(self, profile_id: str) -> dict:
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM model_profiles WHERE id=?",
                (profile_id,), "ModelProfile",
            )

    def create_model_profile(
        self, *, name: str, model_key: str, quantization: str,
        config: dict | None = None, profile_id: str | None = None,
    ) -> dict:
        now = _now()
        profile_id = profile_id or _id("model")
        try:
            with self.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO model_profiles(id,name,provider,model_key,quantization,config_json,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (profile_id, name, "local", model_key, quantization, _json(config or {}), now, now),
                )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"ModelProfile 생성 충돌: {error}") from error
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM model_profiles WHERE id=?", (profile_id,), "ModelProfile"
            )

    def list_agent_profiles(self, *, include_archived: bool = False) -> list[dict]:
        where = "" if include_archived else "WHERE archived_at IS NULL"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                f"SELECT * FROM agent_profiles {where} ORDER BY created_at"
            )]

    def create_agent_profile(
        self, *, name: str, system_prompt: str, tools: list[str],
        model_profile_id: str, approval: str = "ask", worker_policy: str = "autonomous",
        max_steps: int = 15, description: str = "", profile_id: str | None = None,
        budget: dict | None = None, context_policy: dict | None = None,
    ) -> dict:
        now = _now()
        profile_id = profile_id or _id("agent")
        try:
            with self.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO agent_profiles(id,name,description,system_prompt,tools_json,approval,"
                    "worker_policy,max_steps,model_profile_id,budget_json,context_policy_json,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        profile_id, name, description, system_prompt, _json(tools), approval,
                        worker_policy, max_steps, model_profile_id,
                        _json(normalize_budget(budget, max_steps=max_steps)),
                        _json(normalize_context_policy(context_policy)), now, now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"AgentProfile 생성 충돌: {error}") from error
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM agent_profiles WHERE id=?", (profile_id,), "AgentProfile"
            )

    def update_agent_profile(self, profile_id: str, **changes: Any) -> dict:
        mapping = {
            "name": "name", "description": "description", "system_prompt": "system_prompt",
            "approval": "approval", "worker_policy": "worker_policy", "max_steps": "max_steps",
            "model_profile_id": "model_profile_id",
        }
        fields = {mapping[key]: value for key, value in changes.items() if key in mapping}
        if "tools" in changes:
            fields["tools_json"] = _json(changes["tools"])
        if "budget" in changes or "max_steps" in changes:
            current_budget = json.loads(self.get_agent_profile(profile_id)["budget_json"])
            override = dict(changes.get("budget") or {})
            if "max_steps" in changes and "step_limit" not in override.get("dispatch", {}):
                override = {**override, "dispatch": {
                    **override.get("dispatch", {}), "step_limit": int(changes["max_steps"]),
                }}
            fields["budget_json"] = _json(merge_budget(current_budget, override))
        if "context_policy" in changes:
            current_policy = json.loads(
                self.get_agent_profile(profile_id).get("context_policy_json")
                or _json(DEFAULT_CONTEXT_POLICY)
            )
            requested_policy = changes.get("context_policy")
            if not isinstance(requested_policy, dict):
                raise Conflict("context_policy는 객체여야 합니다")
            fields["context_policy_json"] = _json(normalize_context_policy({
                **current_policy, **requested_policy,
            }))
        if not fields:
            return self.get_agent_profile(profile_id)
        assignments = ",".join(f"{key}=?" for key in fields)
        try:
            with self.transaction(immediate=True) as connection:
                values = [*fields.values(), _now(), profile_id]
                changed = connection.execute(
                    f"UPDATE agent_profiles SET {assignments},updated_at=? "
                    "WHERE id=? AND archived_at IS NULL", values,
                ).rowcount
                if not changed:
                    self._one(
                        connection, "SELECT * FROM agent_profiles WHERE id=?",
                        (profile_id,), "AgentProfile",
                    )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"AgentProfile 수정 충돌: {error}") from error
        return self.get_agent_profile(profile_id)

    def get_agent_profile(self, profile_id: str) -> dict:
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM agent_profiles WHERE id=?",
                (profile_id,), "AgentProfile",
            )

    def create_dispatch(
        self, *, task_id: str, workspace_id: str, agent_profile_id: str,
        dispatch_id: str | None = None,
    ) -> dict:
        dispatch_id = dispatch_id or _id("dispatch")
        now = _now()
        try:
            with self.transaction(immediate=True) as connection:
                task = self._one(
                    connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task"
                )
                workspace = self._one(
                    connection, "SELECT * FROM workspaces WHERE id=?",
                    (workspace_id,), "Workspace",
                )
                if workspace["task_id"] != task_id:
                    raise Conflict("Dispatch의 Workspace가 다른 Task에 속합니다")
                profile = self._one(
                    connection, "SELECT * FROM agent_profiles WHERE id=?",
                    (agent_profile_id,), "AgentProfile",
                )
                attempt = int(connection.execute(
                    "SELECT COALESCE(MAX(attempt),0)+1 FROM dispatches WHERE task_id=?",
                    (task_id,),
                ).fetchone()[0])
                connection.execute(
                    "INSERT INTO dispatches(id,task_id,workspace_id,agent_profile_id,attempt,status,"
                    "objective_snapshot,acceptance_snapshot,budget_json,usage_json,"
                    "agent_profile_snapshot_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        dispatch_id, task_id, workspace_id, agent_profile_id, attempt, "queued",
                        task["objective"], task["acceptance_command"], profile["budget_json"],
                        _json(empty_usage()), _json(agent_profile_snapshot(profile)), now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"Dispatch 생성 충돌: {error}") from error
        return self.get_dispatch(dispatch_id)

    def create_execution(
        self, *, task_id: str, workspace_id: str, agent_profile_id: str,
        dispatch_id: str | None = None, session_id: str | None = None,
        budget_override: dict | None = None, adaptive_decision: dict | None = None,
    ) -> dict:
        """Create one Dispatch attempt and its AgentSession atomically.

        Starting a newer attempt retires resumable state from the previous attempt.
        Runtime threads are cancelled by the server; this transaction is the durable
        ownership gate that makes any late events from them stale.
        """
        dispatch_id = dispatch_id or _id("dispatch")
        session_id = session_id or _id("session")
        now = _now()
        try:
            with self.transaction(immediate=True) as connection:
                task = self._one(
                    connection, "SELECT * FROM tasks WHERE id=?", (task_id,), "Task"
                )
                workspace = self._one(
                    connection, "SELECT * FROM workspaces WHERE id=?",
                    (workspace_id,), "Workspace",
                )
                profile = self._one(
                    connection, "SELECT * FROM agent_profiles WHERE id=?",
                    (agent_profile_id,), "AgentProfile",
                )
                if workspace["task_id"] != task_id:
                    raise Conflict("Dispatch의 Workspace가 다른 Task에 속합니다")
                if workspace["state"] != "ready" or not workspace["root_path"]:
                    raise Conflict("ready Workspace가 있어야 Task를 시작할 수 있습니다")
                if profile["archived_at"] is not None:
                    raise Conflict("archive된 AgentProfile은 선택할 수 없습니다")
                if task["archived_at"] is not None:
                    raise Conflict("archive된 Task는 시작할 수 없습니다")
                if task["status"] == "preparing":
                    raise Conflict("Workspace 준비 중에는 Task를 시작할 수 없습니다")

                # A newer attempt owns the Task immediately. Old threads may still emit,
                # but append_session_event(require_latest=True) rejects those events.
                old_dispatch_ids = [
                    row["id"] for row in connection.execute(
                        "SELECT id FROM dispatches WHERE task_id=? "
                        "AND status IN ('queued','running','needs_you')",
                        (task_id,),
                    )
                ]
                if old_dispatch_ids:
                    marks = ",".join("?" for _ in old_dispatch_ids)
                    connection.execute(
                        f"UPDATE dispatches SET status='cancelled',ended_at=?,"
                        f"error='superseded by a newer attempt' WHERE id IN ({marks})",
                        (now, *old_dispatch_ids),
                    )
                    connection.execute(
                        f"UPDATE agent_sessions SET status='stopped',stopped_at=?,updated_at=?,"
                        f"error='superseded by a newer attempt' WHERE dispatch_id IN ({marks}) "
                        "AND status IN ('created','running','idle')",
                        (now, now, *old_dispatch_ids),
                    )

                attempt = int(connection.execute(
                    "SELECT COALESCE(MAX(attempt),0)+1 FROM dispatches WHERE task_id=?",
                    (task_id,),
                ).fetchone()[0])
                dispatch_budget = merge_budget(
                    json.loads(profile["budget_json"]), budget_override
                )
                connection.execute(
                    "INSERT INTO dispatches(id,task_id,workspace_id,agent_profile_id,attempt,status,"
                    "objective_snapshot,acceptance_snapshot,budget_json,usage_json,"
                    "adaptive_decision_json,agent_profile_snapshot_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        dispatch_id, task_id, workspace_id, agent_profile_id, attempt, "queued",
                        task["objective"], task["acceptance_command"],
                        _json(dispatch_budget),
                        _json(empty_usage()), _json(adaptive_decision or {}),
                        _json(agent_profile_snapshot(profile)), now,
                    ),
                )
                connection.execute(
                    "INSERT INTO agent_sessions(id,task_id,dispatch_id,agent_profile_id,status,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (session_id, task_id, dispatch_id, agent_profile_id, "created", now, now),
                )
                connection.execute(
                    "INSERT INTO session_skill_snapshots("
                    "session_id,skill_id,skill_version_id,activation_mode) "
                    "SELECT ?,skill_id,skill_version_id,activation_mode FROM agent_profile_skills "
                    "WHERE agent_profile_id=? AND activation_mode IN ('auto','manual')",
                    (session_id, agent_profile_id),
                )
                connection.execute(
                    "UPDATE tasks SET status='working',updated_at=? WHERE id=?",
                    (now, task_id),
                )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"Task 실행 생성 충돌: {error}") from error
        return {
            "dispatch": self.get_dispatch(dispatch_id),
            "session": self.get_session(session_id),
        }

    def get_dispatch(self, dispatch_id: str) -> dict:
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM dispatches WHERE id=?", (dispatch_id,), "Dispatch"
            )

    def record_dispatch_budget(
        self, dispatch_id: str, *, usage: dict,
        exhausted_reason: str | None = None,
    ) -> dict:
        with self.transaction(immediate=True) as connection:
            self._one(
                connection, "SELECT * FROM dispatches WHERE id=?",
                (dispatch_id,), "Dispatch",
            )
            connection.execute(
                "UPDATE dispatches SET usage_json=?,budget_exhausted_reason=COALESCE(?,"
                "budget_exhausted_reason) WHERE id=?",
                (_json(usage), exhausted_reason, dispatch_id),
            )
        return self.get_dispatch(dispatch_id)

    def list_dispatches(self, task_id: str) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT * FROM dispatches WHERE task_id=? ORDER BY attempt", (task_id,)
            )]

    def latest_dispatch(self, task_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM dispatches WHERE task_id=? ORDER BY attempt DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def transition_dispatch(
        self, dispatch_id: str, target: str, *, error: str | None = None,
        expected: str | None = None,
    ) -> dict:
        if target not in DISPATCH_STATUSES:
            raise InvalidTransition(f"모르는 Dispatch 상태: {target}")
        with self.transaction(immediate=True) as connection:
            current = self._one(
                connection, "SELECT * FROM dispatches WHERE id=?", (dispatch_id,), "Dispatch"
            )
            source = current["status"]
            if expected is not None and source != expected:
                raise Conflict(f"Dispatch 상태가 바뀌었습니다: expected={expected}, actual={source}")
            if target != source and target not in DISPATCH_TRANSITIONS[source]:
                raise InvalidTransition(f"Dispatch 상태 전이 불가: {source} -> {target}")
            started = _now() if target == "running" and current["started_at"] is None else current["started_at"]
            ended = _now() if target in {"completed", "failed", "cancelled"} else None
            connection.execute(
                "UPDATE dispatches SET status=?,error=?,started_at=?,ended_at=? WHERE id=?",
                (target, error, started, ended, dispatch_id),
            )
        return self.get_dispatch(dispatch_id)

    def create_session(
        self, *, task_id: str, dispatch_id: str, agent_profile_id: str,
        session_id: str | None = None,
    ) -> dict:
        session_id = session_id or _id("session")
        now = _now()
        try:
            with self.transaction(immediate=True) as connection:
                dispatch = self._one(
                    connection, "SELECT * FROM dispatches WHERE id=?",
                    (dispatch_id,), "Dispatch",
                )
                if dispatch["task_id"] != task_id:
                    raise Conflict("AgentSession의 Dispatch가 다른 Task에 속합니다")
                if dispatch["agent_profile_id"] != agent_profile_id:
                    raise Conflict("AgentSession의 AgentProfile이 Dispatch와 다릅니다")
                connection.execute(
                    "INSERT INTO agent_sessions(id,task_id,dispatch_id,agent_profile_id,status,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (session_id, task_id, dispatch_id, agent_profile_id, "created", now, now),
                )
                connection.execute(
                    "INSERT INTO session_skill_snapshots("
                    "session_id,skill_id,skill_version_id,activation_mode) "
                    "SELECT ?,skill_id,skill_version_id,activation_mode FROM agent_profile_skills "
                    "WHERE agent_profile_id=? AND activation_mode IN ('auto','manual')",
                    (session_id, agent_profile_id),
                )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"AgentSession 생성 충돌: {error}") from error
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict:
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM agent_sessions WHERE id=?", (session_id,), "AgentSession"
            )

    def grant_session_approval_scope(
        self, session_id: str, workspace_id: str, scope: str,
    ) -> dict:
        if scope != "workspace_write":
            raise Conflict(f"지원하지 않는 승인 범위입니다: {scope}")
        created_at = _now()
        with self.transaction(immediate=True) as connection:
            session = self._one(
                connection, "SELECT * FROM agent_sessions WHERE id=?",
                (session_id,), "AgentSession",
            )
            workspace = self._one(
                connection, "SELECT * FROM workspaces WHERE id=?",
                (workspace_id,), "Workspace",
            )
            if session["task_id"] != workspace["task_id"]:
                raise Conflict("승인 범위의 Session과 Workspace가 다른 Task에 속합니다")
            connection.execute(
                "INSERT OR IGNORE INTO session_approval_scopes("
                "session_id,workspace_id,scope,created_at) VALUES (?,?,?,?)",
                (session_id, workspace_id, scope, created_at),
            )
        return {
            "session_id": session_id, "workspace_id": workspace_id,
            "scope": scope, "created_at": created_at,
        }

    def list_session_approval_scopes(self, session_id: str) -> list[dict]:
        with self._connect() as connection:
            self._one(
                connection, "SELECT * FROM agent_sessions WHERE id=?",
                (session_id,), "AgentSession",
            )
            return [dict(row) for row in connection.execute(
                "SELECT * FROM session_approval_scopes WHERE session_id=? "
                "ORDER BY workspace_id,scope", (session_id,),
            )]

    def list_sessions(self, task_id: str) -> list[dict]:
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT s.* FROM agent_sessions s "
                "JOIN dispatches d ON d.id=s.dispatch_id "
                "WHERE s.task_id=? ORDER BY d.attempt DESC, s.created_at DESC",
                (task_id,),
            )]

    def activate_session_turn(self, session_id: str) -> dict:
        """Claim the latest Dispatch for one turn and mark the Task working."""
        now = _now()
        with self.transaction(immediate=True) as connection:
            session = self._one(
                connection, "SELECT * FROM agent_sessions WHERE id=?",
                (session_id,), "AgentSession",
            )
            latest = connection.execute(
                "SELECT id,status FROM dispatches WHERE task_id=? "
                "ORDER BY attempt DESC LIMIT 1", (session["task_id"],),
            ).fetchone()
            if latest is None or latest["id"] != session["dispatch_id"]:
                raise StaleDispatch(f"오래된 Dispatch의 Session입니다: {session['dispatch_id']}")
            if session["status"] not in {"created", "idle"}:
                raise Conflict(f"실행할 수 없는 AgentSession 상태: {session['status']}")
            if latest["status"] not in {"queued", "needs_you"}:
                raise Conflict(f"실행할 수 없는 Dispatch 상태: {latest['status']}")
            connection.execute(
                "UPDATE agent_sessions SET status='running',error=NULL,updated_at=? WHERE id=?",
                (now, session_id),
            )
            connection.execute(
                "UPDATE dispatches SET status='running',error=NULL,"
                "started_at=COALESCE(started_at,?) WHERE id=?",
                (now, session["dispatch_id"]),
            )
            connection.execute(
                "UPDATE tasks SET status='working',attention_reason=NULL,updated_at=? WHERE id=?",
                (now, session["task_id"]),
            )
        return self.get_session(session_id)

    def settle_session_turn(
        self, session_id: str, *, failed: bool = False, error: str | None = None,
    ) -> dict:
        """Persist the post-turn resumable state, guarded by latest Dispatch."""
        now = _now()
        with self.transaction(immediate=True) as connection:
            session = self._one(
                connection, "SELECT * FROM agent_sessions WHERE id=?",
                (session_id,), "AgentSession",
            )
            if session["status"] == "stopped":
                return session
            if session["status"] != "running":
                raise Conflict(f"정리할 수 없는 AgentSession 상태: {session['status']}")
            latest = connection.execute(
                "SELECT id FROM dispatches WHERE task_id=? ORDER BY attempt DESC LIMIT 1",
                (session["task_id"],),
            ).fetchone()
            if latest is None or latest["id"] != session["dispatch_id"]:
                raise StaleDispatch(f"오래된 Dispatch의 결과입니다: {session['dispatch_id']}")
            session_status = "failed" if failed else "idle"
            dispatch_status = "failed" if failed else "needs_you"
            task_status = "failed" if failed else "needs_you"
            task = self._one(
                connection, "SELECT workflow_stage FROM tasks WHERE id=?",
                (session["task_id"],), "Task",
            )
            attention_reason = None if failed else (
                "mockup_review" if task["workflow_stage"] == "mockup"
                else "conversation_idle"
            )
            connection.execute(
                "UPDATE agent_sessions SET status=?,error=?,updated_at=?,"
                "stopped_at=CASE WHEN ?='failed' THEN ? ELSE NULL END WHERE id=?",
                (session_status, error, now, session_status, now, session_id),
            )
            connection.execute(
                "UPDATE dispatches SET status=?,error=?,"
                "ended_at=CASE WHEN ?='failed' THEN ? ELSE NULL END WHERE id=?",
                (dispatch_status, error, dispatch_status, now, session["dispatch_id"]),
            )
            connection.execute(
                "UPDATE tasks SET status=?,attention_reason=?,updated_at=? WHERE id=?",
                (task_status, attention_reason, now, session["task_id"]),
            )
        return self.get_session(session_id)

    def stop_execution(self, session_id: str, *, reason: str = "stopped by user") -> dict:
        now = _now()
        with self.transaction(immediate=True) as connection:
            session = self._one(
                connection, "SELECT * FROM agent_sessions WHERE id=?",
                (session_id,), "AgentSession",
            )
            latest = connection.execute(
                "SELECT id FROM dispatches WHERE task_id=? ORDER BY attempt DESC LIMIT 1",
                (session["task_id"],),
            ).fetchone()
            if latest is None or latest["id"] != session["dispatch_id"]:
                raise StaleDispatch(f"오래된 Dispatch의 Session입니다: {session['dispatch_id']}")
            if session["status"] not in {"created", "running", "idle"}:
                raise Conflict(f"중지할 수 없는 AgentSession 상태: {session['status']}")
            connection.execute(
                "UPDATE agent_sessions SET status='stopped',error=?,stopped_at=?,updated_at=? "
                "WHERE id=?", (reason, now, now, session_id),
            )
            connection.execute(
                "UPDATE dispatches SET status='cancelled',error=?,ended_at=? WHERE id=?",
                (reason, now, session["dispatch_id"]),
            )
            connection.execute(
                "UPDATE tasks SET status='todo',attention_reason=NULL,updated_at=? WHERE id=?",
                (now, session["task_id"]),
            )
        return self.get_session(session_id)

    def recover_interrupted_runtime(self) -> dict[str, int]:
        """Make process-crash `running` rows explicitly resumable on server restart."""
        now = _now()
        with self.transaction(immediate=True) as connection:
            sessions = connection.execute(
                "UPDATE agent_sessions SET status='idle',updated_at=?,"
                "error='server restarted during a turn' WHERE status='running'",
                (now,),
            ).rowcount
            dispatches = connection.execute(
                "UPDATE dispatches SET status='needs_you',"
                "error='server restarted during a turn' WHERE status='running'",
            ).rowcount
            tasks = connection.execute(
                "UPDATE tasks SET status='needs_you',updated_at=? WHERE status='working' "
                "AND EXISTS (SELECT 1 FROM dispatches d WHERE d.task_id=tasks.id "
                "AND d.status='needs_you')",
                (now,),
            ).rowcount
            verifications = connection.execute(
                "UPDATE verification_runs SET status='error',"
                "error='server restarted during verification',ended_at=? "
                "WHERE status IN ('queued','running')", (now,),
            ).rowcount
            evaluations = connection.execute(
                "UPDATE evaluation_experiments SET status='failed',"
                "error='server restarted during evaluation',ended_at=? "
                "WHERE status IN ('queued','running')", (now,),
            ).rowcount
            terminals = connection.execute(
                "UPDATE task_terminals SET state='stopped',pid=NULL,"
                "error='server restarted; terminal process is no longer attached',"
                "updated_at=?,ended_at=? WHERE state='running'", (now, now),
            ).rowcount
            workspaces = connection.execute(
                "UPDATE workspaces SET state='failed',progress='interrupted',"
                "error='server restarted during workspace preparation',updated_at=? "
                "WHERE state='preparing'", (now,),
            ).rowcount
            preparing_tasks = connection.execute(
                "UPDATE tasks SET status='failed',updated_at=? WHERE status='preparing' "
                "AND EXISTS (SELECT 1 FROM workspaces w WHERE w.task_id=tasks.id "
                "AND w.state='failed' AND w.progress='interrupted')", (now,),
            ).rowcount
        return {
            "sessions": sessions, "dispatches": dispatches, "tasks": tasks,
            "verifications": verifications, "evaluations": evaluations,
            "terminals": terminals, "workspaces": workspaces,
            "preparing_tasks": preparing_tasks,
        }

    def transition_session(
        self, session_id: str, target: str, *, error: str | None = None,
    ) -> dict:
        if target not in SESSION_STATUSES:
            raise InvalidTransition(f"모르는 AgentSession 상태: {target}")
        with self.transaction(immediate=True) as connection:
            current = self._one(
                connection, "SELECT * FROM agent_sessions WHERE id=?", (session_id,), "AgentSession"
            )
            source = current["status"]
            if target != source and target not in SESSION_TRANSITIONS[source]:
                raise InvalidTransition(f"AgentSession 상태 전이 불가: {source} -> {target}")
            stopped = _now() if target in {"stopped", "failed"} else None
            connection.execute(
                "UPDATE agent_sessions SET status=?,error=?,stopped_at=?,updated_at=? WHERE id=?",
                (target, error, stopped, _now(), session_id),
            )
        return self.get_session(session_id)

    def append_session_event(
        self, session_id: str, *, kind: str, payload: dict,
        task_id: str, dispatch_id: str, workspace_id: str | None,
        require_latest: bool = False, require_active: bool = True,
    ) -> dict:
        with self.transaction(immediate=True) as connection:
            session = self._one(
                connection, "SELECT * FROM agent_sessions WHERE id=?",
                (session_id,), "AgentSession",
            )
            if session["task_id"] != task_id or session["dispatch_id"] != dispatch_id:
                raise Conflict("Session event의 Task/Dispatch 귀속이 Session과 다릅니다")
            if require_latest:
                latest = connection.execute(
                    "SELECT id,status FROM dispatches WHERE task_id=? ORDER BY attempt DESC LIMIT 1",
                    (task_id,),
                ).fetchone()
                if latest is None or latest["id"] != dispatch_id or (
                    require_active
                    and latest["status"] not in {"queued", "running", "needs_you"}
                ):
                    raise StaleDispatch(f"오래된 Dispatch 이벤트입니다: {dispatch_id}")
            seq = int(connection.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM session_events WHERE session_id=?",
                (session_id,),
            ).fetchone()[0])
            created_at = _now()
            connection.execute(
                "INSERT INTO session_events(session_id,seq,kind,payload_json,task_id,dispatch_id,"
                "workspace_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    session_id, seq, kind, _json(payload), task_id, dispatch_id,
                    workspace_id, created_at,
                ),
            )
        return {
            "session_id": session_id, "seq": seq, "kind": kind, "payload": payload,
            "task_id": task_id, "dispatch_id": dispatch_id,
            "workspace_id": workspace_id, "created_at": created_at,
        }

    def list_session_events(self, session_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM session_events WHERE session_id=? ORDER BY seq", (session_id,)
            )
            events = []
            for row in rows:
                item = dict(row)
                payload_json = item.pop("payload_json")
                events.append({**item, "payload": json.loads(payload_json)})
            return events
