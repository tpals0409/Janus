# TaskSuite v0 baseline

| Task | Policy | Success | Policy | Wall mean ± σ (s) | Prompt / Completion tok | Approvals | Workers |
|---|---|---:|---:|---:|---:|---:|---:|
| investigate_code_tests | autonomous | 5/5 | 5/5 | 52.94 ± 0.76 | 4927.0 / 680.4 | 2.0 | 0.0 |
| investigate_code_tests | fixed_one | 4/5 | 5/5 | 120.14 ± 30.18 | 11964.4 / 1482.2 | 2.8 | 1.0 |
| investigate_code_tests | none | 5/5 | 5/5 | 42.87 ± 0.99 | 4108.0 / 542.2 | 2.0 | 0.0 |
| multi_file_refactor | autonomous | 5/5 | 5/5 | 53.56 ± 6.32 | 11946.6 / 405.4 | 2.0 | 0.0 |
| multi_file_refactor | fixed_one | 5/5 | 5/5 | 115.01 ± 7.40 | 18073.8 / 1188.6 | 2.0 | 1.0 |
| multi_file_refactor | none | 5/5 | 5/5 | 36.84 ± 5.42 | 6018.0 / 367.8 | 2.0 | 0.0 |
| single_file_bug | autonomous | 5/5 | 5/5 | 27.39 ± 0.41 | 5750.0 / 221.8 | 1.0 | 0.0 |
| single_file_bug | fixed_one | 5/5 | 5/5 | 94.64 ± 3.34 | 14526.8 / 1013.0 | 1.0 | 1.0 |
| single_file_bug | none | 5/5 | 5/5 | 24.65 ± 0.26 | 4862.0 / 228.0 | 1.0 | 0.0 |

## Overall by policy

| Policy | Success | Wall mean ± σ (s) | Prompt / Completion tok | Approvals | Workers |
|---|---:|---:|---:|---:|---:|
| none | 15/15 | 34.79 ± 8.22 | 4996.0 / 379.3 | 1.67 | 0.00 |
| fixed_one | 14/15 | 109.93 ± 21.14 | 14855.0 / 1227.9 | 1.93 | 1.00 |
| autonomous | 15/15 | 44.63 ± 12.74 | 7541.2 / 435.9 | 1.67 | 0.00 |

`Approvals`는 benchmark가 자동 승인한 write/edit 요청 수다. 실제 user message는 모든
실행이 1개로 고정됐다. acceptance는 agent와 분리된 harness가 실행했다.

유일한 실패는 `investigate_code_tests / fixed_one / repeat 1`이다. worker는 60.13초에
성공해 요구 파일을 모두 수정했고 독립 acceptance도 exit 0이었지만, orchestrator의 최종
생성이 전체 180초 turn timeout을 넘었다. 총 model generation 180.05초, 22,233 token으로
확인됐으며 보수적으로 실행 실패를 유지한다.
