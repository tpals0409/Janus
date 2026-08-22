# Janus 구현 상태

기준일: 2026-08-22
제품 목표: **제한된 로컬 하드웨어에서 로컬 에이전트가 가장 적은 시간·토큰·사용자 개입으로
검증된 변경을 만들도록 격리·스케줄·감독·평가하는 ADE**

- 제품 정의: [PRODUCT.md](PRODUCT.md)
- 구현 순서와 출구 조건: [ROADMAP.md](ROADMAP.md)
- 실행 체크리스트: [CHECKLIST.md](CHECKLIST.md)

## 현재 판정

**측정 가능한 로컬 runtime(R1/P0), ADE 작업 경계(R2/P1), 자원 효율 엔진
(R3/P2), Git-derived ChangeSet·Verification·Review·Ship(R4/P3), Evaluation Lab과
Adaptive Orchestration(R5/P4-20)까지 완료됐다. Janus는
이제 Task 생성부터 격리 worktree 실행, 독립 검증, revision-aware review, Task branch
commit/push 또는 명시적 cherry-pick handoff까지 앱 안에서 연결한다.**

현재 앱은 로컬 MLX 오케스트레이터와 런타임 워커를 실행하고 trace를 보여주는 검증된 세로
조각이다. Project/Task 중심 화면에서 별도 Git worktree를 준비하고, AgentProfile을 선택해
고유 Dispatch attempt와 AgentSession을 시작·재개·취소·중지할 수 있다. Session 상태,
transcript와 runtime event는 SQLite에 영속화되고 최신 Dispatch만 Task 이벤트를 쓸 수 있다.
ChangeSet과 review는 agent 답변과 독립적으로 Git에서 매번 다시 파생되며,
현재 revision의 Janus verification이 모두 성공해야 accept·commit할 수 있다.

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
| 최상위 객체 | Project/Task 도메인·API·Task 중심 UI | queue와 Needs You/Review 운영 |
| 실행 경계 | Task별 WorkspaceContext/worktree와 영속 runtime | ResourceLease 기반 실행 권한 |
| 에이전트 | 단일 Janus Local 설정 | 측정·비교 가능한 로컬 Agent/Model Profile |
| 실행 시도 | Dispatch/AgentSession 영속 실행·resume·stale 거부 | 예산·lease·완료 판정 |
| 자원 제어 | 모델 서버에 즉시 요청 | generation lease + tool/verification scheduler |
| 결과 | Git-derived ChangeSet + 독립 Verification | Evaluation Lab의 반복 비교 |
| 최적화 | token/latency 표시 | 고정 TaskSuite의 품질·시간·token·개입 비교 |
| 완료 | review 수락과 Task branch ship | 적응형 최적화와 회귀 판정 |
| 기본 화면 | Task·ChangeSet·Verification·Review·Ship | 운영 Dashboard와 Evaluation Lab |

`tools.WORKSPACE` 전역 mutable 상태는 제거됐다. 파일·셸·검증은 불변
`WorkspaceContext`(소유 Task/Workspace/Dispatch ID 포함)를 받고, 두 context의 병렬 파일
격리와 다른 workspace 경로 거부를 회귀 테스트로 고정했다.

Task UI와 영속 runtime의 분리는 해소됐다. 계측용이던 queue/lease 이벤트는 프로세스 공유
ResourceScheduler의 실제 실행 권한과 연결됐다. lease는 timeout·취소·예외·앱 종료에서
실제 반환을 기다리며 queue 원인이 Task 화면에 표시된다. Dispatch/RuntimeWorker별
token·time·step 한도와 worker cap도 강제된다. queue 상태와 단일 model slot 비용을 worker
정책에 반영했고 실제 TaskSuite로 재측정했다. Evaluation Lab은 고정 TaskSuite에서
AgentProfile·prompt·budget·worker policy를 반복 비교하고 acceptance regression을 자동
판정한다. Task 실행은 분류·model queue·직전 실패에 따라 worker 역할 순서와 fan-out,
Dispatch budget을 결정하고 그 근거를 Dispatch에 불변 스냅샷으로 남긴다. 현재 다음 제품
공백은 평가에서 선택한 프로필의 기본값 승격과 10개 Task를 한 화면에서 감독하는 운영
Dashboard다.

## 현재 검증

2026-08-22 현재 체크아웃에서 직접 확인:

- Python 테스트 111개 통과
- Node lifecycle 테스트 7개 통과(실제 분리 프로세스 그룹 3회 start/stop 포함)
- 도구 자체 검사 통과
- 오케스트레이터 spec 검사 통과
- TypeScript 타입 검사 통과
- Electron production build 통과
- 정적 그래프/LangGraph 의존성 제거 완료
- 실제 Qwen3.8-27B smoke 4개 시나리오 통과: 멀티턴, worker spawn/stop, cancel 후 재개
- TaskSuite 3개 × 정책 3개 × 5회 = 45회 완료, acceptance 44/45
- P2 회귀 수정 fixed-one 15회: 독립 acceptance와 변경 파일 조건 15/15, 정책 준수 15/15,
  정상 turn 종료 14/15. 마지막 1회는 변경·acceptance 성공 후 최종 응답 생성 중 120.12초로
  120초 실험 제한을 0.12초 초과했으며 사용자 판단으로 P2 완료 범위에 포함
- smoke 종료 후 owned MLX PID 종료와 orphan process 0 확인
- P3 Task 생성→Session 시작→검증→review→commit→push E2E와 main checkout 불변 통과
- 두 Task의 ChangeSet·commit 파일과 branch가 서로 교차 오염되지 않음을 통합 테스트로 확인

아직 검증하지 못한 것:

- 실제 27B Task UI 실행에서 ChangeSet review·ship까지 사람이 한 번에
  완주하는 acceptance

P1에서 추가로 검증한 것:

- Task 생성 계약과 AgentSession 패널의 1280×720 실제 React 렌더링, 콘솔 오류 0
- Task Session의 start/send/cancel/stop/resume와 transcript/runtime log 영속화
- 서버 재시작 중 running Session을 idle/needs_you로 복구한 뒤 멀티턴 resume
- 새 Dispatch 이후 오래된 WebSocket/event 거부
- 두 Task 동시 실행에서 한 Task 취소가 다른 Task 완료에 영향을 주지 않음

P2-11에서 추가로 검증한 것:

- 프로세스 전체 model generation 기본 1-slot과 Task 간 queue
- `cpu_tool`, `io_tool`, `verification` 독립 concurrency cap
- 높은 우선순위 우선 실행과 aging 기반 starvation 방지
- model generation 중 독립 verification이 실제로 겹치는 timeline
- 대기 Task가 선행 Task 취소 후 lease를 얻어 독립 완료

P2-12에서 추가로 검증한 것:

- lease queue timeout과 waiter 제거
- 대기 취소, active generation 취소, runtime/verification 예외 후 활성 lease 0
- scheduler close가 active cancel을 전파하고 queue를 깨운 뒤 idle까지 대기
- FastAPI lifespan shutdown이 live Task runtime을 취소하고 lease 반환을 확인
- Task 화면에 resource, queue 위치, active/cap, 대기 원인 표시

P2-13에서 추가로 검증한 것:

- AgentProfile budget의 Dispatch snapshot과 schema v3 migration
- 멀티턴 누적 token/time/step/worker usage 영속화
- Dispatch와 RuntimeWorker token/time/step 소진 판정
- worker 총수·동시 실행 cap과 해당 spawn만 거부
- Task UI에서 queue priority/deadline 지정과 budget 사용량 표시
- 한 Dispatch 소진이 다른 Task의 Session/Budget에 영향 없는 격리

P2-14에서 추가로 검증한 것:

- model generation queue가 이미 대기 중이면 추가 worker spawn을 억제하고 이유를 event로 기록
- 동일 worker 요청은 중복 실행하지 않고, 완료 결과는 같은 Session에서 재사용
- worker name·role·system·task/context·tool subset fingerprint와 입력 길이 상한
- verifier role의 읽기 전용 tool 교집합과 implementer/researcher 결과 통합 역할 분리
- assistant tool call/result 쌍을 보존하는 project summary/session compaction
- stable-prefix cache 후보, 원본/전송 input 문자·token 추정치와 절감량 계측
- 결정적 acceptance marker를 유지하면서 합성 장기 Session 입력 40% 이상 감소
- 실제 Qwen3.8-27B smoke 4개 시나리오 재통과와 owned MLX orphan process 0

R3 TaskSuite 재측정 결과:

- 동일 3개 fixture × 3개 정책 × 5회, 실제 Qwen3.8-27B/4-bit MLX 45회 완료
- 전체 acceptance 40/45: R1 baseline 44/45보다 회귀해 후보 승격 보류
- `none` 15/15, 평균 37.94초(+9%), prompt token은 사실상 동일
- `fixed_one` 10/15: 작은 두 Task는 18~23% 단축됐지만 조사 Task는 0/5
- `autonomous` 15/15, 평균 72.53초(+63%); multi-file에서 5회 모두 불필요한 worker 선택
- 조사 fixed-one worker 5회 모두 누적 token 8,192 한도를 소진했고, 이후 반복 spawn/통합이
  4회 timeout과 1회 실제 acceptance 실패로 이어짐
- 짧은 단일-turn TaskSuite에서는 session compaction threshold에 도달하지 않아 절감 token 0;
  stable-prefix 후보 계측만 확인
- 상세 비교: `janus_server/artifacts/r3/tasksuite/20260822-183500/comparison.md`

P2 회귀 수정 결과:

- worker token/step 소진 시 workspace 변경과 부분 결과를 상위 에이전트가 재사용하고 같은
  subtask를 반복 spawn하지 않도록 `completed_partial` 결과로 통합
- 모델이 광고되지 않은 쓰기 도구를 임의 호출해도 실행 경계에서 `tool_not_in_node_subset`로
  거부해 researcher/verifier read-only 계약을 실제로 강제
- 단일 model slot + tight fixed-one implementer는 1-step read-only scout로 명시 전환하고
  requested/effective role과 이유를 span에 기록; 쓰기 소유자는 상위 에이전트 하나로 유지
- 자율 worker는 사용자의 명시적 위임이나 profile override가 없으면 생성 전 억제하고, 억제
  결과를 오류가 아닌 직접 완료 지침으로 반환
- 최종 fixed-one 15회에서 independent acceptance·필수/허용 변경·worker 1개 정책은 15/15;
  정상 종료는 14/15이며 마지막 1회만 acceptance 성공 후 최종 응답에서 0.12초 초과
- 최종 요약: `janus_server/artifacts/r3/tasksuite/20260822-p2-final-fixed-one-v2/baseline.md`

P3 ADE MVP 결과:

- ChangeSet은 base ref 대비 committed·staged·unstaged·untracked를 Git에서 매번 다시
  파생하고 rename/delete, binary, large diff 절단을 포함한다.
- ChangeSet 전체를 해시한 revision ID로 staged/unstaged 변화까지 stale comment·accept·
  commit에서 거부한다.
- Project별 acceptance/test/lint/typecheck를 verification scheduler에서 실행하고 exit code,
  duration, stdout/stderr, agent claim, Janus result를 분리 저장한다.
- 파일·hunk·line review, resolve/reopen, 일괄 request changes, 검증 gate accept, 명시적
  discard를 제공하며 unmerged 변경은 accept/discard하지 않는다.
- commit/push는 Janus 소유 Task worktree와 `janus/` branch에서만 실행한다. local apply는
  main checkout을 수정하지 않고 사용자가 명시적으로 실행할 cherry-pick 명령을 준다.

P4-19 Evaluation Lab 결과:

- baseline/candidate를 서로 다른 AgentProfile snapshot으로 TaskSuite에서 실행하고 실행
  중 prompt, worker policy, budget, model, quantization을 변경불가 조건으로 저장한다.
- 성공률, wall mean/p95/표준편차, token 평균/표준편차, user input+approval 개입 비용을
  Task별·전체로 비교한다.
- acceptance 하락은 시간·token 개선으로 상쇄할 수 없고, 임계치를 넘는 시간·token·
  개입 증가도 regression으로 판정한다.
- hardware/model/quantization이 다르면 비용 결론을 `incomparable_conditions`로 차단하고,
  JSON/CSV/Markdown으로 결과를 export한다.

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

## 완료한 마일스톤: R2 Task/worktree/WorkspaceContext

영속 도메인 모델, WorkspaceContext, WorkspaceService, Task 중심 UI와 Task–Runtime 연결을
완료했다. R3의 scheduler, ResourceLease 회수, Dispatch/RuntimeWorker budget, worker
backpressure와 context 효율 및 동일 TaskSuite 재측정을 완료했다. 측정 후보는 acceptance
regression으로 승격하지 않으며, 다음으로 Task 적합성 spawn gate와 실패 worker 결과 통합을
수정한다.

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
