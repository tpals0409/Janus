---
name: compact
description: Compress the current work into a concise continuation context while preserving decisions, progress, and blockers. Use only when the user explicitly invokes /compact.
---

# Compact

Create a compact continuation context for the current work. Preserve what another turn needs to continue correctly and omit conversational repetition.

## Include

- The current goal and observable completion condition.
- Decisions and user preferences that still affect the result.
- Work completed, including relevant files or components.
- Work remaining, in execution order only when order matters.
- Current blockers, failures, approvals, and unresolved choices.
- Verification already performed and its result.

Use exact identifiers, paths, commands, or error messages only when they are necessary to resume the work. Clearly separate verified facts from assumptions.

## Output

Return a concise Korean summary when the conversation is in Korean; otherwise match the user's language. Prefer a short paragraph and a small bullet list over a transcript-style recap.

If text follows `/compact`, treat it as an instruction about the summary's emphasis or as the next action to preserve.

## Boundaries

- Do not perform new implementation, file changes, commits, or external actions while compacting.
- Do not invent completed work or silently resolve an open decision.
- This command creates a compact working summary; it does not delete the stored chat history or guarantee a lower model context size by itself.
