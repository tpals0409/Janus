# Janus data recovery policy

Janus never resets its SQLite database automatically. A migration mismatch,
integrity failure, disk-full error, or interrupted write must remain visible and
preserve the last committed data.

## Backup

`POST /maintenance/backups` creates an online SQLite backup, runs
`PRAGMA integrity_check`, publishes it with an atomic rename and `0600`
permissions, and retains the newest five files by default. The retention can be
set from 1 to 50 with `{"retain": 10}`. `GET /maintenance/recovery` reports the
live database integrity, available backups, and this policy. The default backup
directory is `~/.janus/backups`; `JANUS_BACKUPS_DIR` can override it.

## Restore or reset

1. Stop Janus so no process owns the database or its WAL files.
2. Preserve `~/.janus/janus.sqlite3`, `-wal`, and `-shm` by moving them together
   to a dated recovery directory. Do not delete them.
3. For restore, copy a verified backup to `~/.janus/janus.sqlite3` with mode
   `0600`. For a deliberate reset, leave that path absent so Janus creates a new
   schema on the next start.
4. Start Janus and confirm `GET /maintenance/recovery` returns
   `database.ok: true` and the expected schema version from `/health`.
5. Keep the preserved database until Tasks, workspaces, sessions, and profiles
   have been checked. A reset is complete only after this verification.

Workspace directories and Git branches are not database backups. Resetting the
database does not delete either; reconcile or archive them explicitly after the
database decision.

## Soak verification

The release soak repeatedly creates an active Task, simulates a process crash,
reopens SQLite, runs recovery, periodically creates an online backup, and checks
that no running session, dispatch, or preparing workspace leaks remain:

```bash
cd janus_server
uv run python scripts/robustness_soak.py
```

The default is 30 minutes and at least 100 cycles. A short smoke uses the same
loop with `--duration-seconds 3 --minimum-cycles 100`.
