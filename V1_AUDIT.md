# Janus v1 completion audit

Audit date: 2026-08-23. A checked condition below has current code/test or
committed real-model artifact evidence; historical intent alone is not accepted.

| v1 condition | Verdict | Authoritative evidence |
|---|---|---|
| Actual 27B TaskSuite is repeatable | Proven | Committed Qwen3.8-27B 4-bit MLX TaskSuite: 3 fixtures × 3 policies × 5 repeats, 45 runs, 44 acceptance passes; `scripts/audit_v1.py` verifies conditions and run count. The committed real-model smoke passes multi-turn, worker spawn/stop, cancel/resume. |
| Two Tasks cannot cross-contaminate worktrees | Proven | `test_two_parallel_contexts_isolate_same_relative_path` and `test_two_task_e2e_changes_and_commits_remain_isolated` verify same relative paths, Git-derived changes, verification, commits, branches, and unchanged main checkout. |
| Model/tools obey scheduler and budget | Proven | Scheduler and budget suites verify per-resource caps, queue priority/aging, independent verification overlap, timeout/cancel/exception release, Dispatch/worker token-time-step caps, worker caps, and cross-Task budget isolation. The focused v1 gate passed 56 tests. |
| Task → diff review → commit works in app | Proven | The Task API/UI route and E2E test create separate Task changes, derive revision, run independent verification, accept that revision, and commit it while main remains unchanged. P5 adds push, PR/CI, terminal/editor/browser surfaces. |
| Worker efficiency is quantified against baseline | Proven | R1 fixed-one: 14/15, 109.93 s, 14,855 prompt and 1,227.9 completion tokens, 1.93 approvals. Final fixed-one: 14/15, 88.14 s, 10,993.1 prompt and 777.4 completion tokens, 1.67 approvals: same acceptance with about 19.8% less wall time, 26.0% fewer prompt tokens, and 36.7% fewer completion tokens. The scheduler candidate regression is retained rather than hidden. |
| Failure/cancel/restart leaves no workspace, lease, or process orphan | Proven | Real-model smoke records owned model orphan count 0. Scheduler shutdown waits for active and queued leases; restart recovery settles sessions, dispatches, verification, evaluation, terminals, and workspace preparation. The 281-cycle recovery soak ended with all transient counts 0 and SQLite integrity `ok`. |
| No known P0 security, data-loss, or false-status defect | Proven within audited scope | Full 138-test suite plus the 56-test focused security/recovery gate pass. HTTP/WS token+Origin, default-deny approvals, workspace jail, stale Dispatch, atomic writes, migration refusal, backup integrity, diagnostic redaction, and honest failed/interrupted states are covered. Production `pnpm audit` and pinned backend/model `pip-audit` report no known vulnerabilities. |
| Core value works without an external model | Proven | The only model profile/provider accepted by the core is local. Runtime resolves a local snapshot and talks only to loopback MLX; remote model IDs are refused. Clean install, Task lifecycle, worktree isolation, verification/review/ship, development surface, evaluation, supervision, backup, and diagnostics do not require an external model. |

Known non-blocking boundary: a human-operated, real-27B UI session that manually
walks from Task creation through ship remains a separate UX acceptance exercise.
The release gate does not substitute it for either of the two independently
proven requirements above: real-27B runtime/TaskSuite behavior and the app's
revision-aware Task review/commit path.
