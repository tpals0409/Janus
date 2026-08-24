---
name: codebase-recon
description: Find the cause, relevant code, and smallest implementation boundary in an unfamiliar repository. Use for scoped investigation; do not use to produce a comprehensive architecture survey.
---

# Codebase Recon

Gather only enough evidence for the next decision.

## Investigate

- Start from exact symbols, error text, routes, commands, or UI labels in the request.
- Trace the nearest entry point and direct callers or consumers.
- Read repository instructions and the tests closest to the behavior.
- Reproduce the issue with a non-mutating command when practical.
- Use history only when current code cannot explain the behavior.

## Stop conditions

Stop when you can provide:

- the likely cause with concrete evidence
- no more than five primary files or components relevant to the change
- the smallest plausible implementation boundary
- the most relevant verification command
- any uncertainty that still blocks implementation

Do not edit files, map unrelated subsystems, or continue searching for optional improvements.

