# Janus

Janus is a local-first Agent Development Environment (ADE) for getting the most
verified work out of a coding agent on your own machine. It turns requests into
Tasks, runs an orchestrator with bounded sub-agents, and keeps the diff,
verification, review, commit, push, pull request, terminal, editor, and preview
context attached to the same Task.

**Two ways to run a Task, and you pick per message:**

- **A local model** — a Qwen3.8 27B MLX build served on your machine. Nothing
  leaves the laptop. This is the default and the reason the project exists.
- **A subscription CLI** — your own `claude` or `codex` login, driven headlessly.
  Useful when the local model is too slow for the job, or before you have
  downloaded 17 GB of weights.

The two are not equivalent, and the difference matters before you delegate
anything — see [Where the agent writes](#where-the-agent-writes).

GitHub integration is optional and uses an already authenticated `gh` CLI when a
Task is ready to ship.

## Where the agent writes

**Janus works directly in the repository you select, on whatever branch it is
currently on.** There is no scratch copy. When you delegate a Task, the agent
edits your working tree, and commit/push go to that same branch.

That is a deliberate trade — it keeps the model close to your real state and
makes `git` the single undo mechanism — but it has edges worth knowing:

| | Local model | Subscription CLI |
|---|---|---|
| Where it runs | your checkout, current branch | same |
| Path confinement | file tools jailed to the repo | jailed by `--restricted` / sandbox |
| Tools available | exactly what the AgentProfile grants | same, derived from the profile |
| Asks before each write or shell command | **yes**, default-deny | **no** |
| Shell can leave the repo (`cd ..`) | approval is the only barrier | no barrier |

Practical consequences:

- **Commit or stash before delegating.** Uncommitted edits of your own are not
  recoverable from git if the agent touches the same files.
- **Two Tasks in one project share one working tree.** Run them one at a time.
- Prefer a scratch branch if you are trying Janus out on something you care about.

## Supported machine

- Apple Silicon macOS (`arm64`)
- Python 3.13 through [uv](https://docs.astral.sh/uv/)
- Node.js 22 or newer and pnpm 11
- Git; optional `gh` for pull requests and CI checks
- Xcode Command Line Tools (`swift`), required by `pnpm package:mac`
- For the local model: roughly 17 GB of disk for weights plus 8 GB free headroom,
  and enough unified memory to hold a 27B 4-bit model (32 GB is a practical floor).
  A subscription CLI needs none of this.

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

**The terminal is not required for this step.** Start Janus and open
**Settings › 로컬 모델**. It reports which snapshots are present, weighs the
download against your free disk, and fetches them with visible progress.
Cancelling is safe — resuming picks up where it stopped.

To prepare it ahead of time instead:

```bash
zsh scripts/bootstrap_macos.sh --with-model
```

Either path fetches `mlx-community/Qwen3.8-27B-4bit` plus the
`mlx-community/Qwen3.8-27B-MTP-4bit` speculative drafter (~17 GB together). The
drafter is required under the default MTP policy; lower that policy in Settings to
run without it. Janus resolves the local snapshot path and refuses to pass a remote
model ID to the MLX server, preventing an accidental full-repository download.

Settings also offers `orcarouter/Qwen3.8-27B-Uncensored-MLX` as an advanced
alternative. It carries the base model's Apache-2.0 license, but its model card
scopes it to research and excludes end-user deployment without your own moderation
layer — the app repeats that warning where you select it.

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
3. Prepare the workspace — Janus validates the repository and base ref — and
   choose an AgentProfile (local model, Claude Code, or Codex).
4. Start or resume the session. Concurrent model generations are capped (3 by
   default, configurable in Settings) while tool and verification work overlaps.
5. Inspect the Git-derived diff, run verification, review the exact revision,
   then commit and optionally push/create a pull request.
6. Use the Task development surface for split terminals, Monaco editing, local
   preview, console/network capture, screenshots, and DOM/CSS context.

## Data, recovery, and diagnostics

Persistent data defaults to `~/.janus`; your repository and its Git history are
never stored inside it. Janus never automatically resets data. Backup, restore,
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
python3 scripts/check_versions.py

cd janus_server
uv run pytest -q

cd ../janus
pnpm test          # main process + renderer
npx tsc --noEmit
pnpm build
pnpm package:mac
```

CI runs the same suites plus `pnpm check:bundle` (a bundle-size budget) and a
separate dependency-audit job, so a local pass is necessary but not sufficient.
A macOS job packages the app, resolves the MLX runtime lockfile, and checks that
the packaged bundle carries the backend and the license notices. What no CI job
covers is the model server actually generating — that needs the 17 GB weights and
is only ever exercised on a developer's own Mac.

The clean-install smoke copies only distributable source into a temporary
directory, installs from both lockfiles, builds/packages the app, boots a blank
backend data directory, creates a verified backup and redacted diagnostics, and
checks process shutdown:

```bash
python3 scripts/fresh_install_smoke.py
```

## Status and scope

Janus is a young project — one author, and the design notes in
[PRODUCT.md](PRODUCT.md) carry more history than the code does. Read them as
intent, not as a specification of what ships today. [ROADMAP.md](ROADMAP.md),
[CHECKLIST.md](CHECKLIST.md), and [STATUS.md](STATUS.md) are working documents
kept for the record; `STATUS.md` in particular is a dated engineering journal
rather than a changelog.

## License and security

Apache-2.0 — see [LICENSE](LICENSE). The Qwen3.8 27B MLX builds Janus downloads
carry their own terms; the advanced (uncensored) option in particular scopes
itself to research in its model card.

To report a vulnerability, see [SECURITY.md](SECURITY.md), which also states
plainly what Janus can do on your machine and which boundaries do *not* exist.
