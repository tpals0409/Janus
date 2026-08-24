---
name: minimal-patch
description: Implement an approved coding contract with the smallest correct diff. Use for bounded fixes and features; do not use for requested architectural redesigns or broad refactors.
---

# Minimal Patch

Implement only what the approved contract requires.

## Before editing

- Locate the existing behavior and the closest established pattern.
- Identify the smallest set of files likely to change.
- Treat `allowed_scope` and `do_not` as hard boundaries.
- If the task requires leaving that boundary, stop and report why.

## While editing

- Modify existing code before creating new layers or shared abstractions.
- Keep unrelated formatting, cleanup, and naming unchanged.
- Add no dependency unless the contract explicitly requires it and existing code cannot solve the task.
- Do not implement optional improvements discovered during the work.
- Add or update only tests that demonstrate the requested behavior.

## Finish

- Review the diff against every `done_when` item.
- Run the specified verification.
- Fix only failures caused by this patch.
- Stop when the contract is satisfied; report optional ideas without implementing them.

