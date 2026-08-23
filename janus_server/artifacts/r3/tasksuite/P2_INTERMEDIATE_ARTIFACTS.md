# P2 intermediate TaskSuite artifacts

P2 worker 회귀 수정 과정에서 생성된 중간 실행을 분류한 인덱스다. 원본의
`runs/`, trace, workspace는 로컬 진단용으로만 보존하고 Git에 추가하지 않는다.

공식 P2 acceptance 근거는
[`20260822-p2-final-fixed-one-v2`](./20260822-p2-final-fixed-one-v2/baseline.md)다. 이 실행은
`completed`, 15회 중 independent acceptance 15/15, 정상 turn 종료 14/15,
owned model orphan process 0을 기록한다. 마지막 1회는 변경과 acceptance를
완료한 뒤 최종 응답 생성에서 120초 제한을 0.12초 초과했다.

## Interrupted matrices

| Local artifact | Recorded runs | Acceptance marker | 분류 |
| --- | ---: | ---: | --- |
| `20260822-p2-final` | 26 | 26/26 | `running`; 3-policy 매트릭스 중단 |
| `20260822-p2-final-v2` | 36 | 35/36 | `running`; 3-policy 매트릭스 중단 |
| `20260822-p2-final-fixed-one` | 6 | 5/6 | `running`; fixed-one 재검증 중단 |

`running`으로 남은 결과는 종료 시각과 model cleanup 근거가 없으므로 최종
baseline이나 성능 비교에 사용하지 않는다.

## Completed diagnostic runs

| Local artifact | Recorded runs | Acceptance marker | 용도 |
| --- | ---: | ---: | --- |
| `20260822-p2-worker-reserve-smoke` | 1 | 0/1 | worker reserve 경로 진단 |
| `20260822-p2-worker-integration-smoke` | 1 | 0/1 | partial result 통합 경로 진단 |
| `20260822-p2-worker-finish-smoke` | 1 | 0/1 | worker finish 경로 진단 |
| `20260822-p2-worker-fix` | 5 | 4/5 | regression fix 반복 검증 |
| `20260822-p2-single-slot-scout-smoke` | 1 | 1/1 | single-slot scout 전환 smoke |
| `20260822-p2-single-slot-scout-repeat` | 5 | 5/5 | single-slot scout 반복 검증 |
| `20260822-p2-multi-scout-handoff-smoke` | 1 | 0/1 | multi-file handoff 진단 |
| `20260822-p2-multi-scout-enforced-smoke` | 1 | 0/1 | read-only scout 강제 진단 |
| `20260822-p2-multi-one-step-scout-smoke` | 1 | 1/1 | one-step scout 전환 smoke |

이 실행들은 모두 `completed`이고 owned model orphan process 0을 기록했다. 각 결과는
수정 과정의 특정 경로만 검증하므로, 전체 P2 acceptance를 대체하지 않는다.
