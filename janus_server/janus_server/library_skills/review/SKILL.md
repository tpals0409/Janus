---
name: review
description: Review the change already made in this workspace before a person sees it — scope creep, leftover code, missed call sites, unverified claims. Use only when the user explicitly invokes /review.
---

# Review

Report what a careful reviewer would send back about the current change. Do not fix anything while reviewing.

## Read first

- The change itself: `git diff` for working-tree edits, `git diff --staged` for staged ones, and `git diff <base>...HEAD` for what is already committed on the Task branch. Use the Task's base ref when it is known.
- The full body of every function the diff touches, not only the changed lines.
- The callers of every function whose signature, contract, or return shape changed.

## Look for

- **Scope** — changes the request did not ask for: unrelated renames, reformatting, new abstractions with one caller, options nothing sets.
- **Missed call sites** — other callers still written against the old behavior. Grep for them; do not assume the diff found them all.
- **Leftovers** — debug prints, commented-out code, unused imports or symbols stranded by a deletion, a TODO this change introduced.
- **Unverified claims** — behavior asserted but never run, and non-trivial logic with no check that would fail if it broke.
- **Contract drift** — tests, types, docs, or messages that still describe the old behavior.

## Report

List findings in severity order. One line each: `path:line` — what is wrong, and what it breaks. Separate what you verified by reading or running from what you suspect.

Say plainly when the change is clean. Do not pad the report with findings that do not change the result.

End by asking whether to fix the findings. Do not start fixing on your own.

## Boundaries

- Do not edit files, commit, or run destructive commands while reviewing.
- Do not restate what the change does; the reader already knows the intent.
- Do not review code the change did not touch, unless it is a caller the change breaks.
