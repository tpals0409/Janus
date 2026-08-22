# Janus

Janus is a local-first Agent Development Environment (ADE) for getting the most
verified work from one local model. It turns coding requests into isolated Tasks,
runs a local orchestrator and bounded workers inside Task-owned Git worktrees,
and keeps verification, review, commit, push, pull request, terminal, editor, and
preview context attached to the same Task.

The v1 core does not require an external model. GitHub integration is optional
and uses an already authenticated `gh` CLI when a Task is ready to ship.

## Supported machine

- Apple Silicon macOS (`arm64`)
- Python 3.13 through [uv](https://docs.astral.sh/uv/)
- Node.js 22 or newer and pnpm 11
- Git; optional `gh` for pull requests and CI checks
- Qwen3.8 27B MLX 4-bit model: allow roughly 16 GB for model files and use a
  machine with at least 32 GB unified memory for practical development headroom

Check prerequisites without changing the machine:

```bash
zsh scripts/bootstrap_macos.sh --check-only
```

## Install

Clone the repository, then install locked Python and Node dependencies:

```bash
zsh scripts/bootstrap_macos.sh
```

Model files are deliberately not bundled in the app or downloaded implicitly.
Prepare the required local snapshot when ready:

```bash
zsh scripts/bootstrap_macos.sh --with-model
```

That runs the equivalent locked `hf download` for
`orcarouter/Qwen3.8-27B-Uncensored-MLX` and only its `4-bit/*` files. Janus resolves
the local snapshot path and refuses to pass a remote model ID to the MLX server,
preventing an accidental full-repository download.

## Run and package

For development:

```bash
cd janus
pnpm dev
```

For an unsigned local macOS application:

```bash
cd janus
pnpm package:mac
open dist/mac-arm64/Janus.app
```

The package contains the backend source and both lockfiles, not the model or a
machine-specific virtual environment. On first launch, `uv` creates locked
environments under the Janus user-data directory. The package is intentionally
unsigned for local verification; release signing and update rules are in
[VERSIONING.md](VERSIONING.md).

## First Task

1. Add a Project by selecting an existing Git repository.
2. Create a Task with an objective, acceptance command, and base ref.
3. Prepare its isolated worktree and choose an AgentProfile.
4. Start or resume the local session. Janus serializes the one-slot model while
   allowing bounded tool and verification overlap.
5. Inspect the Git-derived diff, run verification, review the exact revision,
   then commit and optionally push/create a pull request.
6. Use the Task development surface for split terminals, Monaco editing, local
   preview, console/network capture, screenshots, and DOM/CSS context.

## Data, recovery, and diagnostics

Persistent data defaults to `~/.janus`; Task worktrees and Git branches remain
separate from SQLite. Janus never automatically resets data. Backup, restore,
and explicit reset policy is documented in
[janus_server/RECOVERY.md](janus_server/RECOVERY.md).

Create a secret-redacted diagnostic bundle without including the database:

```bash
cd janus_server
uv run python -m janus_server.diagnostics
```

The authenticated app API also exposes `POST /maintenance/diagnostics`,
`POST /maintenance/backups`, and `GET /maintenance/recovery`. Electron-owned
backend and MLX logs live in the platform Janus user-data `logs` directory and
their exact paths appear in backend status.

## Verification

```bash
cd janus_server
uv run pytest -q

cd ../janus
pnpm test:main
npx tsc --noEmit
pnpm build
pnpm package:mac
```

The clean-install smoke copies only distributable source into a temporary
directory, installs from both lockfiles, builds/packages the app, boots a blank
backend data directory, creates a verified backup and redacted diagnostics, and
checks process shutdown:

```bash
python3 scripts/fresh_install_smoke.py
```

Product boundaries and completion evidence live in [PRODUCT.md](PRODUCT.md),
[ROADMAP.md](ROADMAP.md), [CHECKLIST.md](CHECKLIST.md), and [STATUS.md](STATUS.md).
