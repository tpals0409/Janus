# Janus 구현 상태

기준일: 2026-08-23
제품 목표: **제한된 로컬 하드웨어에서 로컬 에이전트가 가장 적은 시간·토큰·사용자 개입으로
검증된 변경을 만들도록 격리·스케줄·감독·평가하는 ADE**

- 제품 정의: [PRODUCT.md](PRODUCT.md)
- 구현 순서와 출구 조건: [ROADMAP.md](ROADMAP.md)
- 실행 체크리스트: [CHECKLIST.md](CHECKLIST.md)
- v1 조건별 완료 증거: [V1_AUDIT.md](V1_AUDIT.md)

## 현재 판정

**Janus v1.0.0 완료. 측정 가능한 로컬 runtime(R1/P0), ADE 작업 경계(R2/P1), 자원 효율 엔진
(R3/P2), Git-derived ChangeSet·Verification·Review·Ship(R4/P3), Evaluation Lab·
Adaptive Orchestration·Operations Dashboard(R5/P4), PR·CI와 Task 개발 표면,
견고성·데이터 복구와 배포 품질(R6~R9/P5)까지 완료됐다. Janus는
이제 자연어 목표 위임부터 내부 Task 구성, 격리 worktree 실행, 독립 검증,
revision-aware review, Task branch
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
평가에서 선택한 runner 후보는 improved/equivalent gate를 통과해야 Project 기본
AgentProfile로 승격되며 comparison provenance가 남는다. Operations Monitor는 모든
Project의 Task를 Queue/Working/Needs You/Review/Failed lane에 모으고 model slot·queue,
Janus memory peak, budget 소진율, generation/tool/verification 흔적을 2초 간격으로 갱신한다.

## 현재 검증

2026-08-23 현재 체크아웃에서 직접 확인(테스트 수는 2026-08-25 기준):

- Python 테스트 통과 — 2026-08-25: 182 passed + 39 subtests(당시 166개, schema
  migration subtest 16개 포함)
- Electron main-process 테스트 통과 — 2026-08-25: 28개(당시 25개, 실제 분리 프로세스
  그룹 start/stop 포함)
- Electron renderer 테스트 통과 — 2026-08-25: 13개(당시 10개)
- 공식 Janus 아이콘을 포함한 unsigned macOS `Janus.app` 패키징 통과
- 도구 자체 검사 통과
- 오케스트레이터 spec 검사 통과
- TypeScript 타입 검사 통과
- Electron production build 통과
- v1 actual-model artifact audit 통과: baseline 44/45, scheduler candidate 40/45,
  final fixed-one 14/15, real-model smoke 4/4, owned model orphan 0
- production Node와 pinned backend/model Python dependency advisory audit: 알려진 취약점 0
- clean-source 1.0.0 install/package smoke: schema v11, backup·diagnostics·shutdown 통과
- 정적 그래프/LangGraph 의존성 제거 완료
- 실제 Qwen3.8-27B smoke 4개 시나리오 통과: 멀티턴, worker spawn/stop, cancel 후 재개
- TaskSuite 3개 × 정책 3개 × 5회 = 45회 완료, acceptance 44/45
- P2 회귀 수정 fixed-one 15회: 독립 acceptance와 변경 파일 조건 15/15, 정책 준수 15/15,
  정상 turn 종료 14/15. 마지막 1회는 변경·acceptance 성공 후 최종 응답 생성 중 120.12초로
  120초 실험 제한을 0.12초 초과했으며 사용자 판단으로 P2 완료 범위에 포함
- smoke 종료 후 owned MLX PID 종료와 orphan process 0 확인
- P3 Task 생성→Session 시작→검증→review→commit→push E2E와 main checkout 불변 통과
- 두 Task의 ChangeSet·commit 파일과 branch가 서로 교차 오염되지 않음을 통합 테스트로 확인

별도 UX acceptance 범위:

- 실제 27B Task UI 실행에서 ChangeSet review·ship까지 사람이 한 번에
  완주하는 acceptance — 2026-08-23 사용자 수동 완주로 완료

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

P4-20/21 Adaptive Orchestration과 Operations 결과:

- Task를 narrow bug/investigation/test-heavy/multi-file/general로 분류하고 model queue와
  직전 Dispatch·verification 실패에 따라 worker policy, 역할 순서, fan-out, budget을
  결정한다. 결정 근거와 유효 예산은 Dispatch에 불변 스냅샷으로 저장된다.
- verification 실패는 read-only verifier 진단 후 implementer repair, budget/timeout/tool/
  runtime 실패는 각각 다른 bounded retry 전략으로 다음 attempt를 구성한다.
- regression·조건 불일치·import-only 후보는 기본값 승격을 거부하고, 검증된 runner 후보만
  Project 기본 AgentProfile과 provenance로 저장한다.
