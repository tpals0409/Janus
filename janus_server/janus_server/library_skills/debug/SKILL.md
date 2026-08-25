---
name: debug
description: Find the root cause of a failure before changing code — reproduce, isolate, then fix where every caller routes through. Use only when the user explicitly invokes /debug.
---

# Debug

A report names a symptom. Find the cause before editing.

## Sequence

1. **Reproduce** — state the exact command or input, what happened, and what was expected. If it cannot be reproduced, say so and ask for the missing piece instead of guessing at a fix.
2. **Isolate** — narrow to the smallest failing unit. Read the real flow end to end: the caller, the function, and what it returns on the failing input. Prefer reading the code over inferring from the error text.
3. **Name the cause** — one sentence: *X happens because Y*. If you cannot write that sentence, keep isolating. Do not patch a symptom you cannot explain.
4. **Choose the fix site** — grep every caller of the function you are about to change. One guard in the shared function is a smaller change than a guard in each caller, and patching only the path the report names leaves every sibling caller broken.
5. **Leave a check** — the smallest runnable thing that fails if the cause comes back. A trivial one-liner needs none.

## Report

Before applying the fix, give the cause, the fix site, and what the fix does not cover. Apply immediately only when the user already asked for the fix rather than the diagnosis.

## Boundaries

- Do not edit files before the cause is stated.
- Do not hide a symptom — widening an exception handler, raising a timeout, adding a retry — and call it fixed. If that is the deliberate choice, say it is a workaround and name what still breaks.
- Do not refactor beyond the fix, and do not rewrite working code you happened to read while isolating.
