---
name: clear
description: Reset the working conversational context before starting a new request. Use only when the user explicitly invokes /clear.
---

# Clear

End the current conversational thread of work and treat the text following `/clear` as a fresh request.

## Behavior

- Discard unfinished plans, assumptions, pending questions, and inferred intent from earlier messages.
- Keep only durable constraints that still govern the environment, including system instructions, permissions, repository rules, and facts visible in the current workspace.
- Do not continue an earlier implementation unless the new request explicitly refers to it.
- If text follows `/clear`, begin handling it immediately as the new request.
- If no text follows `/clear`, confirm briefly that the working context has been cleared and wait for a new request.

## Safety

- This command resets the agent's working interpretation only. It does not delete chat history, files, commits, sessions, memories, or stored data.
- Do not undo or revert existing changes.
- Do not claim that model-visible history was physically erased.
