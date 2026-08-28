#!/bin/zsh
set -euo pipefail

ROOT="${0:A:h:h}"
WITH_MODEL=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --with-model) WITH_MODEL=1 ;;
    --check-only) CHECK_ONLY=1 ;;
    *) print -u2 "unknown option: $arg"; exit 2 ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  print -u2 "Janus local 27B v1 requires Apple Silicon macOS."
  exit 1
fi
for command_name in git uv node pnpm; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    print -u2 "missing $command_name; see README.md prerequisites"
    exit 1
  fi
done

NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
if (( NODE_MAJOR < 22 )); then
  print -u2 "Node.js 22 or newer is required (found $(node --version))."
  exit 1
fi

# package.json pins pnpm via "packageManager"; a mismatched major fails on the
# lockfile long after this script says everything is fine.
PNPM_WANT="${$(grep -o '"packageManager": *"pnpm@[0-9]*' "$ROOT/janus/package.json")##*@}"
PNPM_HAVE="${$(pnpm --version)%%.*}"
if [[ -n "$PNPM_WANT" ]] && (( PNPM_HAVE < PNPM_WANT )); then
  print -u2 "pnpm $PNPM_WANT or newer is required (found $(pnpm --version))."
  print -u2 "  corepack enable && corepack prepare pnpm@latest --activate"
  exit 1
fi

# `pnpm package:mac` shells out to swift for the app icon. Without Xcode Command
# Line Tools the whole packaging step dies with a confusing error, and a
# --check-only run would have reported success.
if ! command -v swift >/dev/null 2>&1; then
  print -u2 "missing swift (Xcode Command Line Tools); required by 'pnpm package:mac'"
  print -u2 "  xcode-select --install"
  exit 1
fi

print "platform: $(uname -m) $(sw_vers -productVersion)"
print "uv: $(uv --version)"
print "node: $(node --version), pnpm: $(pnpm --version)"
print "swift: $(swift --version 2>&1 | head -1)"
if (( CHECK_ONLY )); then
  exit 0
fi

(cd "$ROOT/janus_server" && uv sync --frozen)
(cd "$ROOT/qwen3.8mlx" && uv sync --frozen)
(cd "$ROOT/janus" && pnpm install --frozen-lockfile)

if (( WITH_MODEL )); then
  (cd "$ROOT/qwen3.8mlx" && uv run --frozen hf download mlx-community/Qwen3.8-27B-4bit)
  # The MTP drafter is not optional under the default policy (required): without it
  # the model server refuses to start. Lower the policy in Settings to run without it.
  (cd "$ROOT/qwen3.8mlx" && uv run --frozen hf download mlx-community/Qwen3.8-27B-MTP-4bit)
else
  print "model download skipped — Janus downloads it for you in Settings > 로컬 모델,"
  print "or rerun with --with-model (~17 GB plus working memory)."
fi
print "bootstrap complete"