- Operations Monitor 한 화면에서 10개 Task의 queue와 attention, model slot·memory,
  Task별 budget, generation/tool/verification timeline을 감독하는 통합 테스트를 고정했다.

P5-22 PR·CI 통합 결과:

- commit/push 성공과 실패를 Task shipment로 분리 저장하며 인증·remote 실패 후에도 commit,
  branch, workspace를 그대로 보존해 재시도할 수 있다.
- review·commit·push가 끝난 Task branch만 `gh` argument-vector 호출로 GitHub PR을 만들고
  URL, base/head, review/merge 상태, checks, workflow run과 제한된 실패 로그를 영속화한다.
- 원격 refresh가 실패해도 마지막 PR/CI 관측값과 오류를 함께 남기며, merge 후 clean
  workspace archive를 제안하되 branch/worktree를 자동 삭제하지 않는다.

P5-23 Task 개발 표면 결과:

- primary/secondary PTY는 서버가 확인한 Task worktree에서만 실행되고, Task 전환 뒤에도
  bounded output buffer와 pane 상태를 복원한다. 다른 Task ID로 input/stop할 수 없다.
- Monaco 편집기는 Task jail 안의 2MB 이하 text file만 atomic replace로 저장하고 mtime
  충돌을 거부한다. `rg` 고정문자 검색과 파일 탭을 Task별 local state로 복원한다.
- preview는 Task ID를 해시한 별도 Electron persistent partition에서 localhost만 열며,
  console/network를 각 500개로 제한해 수집한다. 요소 선택은 DOM/CSS/rect/source hint와
  screenshot을 함께 반환하고, Task별 URL·탭·split 상태와 출력 copy/단축키를 제공한다.

P5-24 견고성과 복구 결과:

- 서버 재시작은 running session/dispatch/Task, verification, evaluation, terminal뿐 아니라
  준비 중 workspace/Task도 성공으로 오인되지 않는 retry 가능한 상태로 정리한다.
- schema v1~v10 각각을 v11로 올리는 migration subtest를 통과하고, 미래 또는 불연속
  migration 이력은 원본 DB를 수정하기 전에 거부한다. transaction disk-full과 editor
  atomic replace 실패는 부분 데이터를 남기지 않는다.
- 모델 서버 stderr tail을 16,000자로 제한하면서 OOM과 storage failure를 구분해 재시작
  안내를 제공한다. worktree 충돌은 기존 경로·branch를 덮어쓰지 않고, diff·verification·
  CI log·runtime event는 각각 hard limit 안에 보존된다.
- 인증된 maintenance API는 online SQLite backup, integrity check, SHA-256, `0600`, retention을
  제공한다. 자동 초기화는 금지하고 backup-first 수동 복원·reset 절차를 `RECOVERY.md`에
  고정했다. soak 동일 루프 단축 gate는 3.002초에 281 cycle을 완료했고 transient row 0,
  SQLite `integrity_check=ok`를 확인했다.

P5-25 배포 품질 결과:

- 루트 README가 제품 개념에서 Apple Silicon 전제, locked dependency 설치, 선택적 27B 모델
  준비, 첫 Task, 검증·패키징·복구까지 한 경로로 연결한다. bootstrap은 model을 암묵적으로
  받지 않고 `--with-model`에서만 4-bit snapshot을 준비한다.
- Electron production package는 backend와 model runtime lockfile을 Resources에 포함하고,
  writable Python environment와 로그는 Janus user-data 아래에 격리한다. `pnpm package:mac`은
  unsigned `dist/mac-arm64/Janus.app`을 생성했으며 app size는 305MB였다.
- diagnostics bundle은 database·환경변수 값을 제외하고 platform/version/schema/integrity와
  최대 1MB의 redacted log tail만 `0600` ZIP으로 만든다. API와 CLI 모두 제공하며 auth token,
  bearer, password, home path 제거를 테스트한다.
- app/backend/Python version 일치와 schema/update/backup/signing 정책을 자동 검증한다.
  clean-source fresh install smoke는 backend·MLX dependency 설치, Node test/build/package,
  빈 schema v11 health, backup integrity, diagnostics redaction, owned backend 종료를 통과했다.

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
backpressure와 context 효율 및 동일 TaskSuite 재측정을 완료했다. R4의 ADE loop와 R5의
Evaluation Lab, adaptive policy, profile promotion, operations supervision도 완료했다.

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

백엔드·MLX 로그: Electron backend status가 표시하는 Janus user-data `logs/` 경로

## 2026-08-23 개선 체크리스트 — 완료

### P0 · 회귀 기준점

