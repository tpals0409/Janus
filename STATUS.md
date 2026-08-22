# Janus 구현 상태

기준일: 2026-08-22
제품 목표: **제한된 로컬 하드웨어에서 로컬 에이전트가 가장 적은 시간·토큰·사용자 개입으로
검증된 변경을 만들도록 격리·스케줄·감독·평가하는 ADE**

- 제품 정의: [PRODUCT.md](PRODUCT.md)
- 구현 순서와 출구 조건: [ROADMAP.md](ROADMAP.md)
- 실행 체크리스트: [CHECKLIST.md](CHECKLIST.md)

## 현재 판정

**측정 가능한 로컬 runtime(R1/P0)과 ADE의 영속 Task·Workspace 백엔드
(R2/P1 6~8)는 완성됐다. Task 중심 UI와 Task–Runtime 연결(P1 9~10)이 남았다.**

현재 앱은 로컬 MLX 오케스트레이터와 런타임 워커를 실행하고 trace를 보여주는 검증된 세로
조각이다. Project/Task/Workspace/Dispatch/AgentSession 스키마와 Task별 Git worktree는
구현됐지만, Task 중심 UI, runtime 연결, local resource scheduler, Git diff review,
평가 loop가 없으므로 완성된 ADE로 부르기에는 이르다.

기존 “오케스트레이터-워커 전환으로 원래 계획과 일치했다”는 평가는 제품 목표가 런타임일 때만
맞았다. ADE를 목표로 확정했으므로 현재 런타임은 제품 전체가 아니라 Task를 수행하는 핵심
**Janus Local Runtime**으로 재배치한다. 외부 모델과 외부 CLI agent 지원은 현재 미정이며
핵심 로드맵에 포함하지 않는다.

## 구현되어 있고 보존할 것

### Janus Local runtime

- Qwen3.8-27B MLX 로컬 모델 연결
- WS 연결별 지속 멀티턴 오케스트레이터 세션
- `create_worker`를 통한 실행 중 worker 생성
- 같은 턴의 tool call 병렬 실행
- worker 도구 부분집합과 spawn 깊이 1 제한
- 턴 취소, 개별 worker 중단, 취소 후 세션 유지

### 안전과 상태 정직성

- 위험 도구 승인을 `tools.dispatch`에서 중앙 처리
- 승인 없음·거부 시 기본 거부
- 실행별 `WorkspaceContext`와 파일 도구 workspace jail
- 기동별 인증 토큰, Origin 검증, HTTP/WS 인증
- 모델 부재와 인증 실패를 구체적인 오류로 표시
- 병렬 승인 요청과 소켓 경쟁 방어

### 관측과 UI

- 오케스트레이터와 runtime worker span
- monotonic 기반 queue/lease/generation/tool/verification timing
- Task/Session/Dispatch/Worker ID, token, worker 수, memory snapshot
- 배타적 active/user-wait 시간 회계와 실행 기록
- 실제 27B TaskSuite 반복 baseline과 정책 A/B 비교 기반
- Electron shell, 파일 트리, agent profile 설정
- 실패·취소 상태 표시

## ADE 전환의 핵심 결손

| 영역 | 현재 | 목표 |
|---|---|---|
| 최상위 객체 | Project/Task 도메인·API | Task 중심 UI |
| 실행 경계 | Task별 WorkspaceContext/worktree 백엔드 | Task UI와 runtime의 완전한 연결 |
| 에이전트 | 단일 Janus Local 설정 | 측정·비교 가능한 로컬 Agent/Model Profile |
| 실행 시도 | Dispatch/AgentSession 저장 스키마 | runtime에 연결된 영속 실행 |
| 자원 제어 | 모델 서버에 즉시 요청 | generation lease + tool/verification scheduler |
| 결과 | 답변과 trace | Git ChangeSet + Verification |
| 최적화 | token/latency 표시 | 고정 TaskSuite의 품질·시간·token·개입 비교 |
| 완료 | 턴 종료 | review 수락과 ship |
| 기본 화면 | agent/trace | Task 상태와 Needs You/Review |

`tools.WORKSPACE` 전역 mutable 상태는 제거됐다. 파일·셸·검증은 불변
`WorkspaceContext`(소유 Task/Workspace/Dispatch ID 포함)를 받고, 두 context의 병렬 파일
격리와 다른 workspace 경로 거부를 회귀 테스트로 고정했다.

