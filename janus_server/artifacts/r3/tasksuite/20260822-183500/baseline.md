# TaskSuite v0 — r3-scheduler-budget-backpressure

| Task | Policy | Success | Policy | Wall mean ± σ (s) | Prompt / Completion tok | Queue ms | Saved tok est. | Suppress | Workers |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| investigate_code_tests | autonomous | 5/5 | 5/5 | 84.05 ± 17.71 | 10713.4 / 793.2 | 3369.5 | 0.0 | 0 | 0.2 |
| investigate_code_tests | fixed_one | 0/5 | 5/5 | 179.21 ± 11.36 | 14753.8 / 1898.8 | 3651.9 | 0.0 | 4 | 1.0 |
| investigate_code_tests | none | 5/5 | 5/5 | 46.47 ± 0.82 | 4108.0 / 530.2 | 0.2 | 0.0 | 0 | 0.0 |
| multi_file_refactor | autonomous | 5/5 | 5/5 | 98.45 ± 3.36 | 10973.8 / 1055.2 | 0.5 | 0.0 | 0 | 1.0 |
| multi_file_refactor | fixed_one | 5/5 | 5/5 | 94.30 ± 2.00 | 16581.0 / 751.0 | 0.7 | 0.0 | 0 | 1.0 |
| multi_file_refactor | none | 5/5 | 5/5 | 42.61 ± 6.72 | 6018.2 / 378.2 | 0.3 | 0.0 | 0 | 0.0 |
| single_file_bug | autonomous | 5/5 | 5/5 | 35.10 ± 4.44 | 6404.6 / 250.4 | 0.2 | 0.0 | 0 | 0.0 |
| single_file_bug | fixed_one | 5/5 | 5/5 | 72.90 ± 9.10 | 9574.6 / 764.8 | 0.4 | 0.0 | 0 | 1.0 |
| single_file_bug | none | 5/5 | 5/5 | 24.73 ± 0.36 | 4862.0 / 223.0 | 0.2 | 0.0 | 0 | 0.0 |

## Overall by policy

| Policy | Success | Wall mean ± σ (s) | Prompt / Completion tok | Approvals | Workers |
|---|---:|---:|---:|---:|---:|
| none | 15/15 | 37.94 ± 10.25 | 4996.1 / 377.1 | 1.67 | 0.00 |
| fixed_one | 10/15 | 115.47 ± 46.69 | 13636.5 / 1138.2 | 2.60 | 1.00 |
| autonomous | 15/15 | 72.53 ± 29.16 | 9363.9 / 699.6 | 1.67 | 0.40 |

`Approvals`는 benchmark가 자동 승인한 write/edit 요청 수다. 실제 user message는 모든 실행이 1개로 고정됐다. acceptance는 agent와 분리된 harness가 실행한다.
