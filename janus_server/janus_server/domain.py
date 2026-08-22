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

CURRENT_SCHEMA_VERSION = 8

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

MIGRATIONS = {
    1: MIGRATION_1, 2: MIGRATION_2, 3: MIGRATION_3, 4: MIGRATION_4,
    5: MIGRATION_5, 6: MIGRATION_6, 7: MIGRATION_7, 8: MIGRATION_8,
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
                    _json(["read_file", "glob", "grep", "write_file", "edit_file"]),
                    "ask", "autonomous", 15, "model_qwen38_27b_4bit",
                    _json(normalize_budget(None, max_steps=15)), now, now,
                ),
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
        try:
            with self.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO projects(id,name,repo_path,created_at,updated_at) VALUES (?,?,?,?,?)",
                    (project_id, name.strip(), path, now, now),
                )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"Project 생성 충돌: {error}") from error
        return self.get_project(project_id)

    def get_project(self, project_id: str) -> dict:
        with self._connect() as connection:
            return self._one(connection, "SELECT * FROM projects WHERE id=?", (project_id,), "Project")

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
        acceptance_command: str, base_ref: str, task_id: str | None = None,
    ) -> dict:
        now = _now()
        task_id = task_id or _id("task")
        try:
            with self.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO tasks(id,project_id,title,objective,acceptance_command,base_ref,"
                    "status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        task_id, project_id, title.strip(), objective.strip(),
                        acceptance_command.strip(), base_ref.strip(), "todo", now, now,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise Conflict(f"Task 생성 충돌: {error}") from error
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
                "UPDATE tasks SET status=?,updated_at=? WHERE id=?",
                (target, _now(), task_id),
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
        budget: dict | None = None,
    ) -> dict:
        now = _now()
        profile_id = profile_id or _id("agent")
        try:
            with self.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO agent_profiles(id,name,description,system_prompt,tools_json,approval,"
                    "worker_policy,max_steps,model_profile_id,budget_json,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        profile_id, name, description, system_prompt, _json(tools), approval,
                        worker_policy, max_steps, model_profile_id,
                        _json(normalize_budget(budget, max_steps=max_steps)), now, now,
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
                    "objective_snapshot,acceptance_snapshot,budget_json,usage_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        dispatch_id, task_id, workspace_id, agent_profile_id, attempt, "queued",
                        task["objective"], task["acceptance_command"], profile["budget_json"],
                        _json(empty_usage()), now,
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
                    "adaptive_decision_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        dispatch_id, task_id, workspace_id, agent_profile_id, attempt, "queued",
                        task["objective"], task["acceptance_command"],
                        _json(dispatch_budget),
                        _json(empty_usage()), _json(adaptive_decision or {}), now,
                    ),
                )
                connection.execute(
                    "INSERT INTO agent_sessions(id,task_id,dispatch_id,agent_profile_id,status,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (session_id, task_id, dispatch_id, agent_profile_id, "created", now, now),
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
        except sqlite3.IntegrityError as error:
            raise Conflict(f"AgentSession 생성 충돌: {error}") from error
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict:
        with self._connect() as connection:
            return self._one(
                connection, "SELECT * FROM agent_sessions WHERE id=?", (session_id,), "AgentSession"
            )

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
                "UPDATE tasks SET status='working',updated_at=? WHERE id=?",
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
                "UPDATE tasks SET status=?,updated_at=? WHERE id=?",
                (task_status, now, session["task_id"]),
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
                "UPDATE tasks SET status='todo',updated_at=? WHERE id=?",
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
        return {
            "sessions": sessions, "dispatches": dispatches, "tasks": tasks,
            "verifications": verifications, "evaluations": evaluations,
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