현재 가장 큰 제품 공백은 Task UI와 영속 runtime이 아직 분리됐다는 점이다.
계측된 queue/lease를 ResourceScheduler 정책으로 연결하는 작업은 P1 통합 후 R3에서 진행한다.

## 현재 검증

2026-08-22 현재 체크아웃에서 직접 확인:

- Python 테스트 53개 통과
- Node lifecycle 테스트 7개 통과(실제 분리 프로세스 그룹 3회 start/stop 포함)
- 도구 자체 검사 통과
- 오케스트레이터 spec 검사 통과
- TypeScript 타입 검사 통과
- Electron production build 통과
- 정적 그래프/LangGraph 의존성 제거 완료
- 실제 Qwen3.8-27B smoke 4개 시나리오 통과: 멀티턴, worker spawn/stop, cancel 후 재개
- TaskSuite 3개 × 정책 3개 × 5회 = 45회 완료, acceptance 44/45
- smoke 종료 후 owned MLX PID 종료와 orphan process 0 확인

아직 검증하지 못한 것:

- Task 중심 UI에서 생성→worktree 준비→runtime 실행→review까지의 전체 흐름
- 두 Task의 영속 AgentSession을 동시 실행·취소하는 runtime 격리
- scheduler/lease/budget 적용 후 baseline 대비 개선

## 완료한 마일스톤: R1 실제 27B Baseline과 계측

완료 항목:

1. queue/generation/tool/verification timing event schema와 전체 시간 회계
2. 실제 MLX 서버를 사용하는 반복 가능한 smoke harness
3. objective와 acceptance command가 고정된 TaskSuite v0
4. worker 없음/고정 worker/자율 worker 각각 5회 baseline
5. backend/model PID 소유권, health probe, backoff, orphan 검사

전체 정책 결과는 `none` 15/15, `fixed_one` 14/15, `autonomous` 15/15다. 평균 wall time은
각각 34.79초, 109.93초, 44.63초였다. 자율 정책은 15회 모두 worker 0을 선택했다. 상세 표는
`janus_server/artifacts/p0/tasksuite/20260822-115844/baseline.md`에 있다.

## 다음 마일스톤: R2 Task/worktree/WorkspaceContext

영속 도메인 모델, WorkspaceContext, WorkspaceService는 완료됐다. 다음은 Task 중심 UI와
Task–Runtime 연결이다.
R3에서 model generation 1-slot scheduler, ResourceLease와 token/time/worker budget을 도입한다.

R1의 상세 출구 조건은 [ROADMAP.md](ROADMAP.md#r1-실제-27b-baseline과-계측--최적화의-기준선)를
단일 기준으로 사용한다.

## 기존 결함의 새 우선순위

### R2에서 해결됨

- **M4:** legacy Agent 표시 slug와 불변 `_instance_id` 실행 소유권을 분리했다.
  Agent를 삭제·재생성해도 이전 `runs/`를 상속하지 않고, 기존 기록은 보존한다.
- 신규 Task runtime 기록은 `AgentSession`/`Dispatch` UUID를 소유권으로 사용한다.

### R1에서 해결됨

- **H5:** owned PID/process group만 TERM→KILL하고 종료를 기다리며 orphan을 명시적으로 탐지한다.
- 실제 27B smoke와 45회 TaskSuite로 fake client와 실환경 사이의 공백을 닫았다.
- worker 정책의 성공률·wall time·token·승인 요청 baseline을 저장했다.
- queue, lease, generation, tool, verification, memory 계측을 저장한다.

### 제품 전환으로 소멸한 항목

- 정적 DAG 편집·삭제·순환 trace 문제
- LangGraph token 귀속 문제
- graph node/edge Inspector 관련 UX 결함

## 테스트와 실행

```bash
cd janus_server
uv run pytest tests/ -q
uv run python -m janus_server.tools
uv run python -m janus_server.spec

cd ../janus
npx tsc --noEmit
pnpm test:main
pnpm build
pnpm dev
```

백엔드 로그: `/tmp/janus-server.log`
MLX 로그: `/tmp/janus-mlx.log`
