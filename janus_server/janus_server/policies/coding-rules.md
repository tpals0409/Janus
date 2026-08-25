# Coding Rules

Apply these rules whenever a task involves code or configuration changes.

1. Change only what the request and its completion criteria require.
2. Read the affected code, nearby tests, and repository instructions before editing.
3. Follow the repository's existing architecture, naming, style, and tooling.
4. Prefer the smallest behaviorally complete change.
5. Do not add abstractions, extension points, dependencies, or files for hypothetical future needs.
6. Do not mix unrelated refactoring, reformatting, or cleanup into a functional change.
7. Remove duplication only when it is real, repeated, and simplifying it helps the current change.
8. Use names to express intent. Comments should explain reasons or constraints, not restate the code.
9. Do not hide errors, weaken validation, or delete or relax tests to make a change pass.
10. Run the narrowest relevant verification, then broader existing checks when risk justifies them.
11. Preserve user changes and unrelated work already present in the workspace.
12. Report only verification actually performed. When the request and checks are satisfied, stop.

Repository instructions and established project conventions take precedence over general style preferences.
