# R1 baseline vs R3 candidate

Verdict: **acceptance_regression**

| Task | Policy | Acceptance B→C | Wall Δ | Prompt tok Δ | Completion tok Δ | Queue ms | Saved tok est. | Suppress |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| investigate_code_tests | autonomous | 5/5 → 5/5 | +58.76% | +117.44% | +16.58% | 3369.5 | 0.0 | 0 |
| investigate_code_tests | fixed_one | 4/5 → 0/5 | +49.17% | +23.31% | +28.11% | 3651.9 | 0.0 | 4 |
| investigate_code_tests | none | 5/5 → 5/5 | +8.41% | +0.00% | -2.21% | 0.2 | 0.0 | 0 |
| multi_file_refactor | autonomous | 5/5 → 5/5 | +83.80% | -8.14% | +160.29% | 0.5 | 0.0 | 0 |
| multi_file_refactor | fixed_one | 5/5 → 5/5 | -18.01% | -8.26% | -36.82% | 0.7 | 0.0 | 0 |
| multi_file_refactor | none | 5/5 → 5/5 | +15.69% | +0.00% | +2.83% | 0.3 | 0.0 | 0 |
| single_file_bug | autonomous | 5/5 → 5/5 | +28.14% | +11.38% | +12.89% | 0.2 | 0.0 | 0 |
| single_file_bug | fixed_one | 5/5 → 5/5 | -22.97% | -34.09% | -24.50% | 0.4 | 0.0 | 0 |
| single_file_bug | none | 5/5 → 5/5 | +0.31% | +0.00% | -2.19% | 0.2 | 0.0 | 0 |

Acceptance regressions: investigate_code_tests/fixed_one
