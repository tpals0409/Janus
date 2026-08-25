---
name: interview
description: Clarify a product, feature, workflow, or design request through a short decision-focused interview before implementation. Use only when the user explicitly invokes /interview.
---

# Interview

Turn an uncertain request into a compact execution brief. Do not begin implementation during the interview.

## Conversation

- Ask one material question per turn.
- Offer two to four concrete, mutually distinct options when useful. Put the recommended option first and explain its tradeoff in one sentence.
- Include a short free-form choice such as "직접 입력" when the listed options may not fit.
- Do not ask about details that can be safely inferred from the existing product, repository conventions, or the user's earlier answers.
- Never repeat a settled question. If the user says to decide, choose the smallest coherent option and record that assumption.
- Prefer at most six questions. Continue only when the user asks for a deeper interview or an unresolved choice would materially change the result.
- Match the language of the user.

## Boundaries

- Do not read or modify files, run commands, create workers, or produce a mockup while interviewing unless the user explicitly ends the interview and requests execution.
- Do not turn optional ideas into requirements.
- Do not propose architecture, abstractions, pages, roles, or future features that the request does not need.
- If the request is already sufficiently clear, skip unnecessary questions and present the brief.

## Completion

When the important choices are settled, provide a concise brief containing only applicable fields:

```yaml
goal: observable user outcome
users: primary user, if relevant
in_scope:
  - required behavior
out_of_scope:
  - nearby work explicitly excluded
experience:
  - key interaction or visual decisions
constraints:
  - technical or operational boundaries
done_when:
  - observable acceptance criteria
assumptions:
  - decisions made on the user's behalf
```

End by asking whether the brief should be adjusted or execution should begin. Do not start automatically.
