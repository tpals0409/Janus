---
name: verification-before-completion
description: Require fresh command or inspection evidence before claiming a coding task is complete. Use at the completion boundary, not during read-only explanation.
---

# Verification Before Completion

Do not infer success from code appearance or a worker's completion claim.

## Verify

For each `done_when` criterion:

1. Select the narrowest command or inspection that proves it.
2. Run it against the current workspace state.
3. Record the exit status and relevant result.
4. Distinguish task failures from unrelated pre-existing failures.

Also inspect the final diff for:

- changes outside `allowed_scope`
- violations of `do_not`
- unrequested dependencies or abstractions
- missing user approval where the workflow requires it

## Decision

- Return `pass` only when fresh evidence supports every criterion.
- Return `fail` with the exact command, file, or behavior that failed.
- If verification cannot run, return `blocked`; do not substitute confidence for evidence.
- Do not edit or repair code when acting as an independent Verifier.