- [x] 기존 스킬·프로필·컨텍스트·Graphite UI 변경을 전체 테스트 후 기준 커밋으로 고정
- [x] renderer에 Vitest + Testing Library 통합 테스트 추가
- [x] Electron main, renderer, TypeScript, production build를 하나의 반복 가능한 검증 경로로 구성

### P1 · 로컬 에이전트 효율

- [x] terminal 250ms, verification 500ms, evaluation 1s, operations 2s, workspace 650ms polling 제거
- [x] 인증된 `/events` WebSocket과 thread-safe bounded event bus 추가
- [x] terminal output은 payload stream, 나머지는 domain invalidation으로 분리
- [x] TaskSuite에 `none`/`relevant`/`noisy` 스킬 코호트와 load rate·prompt token 비용 계측 추가
- [x] 같은 고정 Task·모델·정책에서 성공률·wall time·token·개입·스킬 비용을 비교하는 A/B 결과 형식 추가

장시간 실제 27B 반복 실험은 기능 출구 조건이 아니라 운영 측정이다. 모델 서버가 떠 있지 않은
개발 환경에서도 하네스·코호트·집계 테스트는 결정적으로 검증되며, 필요할 때
`run_tasksuite_v0.py --skills-json tasksuite/v0/skill_cohorts/relevant.json`으로 실행한다.

### P2 · 구조와 접근성

- [x] 서버 주소·인증·HTTP/WebSocket 오류 처리를 `renderer/api.ts`로 분리
- [x] Task 프로젝트/작업 탐색을 `TaskSidebar.tsx`로 분리
- [x] 공통 Tabs에 Arrow/Home/End roving focus 추가
- [x] 공통 Dialog focus trap·Escape close·focus restore 및 ConfirmDialog 추가
- [x] 네이티브 `window.confirm` 제거, Skill import dialog를 공통 계약으로 전환
- [x] Checkbox·SegmentedControl·Toolbar·Menu 공통 컴포넌트 추가
- [x] 구성 상태의 green signal 오용과 실행 전 “오케스트레이터 1” 표시 수정

### P3 · 번들 및 유지보수

- [x] 미사용 legacy File/Inspector/YAML/Trace 화면 6개 제거
- [x] Monaco 전체 import를 13개 구문 언어 선택 등록으로 교체
- [x] TypeScript 13.31MB 등 Monaco worker bundle 제거
- [x] lazy 개발 화면 7.72MB → 5.16MB, 약 33% 감소
- [x] initial renderer 1.30MB, 개발 화면 5.50MB 상한과 worker 재유입 금지 bundle gate 추가

---

## 2026-08-25 정리 — 레거시 제거와 화면 축소

전수 조사로 확인된 이중 구현·정합성 어긋남을 해소했다.

### 제거한 것

- 레거시 에이전트 시스템: `/agents` CRUD, `/runs/*`, WS `/run/{agent_id}`,
  `janus_server/agents/*.yaml`, `runs/` 실행 기록 저장. UI는 오래 전부터 AgentProfile만
  사용했고 스토어의 레거시 액션·상태(spans/pastRuns/ws 등)도 함께 지웠다.
- 전역 워크스페이스 API: `GET/POST /workspace`, `/workspace/tree|file` 과
  `~/.janus/state.json` 영속화. Task-owned workspace가 유일한 경로다.
- 미연결 orchestration 계열: `workflow.py`, `pipeline.py`, `workflow_workspace.py`,
  `workflow_template.py`, `model_router.py`, `airgap.py`, `orchestration_bundle.py`와
  해당 테스트·verify 스크립트. server/runtime 어디서도 import되지 않았다.
- 죽은 프론트 코드: ApprovalCard, yaml.ts, 빈 evaluations/·operations/ 디렉토리,
  TaskSidebar↔TaskWorkspace에 이중 정의된 Task 상태 맵(`taskStatus.ts` 단일 소스로 통합).

### 남기고 명시한 것

- Evaluation Lab·Operations Monitor는 백엔드 API(`/evaluations/*`,
  `/operations/dashboard`)만 유지하고 화면은 없다. CHECKLIST §19에 각주로 명시했다.
- 세션 WebSocket은 인증 게이트 통과 즉시 accept하도록 바꿨다 — 도메인 검증 실패는
  accept 뒤 1008로 닫힌다.
- `library_skills/`와 `policies/`를 git 추적에 넣어 패키징 누락 위험을 제거했고,
  `forgeboard-*` 로컬 산출물을 .gitignore에 추가했다.
- 남은 비대칭: runtime `WORKER_ROLES`의 `researcher`(scout 별칭)는 TS union에 없지만
  adaptive가 해당 역할을 반환하지 않으므로 불일치는 발생하지 않는다.

## 2026-08-26 — write 워커 파일 소유권 임대

오케스트레이터-워커 전환 때 사라졌던 "같은 파일 병렬 쓰기 불가" 불변식을 현재 플로우로 이식했다.

