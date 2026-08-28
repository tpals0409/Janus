# Security Policy

## Reporting a vulnerability

Report privately through
[GitHub Security Advisories](https://github.com/tpals0409/Janus/security/advisories/new).
Please do not open a public issue for a security problem.

Include what you did, what happened, and what you expected. A proof of concept
helps but is not required. This is a one-person project, so expect a first reply
within about a week rather than within hours.

## Supported versions

Only the latest tagged release is supported. There are no backports.

## What Janus does on your machine

Janus is a desktop application that runs a coding agent against your own
repositories. Understanding its actual privileges matters more than any promise
this file could make.

- **It edits your repository directly.** Janus works in the repository you
  select, on whatever branch it is currently on. There is no scratch copy and no
  separate branch. Git is the only undo mechanism, and it does not cover
  uncommitted or untracked work.
- **It runs shell commands.** With the local model, each `run_bash` call requires
  your approval by default. Approval is remembered per workspace once you grant
  it. With a subscription CLI, shell runs without a per-command prompt.
- **It spawns subprocesses** — the model server, `uv`, `git`, PTY shells, and
  (when selected) the `claude` or `codex` CLI you already have installed.
- **It serves a local HTTP and WebSocket API** on `127.0.0.1:8765`, authenticated
  with a token minted fresh on each app launch and handed only to the app window.
  Requests are also Origin-checked.
- **It stores data locally** — SQLite under the app's user-data directory. Your
  repository and its Git history are never copied into it.

## Boundaries that exist

- **Path jail.** File tools resolve every path against the workspace root,
  follow symlinks, and refuse anything outside it. Subscription CLIs are confined
  by `--restricted` (Claude Code) or the sandbox mode (Codex).
- **Default-deny tool approval.** `write_file`, `edit_file`, `run_bash`, and
  `http_get` require approval on the local path. A missing or failing approval
  callback is treated as a refusal, and no response within 300 seconds is a
  refusal.
- **Tool scoping.** An AgentProfile grants a specific tool set. Subscription CLIs
  receive exactly that set — if the profile withholds shell, the CLI has no shell
  tool at all.
- **Review and ship gates.** Committing through the ship flow requires an accepted
  review at the current revision with all verification runs passing. Pushing
  requires a Janus-recorded commit matching HEAD and an explicit SHA confirmation.
- **Skill imports are inert data.** Imported `SKILL.md` trees are compiled, never
  executed; embedded scripts and hooks do not run.

## Boundaries that do not exist

Stated plainly, because assuming otherwise is how people get hurt:

- **`run_bash` is not path-jailed.** Only its working directory is set. A command
  can `cd` anywhere the user can. Approval is the only barrier, and the
  subscription path has no approval.
- **There is no OS-level sandbox.** The agent runs with your user's privileges.
  Janus's jail is application-level, not a security boundary against deliberate
  escape.
- **Subscription CLIs do not ask before each action.** Their scope is enforced;
  individual writes and commands are not gated.
- **Tasks are not isolated from each other.** Two Tasks in the same project share
  one working tree.
- **`PUT /tasks/{id}/development/file`** writes into the workspace with no
  approval, lease, or budget. It is the human editor's path and is intended, but
  it is another door into the same directory.
- **The workspace file jail is not a multi-tenant boundary.** Janus assumes a
  single trusted local user.

## Reasonable use

Point Janus at repositories you can afford to have modified, commit or stash
before delegating, and read the diff before you ship. If you are evaluating it,
use a scratch branch.
