# Contributing

Thanks for looking. Janus is a one-person project, so a short issue before a
large change will save you time — I may already be mid-rewrite of the area you
are about to touch.

## What you need

Everything in [README.md § Supported machine](README.md#supported-machine). The
short version: Apple Silicon macOS, `uv`, Node 22+, pnpm 11, Xcode Command Line
Tools. Check without changing anything:

```bash
zsh scripts/bootstrap_macos.sh --check-only
```

You do **not** need the 17 GB local model to work on most of the codebase. The
backend, the renderer, and the Electron main process all run and test without it
— CI proves this by running the full suite on Linux. You need the model only to
exercise the local agent end to end.

## The loop

```bash
zsh scripts/bootstrap_macos.sh        # locked installs, no model

cd janus_server && uv run pytest -q   # backend
cd ../janus && pnpm test              # main process + renderer
./node_modules/.bin/tsc --noEmit
```

Before opening a PR, run what CI runs:

```bash
python3 scripts/check_versions.py
cd janus_server && uv run pytest -q
cd ../janus && pnpm test && ./node_modules/.bin/tsc --noEmit && pnpm check:bundle
pnpm check:notices
pnpm audit --audit-level high
```

`pnpm check:bundle` enforces a size budget on the renderer chunk, and
`check:notices` fails if `THIRD-PARTY-NOTICES.md` is stale after a dependency
change — regenerate with `pnpm notices`.

A macOS job packages the app and resolves the MLX lockfile, so those no longer
depend on your machine alone. The model server itself is still never exercised in
CI — it needs the 17 GB weights. If you touch the model runtime, launch the
packaged app and run a real turn before you open the PR.

## Layout

| Path | What lives there |
|---|---|
| `janus/src/main/` | Electron main — process supervision, model runtime, IPC |
| `janus/src/renderer/` | React UI, Zustand store |
| `janus_server/janus_server/` | FastAPI backend, agent runtime, tools, domain store |
| `janus_server/janus_server/routers/` | HTTP + WebSocket surface |
| `qwen3.8mlx/` | MLX model runtime — dependency manifest, shipped with the app |
| `scripts/`, `janus_server/scripts/` | Bootstrap, release gates, benchmark harness |

## House rules

These are what I actually apply when reading a diff.

- **Fix the cause, not the symptom.** If a bug reaches three callers, the guard
  goes in the shared function, not in three places.
- **Leave a runnable check.** Non-trivial logic gets one test that fails if the
  logic breaks. Framework-free `assert` self-checks are fine for small modules —
  `janus_server/janus_server/tools.py` has one.
- **Comments explain why, not what.** The codebase is dense with rationale for
  non-obvious decisions; match that, and skip comments that restate the code.
- **Do not weaken a test to make a change pass.** If a test is wrong, say so in
  the PR and fix it deliberately. Several tests here once encoded a regression as
  correct behaviour; that is the failure mode to avoid.
- **Docs are load-bearing.** `janus_server/tests/test_docs_match_code.py` fails
  when documentation contradicts the code. If you change a default, a provider,
  or a safety boundary, the docs change in the same commit.
- **Comments and docs in Korean, README and the public-facing files in English.**
  Match the file you are editing.

## Database changes

The SQLite schema is forward-only. A migration is appended to `MIGRATIONS` in
`janus_server/janus_server/domain.py` with the next integer, and
`CURRENT_SCHEMA_VERSION` moves with it. Migrations never rewrite user data in a
way that cannot be recovered — `MIGRATION_24` did, and it took a week to notice.

## Releases

Three version locations must agree — `janus/package.json`,
`janus_server/pyproject.toml`, `janus_server/janus_server/version.py` — and CI
enforces it. Full policy in [VERSIONING.md](VERSIONING.md).

## Conduct

[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) applies to issues, pull requests, and
every other space this project occupies.

## Security

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).

## License

Contributions are accepted under [Apache-2.0](LICENSE), the project's license.