- `create_worker`는 쓰기 능력(`write_file`/`edit_file`/`run_bash`)을 가진 비읽기 역할 워커에게
  배타적 파일 소유권 임대를 요구한다 (`ownership.FileOwnershipTable`, `runtime.write_ownership`).
- `owned_paths`(워크스페이스 상대 경로/디렉터리)를 선언하면 겹치지 않는 파티션끼리 병렬 write
  fan-out이 가능하고, 미선언 시 워크스페이스 전체(`*`) 배타 임대라 두 번째 동시 writer는
  `write_partition_conflict`로 스폰이 거부된다. 거부 응답에는 보유 임대 스냅샷과 wait_worker
  재시도 지침이 함께 담긴다.
- 임대 획득은 스폰 수락과 같은 임계영역에서 원자적이라, 충돌 거부가 seq·fingerprint·
  active_workers 회계를 소비하지 않는다. 워커 종료·취소·예산 소진·스레드 기동 실패 어느
  경로로도 임대는 해제된다.
- `_worker_view`에 `owned_partitions`이 노출되고 스폰 성공 시 `worker_write_lease_acquired`
  이벤트가 기록된다.
- 한계: run_bash 내부의 셸 쓰기까지 임대가 강제하지는 못한다 — 임대는 선언된 writer 간
  겹침을 차단하며, 셸 쓰기는 기존 승인 게이트에 의존한다.
- 테스트: `tests/test_worker_write_leases.py` 7건 — 루트 파티션(`*`) 의미론, 잘못된
  owned_paths 사전 거부(회계 무오염), 무선언 충돌→해제 후 재스폰, 비종속 파티션 병렬
  Barrier 실증, 선언 겹침 거부·비종속 통과, read-only 역할 무임대, view 노출·완료 시 해제.

### 후속: 워커 성과의 턴 경계 회수 (같은 날)

부모가 결과를 받기 전에 턴이 끝난 워커는 quiesce로 강제 종료되지만, 그 성과가 조용히
버려지지 않게 한다.

- quiesce 강제 종료 직전 각 워커의 스냅샷(상태·changed_paths·부분 result)을 레코드에 남긴다.
- wait/status/stop/send로 부모가 실제로 받아낸 기록은 `delivered`로 낙관하고, 미전달 종료
  기록은 다음 턴 시작에서 `[janus runtime]` 봉투의 운영 노트로 세션에 재주입된다
  (`worker_recovery_injected` 이벤트 동반). 같은 기록은 최대 3턴까지만 재노출한다.
- `on_worker_outcome` 콜백 시임을 추가했다 — 서버가 도메인 스토어를 연결하면 크래시 이후에도
  워커 성과 복원이 가능해진다(런타임은 여전히 저장소를 모른다). 종료 상태마다 정확히 1회
  발화하고, send 후속 재기동 시 초기화된다.
- 운영 노트는 user kind를 재사용해 UI·메시지 조립 경로 변경이 없으며, 봉투 문구 자체가
  사용자 발화가 아님을 선언한다.
- 테스트: `tests/test_worker_recovery.py` 3건 — quiesce→다음 턴 주입과 재노출 상한,
  delivered/stop 제외 규칙, 종료 훅 1회 보장과 followup 리셋.

### 후속: 의도 어휘 단일 원천과 경계 매칭 (같은 날)

- 신규 `intent.py`가 요청 의도 어휘의 유일한 원천이 된다 — runtime의 읽기 전용/변형 리스트와
  adaptive의 investigation 토폴로지 리스트를 이관했고, runtime `is_read_only_request`는
  호환 위임만 남긴다.
- 영어 낱말은 단어 경계 + 최소 활용형(s/es/ed/ing, 어미 -e 탈락 흡수)으로 매칭한다. 부분
  문자열 시절에는 fix ⊂ fixtures, edit ⊂ edition 오검 때문에 순수 조사 요청의 read-only
  도구 축소가 풀렸다. -tion 계 명사형(creation 등)은 의도 신호가 아니라고 명시적으로 제외하고,
  한글은 활용 결합 때문에 부분 문자열 매칭을 유지한다.
- 방향 불변식을 문서·테스트로 고정했다: 변형 신호가 하나라도 있으면 혼합 요청("조사하고 수정해줘")
  은 절대 read-only로 좁히지 않는다. 어휘가 없을 때의 기본값도 전체 도구다.
- adaptive 나머지 범주 어휘(test/planning 등)는 복수형 매칭을 위해 substring이 필요하므로 이관만
  하고 매칭 방식은 보존했다 — dispatch 분류 동작은 무변경이다.
- 테스트: `tests/test_intent.py` 14건 — 순수/혼합/무신호 판정, 경계·활용형 회귀(fixtures·edition),
  한글 substring, 런타임 위임, adaptive 공유 어휘 와이어링과 우선순위 보존, demo 자가검증.

