---
name: task-contract
description: Convert an uncertain or multi-part coding request into a small execution contract before dispatch. Do not use for a clear, bounded change that can be implemented directly.
---

# Task Contract

Produce an execution boundary, not an architecture document.

## Contract

Return these fields:

```yaml
objective: one observable outcome
allowed_scope:
  - only the components, paths, or systems required
do_not:
  - explicit nearby work that is outside the request
done_when:
  - observable acceptance criteria
verification:
  - commands or checks that prove the criteria
workflow: direct | inspect | plan | mockup | operate
```

## Decisions

- Choose `direct` for a clear code change.
- Choose `inspect` when the user asked for explanation, review, or diagnosis only.
- Choose `plan` only when unresolved design choices materially affect the result.
- Choose `mockup` only when the user must judge a visual interaction before implementation.
- Choose `operate` for build, packaging, process, model-runtime, or environment work.

## Constraints

- Preserve the user's wording and intended outcome.
- Keep the contract short enough to review at a glance.
- Prefer existing project patterns and dependencies.
- Do not invent requirements, files, abstractions, or future work.
- Ask the user only when a missing decision would materially change the result.

