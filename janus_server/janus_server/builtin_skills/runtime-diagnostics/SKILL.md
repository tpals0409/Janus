---
name: runtime-diagnostics
description: Diagnose and operate Janus local build, packaging, process, backend, and MLX model-runtime failures. Do not use for ordinary product feature implementation or external deployment.
---

# Runtime Diagnostics

Restore or verify the requested local runtime state without changing product behavior to hide a failure.

## Observe first

- Identify exact app, parent, child, and model-server processes.
- Check expected ports and authenticated health endpoints.
- Read the newest relevant log lines rather than aggregated historical errors.
- Confirm runtime paths, virtual environments, model paths, and launch arguments.
- For model startup, distinguish loading delay, port conflict, dependency failure, and memory failure.

## Act safely

- Target exact processes and paths; preserve unrelated services and user data.
- Prefer normal termination and reversible configuration changes.
- Request approval before destructive actions, external deployment, or broader system changes.
- Rebuild only when source and packaged artifacts differ or the contract requires it.

## Verify

- Confirm the expected process tree.
- Confirm backend health and model endpoint responses.
- Confirm required launch flags such as the selected model and MTP draft configuration.
- Report actions, evidence, and recovery information, then stop.