### 후속: 워커 성과의 SQLite 영속 연결 (같은 날)

- `MIGRATION_25`로 `worker_outcomes` 테이블을 추가했다(schema v25) — task FK cascade,
  changed/owned 경로의 JSON 컬럼, 최신순 조회 인덱스를 갖는다.
- `DomainStore.record_worker_outcome/get/list_worker_outcomes`가 상태 검증, JSON 왕복,
  limit 상한을 담당한다.
- 런타임 종료 훅 페이로드에 task/workspace/session/dispatch 식별자를 보강했고, 세션 WS
  핸드셰이크가 `on_worker_outcome=store.record_worker_outcome`을 주입해 모든 종료 상태가
  크래시 내구적으로 기록된다. 훅 실패는 실행을 죽이지 않고 이벤트로 남는다.
- 새 AgentSession 시작 시 해당 Task의 최근 8건을 읽어 첫 턴에 `[janus runtime] Persisted
  worker outcomes …` 노트로 정확히 한 번 주입하고 소비한다 — 프로세스 재시작 후에도 이전
  워커가 무엇을 바꿨는지 오케스트레이터가 알 수 있다.
- 테스트: 스토어 왕복·검증·limit·재시작 복원 4건(`test_worker_outcome_store.py`),
  런타임 훅 ID 보강과 persisted 1회 주입 검증 확장(`test_worker_recovery.py`).

### 후속: 같은 역할 재스폰 상한의 엔진 강제 (같은 날)

- `budget.workers.role_limit`(기본 3)을 신설했다 — 초기 시도 뒤 교정 재시도 2회까지 허용하는
  페르소나 계약("두 번의 연속 실패 후 사용자에게 보고")을 모델 규율이 아니라 엔진이 강제한다.
- 스폰 수락 임계영역에서 역할별 카운트를 가산하고 상한 도달 시 `worker_role_budget`으로
  거부한다. 거부 응답에는 {role, spawned, role_limit, total_spawns, total_limit} 스냅샷과
  "다른 허용 역할로 더 작게 분할 위임하거나 사용자에게 보고하라"는 지침(직접 구현 금지)이
  담기며, 거부 자체는 seq·fingerprint 회계를 소비하지 않는다. `send_worker` 후속은 재스폰이
  아니므로 가산하지 않는다.
- 이전 실행에서 저장한 예산 JSON에 role_limit이 없어도 normalize가 기본값을 채워 호환된다.
- 테스트: 기본 상한 3회 도달·역할별 독립성(implementer 소진 후 scout 가능)·예산 오버라이드
  조임(role_limit=1) 3건(`test_worker_role_budget.py`).

## 2026-08-27 — 토큰 실측 기반 컨텍스트 압축 (P1-3)

- 압축 임계가 고정 chars 휴리스틱(4자/토큰 가정)에서 실측 보정으로 바뀌었다. 설정값
  `context_policy.max_chars`는 그대로 두고(스키마·프로필 변경 없음) 내부에서 토큰
  목표치(`max_chars / 4`)로 환산해 보관하며, 매 스텝 `usage.prompt_tokens` 실측이
  들어오면 "보낸 chars ÷ 실측 토큰" 비율을 EMA(0.7/0.3)로 보정해 임계를 다시 chars로
  환산한다. 한국어(1~2자/토큰)는 임계가 조여져 넘치기 전에 압축되고, 코드 위주
  컨텍스트는 느슨해져 불필요한 조기 압축이 준다.
- 비율은 [0.5, 8.0]으로 클램프해 이상한 usage 보고(0·음수·극단값)를 방어하고, 잘못된
  보고는 무시한다. prompt_tokens에 도구 스키마·챗 템플릿 오버헤드가 포함되는 편향은
  임계가 이르게 잡히는(넘침보다 안전한) 방향이라 그대로 둔다. 보정은 프로세스
  메모리에만 있고 재시작 시 4.0에서 다시 시작해 첫 호출 후 재보정된다.
- `context_stats`의 `//4` 추정치(`*_token_estimate`)와 스킬 로드 `prompt_tokens` 기록이
  보정 비율 기반으로 바뀌었고, `chars_per_token`·`token_calibration_samples`·
  `context_token_target`이 `context_window` 이벤트로 노출된다.
- 테스트: 무보정 시 기존 동작 동일·실측 비율로 조기 압축·클램프와 무시·보정 통계
  노출·run() 배선 5건(`test_context_calibration.py`), 전체 스위트 232건 통과.

### 후속: 안정 prefix를 서버 프롬프트 캐시에 연결 (같은 날, P1-4)

