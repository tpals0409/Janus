# Janus v1 completion audit

Audit date: 2026-08-23. A checked condition below had current code/test or
committed real-model artifact evidence at that date; historical intent alone was
not accepted.

> **This is a dated snapshot, not a description of the current build.** Rows
> marked **Withdrawn** or carrying a **Correction** were falsified by later
> changes — chiefly v1.0.21 (subscription CLI providers) and v1.0.28 (per-Task
> worktree isolation removed). Read `README.md` for what ships today.

| v1 condition | Verdict | Authoritative evidence |
|---|---|---|
| Actual 27B TaskSuite is repeatable | Proven | Committed Qwen3.8-27B 4-bit MLX TaskSuite: 3 fixtures × 3 policies × 5 repeats, 45 runs, 44 acceptance passes; `scripts/audit_v1.py` verifies conditions and run count. The committed real-model smoke passes multi-turn, worker spawn/stop, cancel/resume. |
| Two Tasks cannot cross-contaminate worktrees | **Withdrawn 2026-08-28** | Per-Task worktree isolation was removed in v1.0.28; Tasks in one project share the repository checkout. `WorkspaceContext` still jails file tools to the workspace root, but that root is now the same for every Task in a project. Run Tasks in a project one at a time. |
| Model/tools obey scheduler and budget | Proven | Scheduler and budget suites verify per-resource caps, queue priority/aging, independent verification overlap, timeout/cancel/exception release, Dispatch/worker token-time-step caps, worker caps, and cross-Task budget isolation. The focused v1 gate passed 56 tests. |
| Task → diff review → commit works in app | Proven | The Task API/UI route and E2E test create separate Task changes, derive revision, run independent verification, accept that revision, and commit it. **Correction 2026-08-28:** the commit lands on the repository's current branch, not a Task branch — `main` is no longer left unchanged. P5 adds push, PR/CI, terminal/editor/browser surfaces. |
| Worker efficiency is quantified against baseline | Proven | R1 fixed-one: 14/15, 109.93 s, 14,855 prompt and 1,227.9 completion tokens, 1.93 approvals. Final fixed-one: 14/15, 88.14 s, 10,993.1 prompt and 777.4 completion tokens, 1.67 approvals: same acceptance with about 19.8% less wall time, 26.0% fewer prompt tokens, and 36.7% fewer completion tokens. The scheduler candidate regression is retained rather than hidden. |
| Failure/cancel/restart leaves no workspace, lease, or process orphan | Proven | Real-model smoke records owned model orphan count 0. Scheduler shutdown waits for active and queued leases; restart recovery settles sessions, dispatches, verification, evaluation, terminals, and workspace preparation. The 281-cycle recovery soak ended with all transient counts 0 and SQLite integrity `ok`. |
| No known P0 security, data-loss, or false-status defect | Proven within audited scope | The suite passed at the time of this audit (138 tests). It has since roughly doubled — see CI for the current count. HTTP/WS token+Origin, default-deny approvals, workspace jail, stale Dispatch, atomic writes, migration refusal, backup integrity, diagnostic redaction, and honest failed/interrupted states are covered. Production `pnpm audit` and pinned backend/model `pip-audit` report no known vulnerabilities. |
| Core value works without an external model | Proven | **Correction 2026-08-28:** the core accepts three providers (`local`, `claude_code`, `codex`). The claim still holds for the *local* path: runtime resolves a local snapshot and talks only to loopback MLX, and remote model IDs are refused. The subscription paths drive the user's own already-installed CLI and never receive an API key from Janus. Clean install, Task lifecycle, worktree isolation, verification/review/ship, development surface, evaluation, supervision, backup, and diagnostics do not require an external model. |

Former non-blocking boundary, now closed: the human-operated, real-27B UI
session walking from Task creation through ship was completed by the user on
2026-08-23. The release gate never substituted it for the two independently
proven requirements above (real-27B runtime/TaskSuite behavior and the app's
revision-aware Task review/commit path); it now stands as its own completed
UX acceptance.
