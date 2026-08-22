# Version and update policy

Janus uses semantic versioning for the desktop app and backend API. The Electron
`package.json`, Python `pyproject.toml`, and `janus_server.version.__version__`
must match; CI tests this invariant. The SQLite schema has its own monotonic
integer version because app and data compatibility do not map one-to-one.

## Compatibility

- Patch releases may fix behavior without removing API or persisted fields.
- Minor releases may add compatible API fields and forward-only SQLite
  migrations. Migration from every prior schema must be tested.
- A database newer than the running app, or with a discontinuous migration
  history, is rejected before mutation. Downgrade is restore-from-backup, not a
  reverse migration.
- Every release migration requires a verified online backup first. Janus never
  silently resets or deletes worktrees, branches, Tasks, or databases.
- Major releases may change product contracts only with an explicit migration
  guide and a retained export/restore path.

## Distribution and signing

`pnpm package:mac` creates an unsigned Apple Silicon `Janus.app` for local release
verification. It bundles app code, backend source, and locked dependency metadata;
models, secrets, user data, and virtual environments are excluded.

A public build must be signed with a Developer ID certificate, hardened runtime,
and notarized before distribution. Unsigned artifacts are never presented as a
trusted public update. Until signing and a verified update feed exist, updates are
manual releases: stop Janus, create a database backup, replace the app, start it,
and verify `/health` plus `/maintenance/recovery`.

Automatic background updates are disabled for v1. An updater can be introduced
only after signature verification, rollback behavior, schema compatibility, and
interrupted-download recovery have automated tests.

## Release gate

1. Bump all three product version locations in one commit.
2. Run Python, Node, TypeScript, production build, and `package:mac` checks.
3. Run `scripts/fresh_install_smoke.py` from a clean source copy.
4. Confirm database backup/integrity and diagnostics redaction tests.
5. Audit every v1 completion condition in `CHECKLIST.md`; do not infer completion
   from a narrower green test.
6. Publish checksums with signed/notarized public artifacts when signing begins.