- 지금까지 `prompt_cache_probe`는 안정 prefix(system+summary) 재사용 "가능성"만 계측하고
  실제 서버 캐시는 없었다. mlx_vlm.server의 APC(Automatic Prefix Caching, 블록 단위
  KV 재사용)를 앱 기동 커맨드에서 `APC_ENABLED=1`로 켜서 프롬프트 캐시를 실제로 연결했다.
  `JANUS_APC=0`으로 끌 수 있다(블록 풀 등 세부는 APC_* 환경변수).
- 엔진이 usage의 `prompt_tokens_details.cached_tokens`(실측 적중)를 추출해 `usage`
  이벤트로 전파하고, 런타임 `node_usage`에 노드별로 누적한다 — prompt_tokens 대비
  비율이 곧 실측 캐시 적중률. APC 미지원 서버는 0으로 동작이 동일하다.
- P1-3 보정과의 상호작용 없음: 서버는 캐시 적중과 무관하게 `prompt_tokens`에 전체
  프롬프트 수를 보고하므로 chars/token 보정과 예산 회계는 그대로 정확하다.
- 테스트: cached_tokens 추출·미보고 시 0·run() usage 이벤트 전파 3건
  (`test_prompt_cache.py`), APC 기동 플래그와 opt-out 1건(`model-runtime.test.ts`).
  Python 235건·Electron 22건 전체 통과. 실기기 27B 서버에서의 적중률 확인은 다음
  QA 라운드에서 `usage.cached_tokens`로 본다.

### 후속: 반환 방향 핸드오프 예산 — 워커 보고 상한 (같은 날, P1-5)

- 스폰 방향은 이미 캡이 있었다(system 8K·task 6K·context 4K, 절단 이벤트 포함).
  반대 방향이 뚫려 있었다: `wait_worker`/`worker_status`가 돌려주는 `result`가
  무제한이라, 장황한 워커 보고가 가장 비싼 오케스트레이터 컨텍스트에 그대로 실렸다.
- `WORKER_RESULT_MAX_CHARS`(4,000, context 캡과 대칭)를 신설하고 `_worker_view`에서
  상한 초과 시 머리 3,000자(요약·계획)와 꼬리 800자(결론·검증)를 남기고 가운데를
  접는다. 절단 표식에 생략 분량과 "전문은 이벤트 로그·성과 스토어에 보존"을 명시하고,
  `result_chars`(원본 길이)·`result_truncated`를 뷰에 노출한다.
- 절단은 모델 컨텍스트 전용이다: 영속 훅(`on_worker_outcome`)은 전문을 복원해 받고,
  UI로 가는 `worker_state` 이벤트·quiesce 스냅샷(500자)·재시작 다이제스트(200자)는
  기존 경로 그대로다.
- 테스트: 상한 이하 통과·상한 초과 시 머리/꼬리 보존과 영속 전문 보존 2건
  (`test_worker_result_budget.py`), 전체 237건 통과.

### 후속: VRAM 슬롯 게이트에 실측 배선 (같은 날, P1-6)

- "VRAM 기반 정밀 슬롯 계산은 세마포어가 실측으로 병목일 때만 착수"가 체크리스트의
  게이트인데, 판정 함수 `assess_vram_sizing()`을 프로덕션에서 아무도 호출하지 않아
  게이트가 영원히 열릴 수 없었다 — 근거 데이터(리스 대기 실측)도 어디에도 없었다.
- 스케줄러가 model_generation 리스 승인 시점에 실제 대기시간을 bounded deque(512건)에
  기록한다. 다른 리소스(tool/verification)는 창에 섞지 않는다 — 잦은 tool 리스가
  실측 창을 밀어내면 p95가 흐려진다.
- `snapshot()`이 `vram_sizing` 판정(status/reason/p95_wait_ms/sample_count)을 싣고,
  Operations `/operations` 경로가 스냅샷을 그대로 통과시키므로 2초 주기로 노출된다.
  p95 ≥ 1초·표본 ≥ 10일 때만 `recommended`가 뜬다 — 그때가 슬롯 증설을 검토할 시점.
- 테스트: 경합 시 대기 실측 기록·tool 리스 미포함·판정 노출 1건(`test_scheduler.py`),
  전체 238건 통과. UI 배지 표시는 다음 디자인 라운드로 미룬다(데이터는 이미 API에 있음).

## 2026-08-27 — P2: P1 실측 신호의 UI 노출

P1에서 "다음 라운드로 미룬다"고 기록한 표시 작업을 정리했다. Operations Monitor는
화면이 의도적으로 없으므로(CHECKLIST §19) 살아있는 Task 화면에만 노출한다.

