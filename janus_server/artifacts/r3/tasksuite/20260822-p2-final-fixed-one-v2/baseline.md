# TaskSuite v0 — p2-final-fixed-one-v2

| Task | Policy | Success | Policy | Wall mean ± σ (s) | Prompt / Completion tok | Queue ms | Saved tok est. | Suppress | Workers |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| investigate_code_tests | fixed_one | 4/5 | 5/5 | 106.34 ± 7.91 | 9619.2 / 1056.0 | 0.4 | 0.0 | 1 | 1.0 |
| multi_file_refactor | fixed_one | 5/5 | 5/5 | 93.64 ± 3.22 | 13836.0 / 758.2 | 0.4 | 0.0 | 0 | 1.0 |
| single_file_bug | fixed_one | 5/5 | 5/5 | 64.46 ± 1.04 | 9524.0 / 518.0 | 0.3 | 0.0 | 0 | 1.0 |

## Overall by policy

| Policy | Success | Wall mean ± σ (s) | Prompt / Completion tok | Approvals | Workers |
|---|---:|---:|---:|---:|---:|
| fixed_one | 14/15 | 88.14 ± 18.22 | 10993.1 / 777.4 | 1.67 | 1.00 |

`Approvals`는 benchmark가 자동 승인한 write/edit 요청 수다. 실제 user message는 모든 실행이 1개로 고정됐다. acceptance는 agent와 분리된 harness가 실행한다.