- **ContextInspector 실측화** — 토큰 한도가 `max_chars/4` 하드코딩이었는데, 이제
  `context_window` 이벤트의 실측 보정치(`context_token_target`)를 우선 사용하고
  이벤트가 없을 때만 휴리스틱으로 폴백한다. "토큰 보정" 행(`chars_per_token`자/tok ·
  실측 n회/휴리스틱)과 "캐시 적중" 행(usage 이벤트 누적 cached/prompt %, 미보고 시
  '미측정')을 추가했다.
- **TaskWorkspace 예산 스트립** — 토큰·단계·시간·워커 옆에 `캐시 · n%` 셀을 추가.
  APC 미보고 서버에서는 셀 자체가 안 보인다(0% 노이즈 방지). 전폭 행(예산 소진·MTP)은
  `col-span-full`로 바꿔 5열에서도 안전하다.
- **VRAM 판정 표시는 종결** — 화면 제거가 의도된 결정이므로 배지를 새로 만들지 않는다.
  판정은 `/operations/dashboard` 스냅샷으로 조회한다(P1-6).
- 테스트: ContextInspector 보정 표시·폴백 2건(`ContextInspector.test.tsx`),
  Electron 24건·tsc 통과.

## 2026-08-27 — P3: 실기기 검증과 v1.0.11 릴리스

- **실기기 27B 검증 PASS** — APC_ENABLED=1로 mlx_vlm.server를 띄우고 같은 세션에서
  두 턴을 실행: 2턴째 usage가 `cached_tokens=36/57`(1턴 프롬프트 전체 적중)을 보고했고,
  chars/token 보정이 4.0 휴리스틱에서 실측 3.57(2회)로 이동했으며, 스케줄러
  vram_sizing이 대기 실측 2건을 수집해 `insufficient_samples`로 정직하게 유보했다.
  P1-3·P1-4·P1-6의 실측 루프가 실기기에서 전부 닫혔다.
- v1.0.11: P1 라운드(역할 재스폰 상한, 토큰 실측 압축, APC 연결, 워커 보고 상한,
  슬롯 게이트 실측)와 P2(Task 화면 실측 표시)를 묶은 릴리스.

## 2026-08-27 — P4: 실사용 QA 라운드 (CHECKLIST §30-A P0)

v1.0.11 앱(실제 27B + APC)에 WS 드라이버로 P0 시나리오 3종을 실측 실행했다.
QA 안전 기준 준수: janus-qa-fixture 격리 worktree, 모든 승인 자동 거부, 종료 후 clean.

- **결함 발견·수정** — 서킷 브레이커가 `"error" in value`(키 존재)로 실패를 계수해,
  정상 worker view의 `error: None`까지 실패로 세어 wait_worker 3연속 후
  `circuit_break:wait_worker`로 턴이 통합 답변 없이 종료됐다(outcome partial).
  값 기반 판정으로 수정하고 회귀 테스트 2건 추가(`test_circuit_breaker.py`).
- **단일 워커 위임 통과** — 워커 1개(verifier, read_file만), queued→running→completed,
  finish_turn 통합 답변에 출처·핵심 포함, workers_started=1, worktree clean.
- **두 워커 분할 통과 (08-23 실패 건 해소)** — 목표 분리된 scout 2개 동시 실행,
  격리·통합·workers_started=2 일치, token_limit 붕괴·부모 edit_file 재현 안 됨.
  관찰: create_worker 스키마 외 인자 2회는 엔진이 거부하고 모델이 자가 교정했으며,
  scout 보고 품질 부실("!" 1자·step_limit partial)은 부모 보완 조사로 회복됐다.
- **거짓 실행 방지 통과** — 단순 질문에 워커 0개, 직접 답변.
- **잔존 결함(미해결 유지)** — 단순 읽기 위임에서 부모가 read_file을 1회 재실행
  (단일·두 워커 모두 재현). CHECKLIST에 재현 Task ID로 기록.

## 2026-08-27 — v1.0.12 릴리스

- P4 실사용 QA에서 발견한 서킷 브레이커 결함 수정(fc039f2 — error 키 존재가 아니라
  값으로 실패 계수)을 담은 패치 릴리스. 이 결함은 워커 위임 턴이 wait_worker 정상
  호출 3연속만으로 통합 답변 없이 종료되게 만들었다.
- QA 재검증: 단일 워커·두 워커 분할·거짓 실행 방지 P0 시나리오 통과 (§30-A 기록).

## 2026-08-27 — P5: 렌더링 근본 원인 해소와 QA 확장

- **read_file 재실행 결함의 진짜 원인 발견·수정** — fc039f2와 같은 키-존재 버그가 두
  층 더 있었다: `T.render()`가 `"error" in value`로 판정해 정상 worker view(`error:
  None`)를 전부 "ERROR: None"으로 렌더링했고, 모델은 워커 보고 본문을 아예 못 봤다.
  wait_worker를 반복하고 파일을 재독한 것은 모델 규율 문제가 아니라 이 결함의 증상.
  `tool_run_end` 상태 분류도 같은 패턴이었다. 둘 다 값 판정으로 수정(b2807b3).
- **실기기 재검증** — 단일 워커: create_worker→wait_worker→finish_turn 3콜로 종료,
  parent 재독 0회 (§30-A 미해결 항목 해소). 두 워커: 통합 답변 1000자(워커별 출처·상태
  인용), 남은 parent read 1회는 completed_partial 복구 계약대로의 정상 동작.
- **create_worker 필드 거부 정리** — 27B가 두 실행 연속 objective/allowed_scope 같은
  계약 필드를 발명해 TypeError 노이즈를 받았다. 허용 필드 목록과 "계약은 task 본문에"
  지침을 담은 `invalid_worker_fields` 거부로 교체 — 교정 비용(턴당 ~25초)을 줄인다.
- **P1 채팅 QA** — 후속 질문 기억 통과: 2턴째 도구 0회, 1턴 결과를 기억으로 정답.
- 테스트: render·tool_run_end·스폰 필드 거부 회귀 3건 추가, 전체 243건 통과.

## 2026-08-27 — v1.0.13 릴리스

- P5의 렌더링 근본 원인 수정(b2807b3 — worker view가 "ERROR: None"으로 렌더링되어
  모델이 워커 보고를 못 보던 결함)과 create_worker 필드 거부 정리(5945b9b)를 담은
  패치 릴리스. §30-A P0의 마지막 미해결 항목(read_file 재실행)이 해소됐다.

## 2026-08-27 — P6: 승인·취소·격리·중복 억제 QA (§30-A P0/P1)

실기기 시나리오 6종을 추가 실행해 8개 항목을 통과 처리했다.

- **승인 Reject** — 쓰기 승인 4건 전부 거부 시 워크스페이스 완전 불변. 관찰: 3연속
  거부가 서킷 브레이커로 턴을 끝내 최종 보고 없이 종료 — 보호는 정상, 보고 UX 여지.
- **승인 Allow** — 승인한 변경(multiply+테스트)만 적용, git diff 일치, run_bash 검증
  통과, 정직한 최종 보고. **발견**: §30-A의 "Task worktree/main checkout" 문구는
  MIGRATION_24(프로젝트 체크아웃 직접 작업, worktree 격리 폐기) 이전의 낡은 계약이라
  재정의했다. QA 쓰기 시나리오는 종료 후 fixture를 git으로 복구한다.
- **경계 차단** — /etc/hosts read_file이 jail에서 거부되고 사유가 답변에 표시. 셸 우회
  시도는 승인 게이트가 차단.
- **취소·복구** — 취소 후 세션 idle·lease 0, 같은 세션 후속 질문 정상.
- **중복 억제** — 동일 fingerprint 재스폰이 duplicate_worker_running으로 억제,
  이벤트 기록, 최종 답변이 억제를 정확히 보고(거짓 배치 주장 없음).
- **부수 확인** — 쓰기 요청이 이미 충족된 경우(subtract 기존재) 모델이 변경 없이
  "작업 불필요"로 정직 종료.
- 남은 §30-A: 상한 초과 queue 처리·queue 밀림 무유실(슬롯 5라 재현 곤란), WS 비정상
  종료·앱 재시작 복원 2건(반자동), 변경 패널 UI 일치·IME·화면 상태(수동).

### 후속: 서킷 브레이크가 턴 대신 도구를 회수한다 (같은 날)

- P6에서 관찰한 무보고 종료를 해소했다. 같은 도구 3연속 실패/거부 시 턴을 즉시
  끝내는 대신, 그 도구만 남은 턴에서 회수(스키마 제외)하고 "재시도·우회 금지,
  시도 내용과 미완료 사유를 보고하고 턴을 마무리하라"는 지시를 주입한다.
  finish_turn 경로가 살아 있어 사용자가 항상 최종 보고를 받는다.
- `done reason=circuit_break:<tool>` 대신 `circuit_break` 이벤트(tool·failures)로
  관측을 유지한다. 소비처는 없었음을 확인하고 교체했다.
- 실기기 재검증: 승인 3연속 거부 후 edit_file 회수 → finish_turn으로 "3회 연속
  거부되어 미완료, 승인 필요" 정확 보고, 워크스페이스 불변 유지.
- 테스트: 회수·보고 경로와 스키마 제외·지시 주입 검증으로 갱신, 전체 243건 통과.

## 2026-08-27 — v1.0.14 릴리스

- P6의 서킷 브레이커 UX 개선(e11a20a — 턴 종료 대신 도구 회수 + 최종 보고 보장)을
  담은 패치 릴리스. 승인·취소·격리·중복 억제 QA 8항목 통과 기록 포함.
