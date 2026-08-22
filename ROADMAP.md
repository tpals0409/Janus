# Janus Local ADE 로드맵

이 로드맵의 목적은 현재 Qwen3.8-27B/MLX 오케스트레이터를 폐기하지 않고, 제한된 로컬
하드웨어에서 가장 적은 시간·토큰·사용자 개입으로 검증된 코드 변경을 만드는 ADE로 전환하는
것이다. 일정이 아니라 **측정 가능한 개선과 단계별 출구 조건**을 기준으로 한다.

제품 정의와 용어는 [PRODUCT.md](PRODUCT.md)를 따른다.
실제 진행 상태는 [CHECKLIST.md](CHECKLIST.md)에서 체크한다.

---

## 로드맵 원칙

1. 측정하지 않은 최적화는 도입하지 않는다.
2. 고정 하드웨어·모델·quantization·TaskSuite에서 baseline과 후보를 비교한다.
3. 모델 생성, queue 대기, tool I/O, verification, 사용자 대기를 별도 시간으로 기록한다.
4. worker 수나 token 수가 아니라 acceptance를 통과한 Task 처리량을 최적화한다.
5. 현재 MLX 경로를 깊게 최적화하며 외부 provider 가능성을 위해 미리 복잡하게 만들지 않는다.
6. Git이 코드 변경의 진실 원천이며 별도 diff 저장소를 만들지 않는다.
7. 동시성은 명시적 workspace와 ResourceLease가 생긴 뒤 허용한다.
8. 각 단계는 실제 저장소와 실제 27B를 사용하는 smoke 또는 benchmark로 끝난다.

## 최종 사용자 흐름

```text
Add Project
  → Create Task + Acceptance
  → Prepare Worktree
  → Select Local AgentProfile + Budget
  → Queue / Run
  → Observe / Answer / Cancel
  → Review Diff + Verification
  → Revise or Accept
  → Commit / Push / Apply
  → Feed result back into Evaluation
```

---

## R0. 제품 계약 고정 — 현재 단계

### 목표

Janus를 범용 agent aggregator가 아니라 local-agent efficiency ADE로 정의한다.

### 출구 조건

- [x] [PRODUCT.md](PRODUCT.md)가 제품 목표, 원칙, 도메인, 비목표를 정의한다.
- [x] `RuntimeWorker`와 ADE의 `AgentSession`을 구분한다.
- [x] 외부 모델·구독형 CLI·원격 실행을 핵심 로드맵에서 제외한다.
- [x] [STATUS.md](STATUS.md)가 현재 구현과 다음 실험을 기록한다.

---

## R1. 실제 27B Baseline과 계측 — 최적화의 기준선

### 사용자 결과

Janus가 느린 이유와 worker가 도움 되는지를 숫자로 볼 수 있다. 변경 전후 결과를 같은 조건에서
재현할 수 있으며 “빨라 보인다”가 아니라 acceptance·시간·token으로 판단한다.

### 구현 범위

#### 실제 모델 Smoke Harness

- 앱 UI 없이 실제 MLX 서버와 Janus runtime을 호출하는 재현 가능한 명령
- 멀티턴 세션, worker spawn, worker stop, turn cancel 후 재개
- timeout과 실패 로그 수집
- 모델/backend 시작·종료 소유권과 orphan 검사

#### Timing Event Schema

- `queued_at`, `lease_acquired_at`, `generation_started_at`, `generation_ended_at`
- tool call별 queue/run duration
- verification duration과 사용자 입력 대기
- session/dispatch/worker ID 귀속
- monotonic clock 기반 duration

#### Local Resource Snapshot

- model/quantization/context/budget
- prompt/completion token
- process RSS 또는 사용 가능한 로컬 메모리 지표
- model server warm/cold 상태
- hardware와 Janus version

#### TaskSuite v0

최소 세 종류의 작은 실제 저장소 fixture와 자동 acceptance command.

- 단일 파일 버그 수정
- 여러 파일 리팩터링
- 조사 후 코드·테스트 수정
- 각 Task에 objective, constraints, verification command, timeout

#### Baseline Matrix

- 오케스트레이터 단독
- 고정 worker 1개
- 현재 모델이 자율적으로 worker를 선택
- 동일 seed가 불가능하면 반복 횟수와 분산을 함께 기록

### 의도적으로 제외

- Task board UI
- worktree 병렬 실행
- 새로운 scheduling 알고리즘
- prompt 자동 튜닝

### 출구 조건

- [x] 실제 27B smoke가 한 명령으로 반복 실행된다.
- [x] 멀티턴, worker spawn, stop, cancel 후 재개 결과가 자동 판정된다.
- [x] 모델 queue/generation/tool/verification 시간이 분리되어 기록된다.
- [x] 같은 TaskSuite를 5회 실행해 성공률과 wall-time 분산을 볼 수 있다.
- [x] worker 없음/고정 worker/자율 worker baseline 표가 생성된다.
- [x] 종료 후 Janus가 시작한 backend/model orphan process가 남지 않는다.

---

## R2. Task와 Worktree 기반 — 안전한 작업 경계

### 사용자 결과

프로젝트에서 Task를 만들면 Janus가 별도 branch/worktree를 준비한다. 앱을 재시작해도 상태가
복원되고 두 작업의 파일 변경이 섞이지 않는다.

### 구현 범위

#### 영속 모델

- Project: repo path, default ref, worktree root, setup/verification command
- Task: objective, acceptance, status, priority
- Workspace: path, branch, base ref, lifecycle status
- Dispatch와 AgentSession identity
- SQLite 또는 동등한 트랜잭션 로컬 저장소와 schema migration

#### WorkspaceService

- 저장소와 base ref 검증
- 충돌 없는 branch/worktree 생성
- background create, progress, retry
- existing worktree 발견·복구
- dirty, untracked, unmerged commit 계산
- archive, safe delete, force delete 분리

#### WorkspaceContext

- 전역 `tools.WORKSPACE` 제거
- 파일·셸·검증 호출에 명시적 workspace root 전달
- 실행·승인·저장 키에 task/workspace/dispatch ID 포함
- 다른 workspace 경로 접근 기본 거부

#### 최소 UI

- Agent 목록 중심 sidebar를 Project/Task 중심으로 교체
- Task 생성: title, objective, acceptance, base ref
- `preparing`, `working`, `needs-input`, `review`, `failed` 표시
- Workspace 복구와 안전 삭제 동작

### 출구 조건

- [x] 동일 프로젝트의 Task 두 개가 서로 다른 worktree/branch를 가진다.
- [x] 두 WorkspaceContext의 도구를 병렬 실행해도 파일이 섞이지 않는다.
- [x] main checkout은 agent 실행으로 수정되지 않는다.
- [x] 앱 재시작 후 Project, Task, Workspace 상태가 복원된다.
- [x] dirty 또는 미병합 workspace 삭제는 기본 거부된다.
- [x] 생성 실패가 `preparing`에 영구 고착되지 않고 복구 동작을 제공한다.

---

## R3. Resource Scheduler와 Budget — 로컬 처리량 제어

### 사용자 결과

여러 Task를 queue에 넣어도 단일 로컬 모델이 과부하되지 않는다. Janus는 모델 생성은
직렬화하고 독립적인 tool I/O와 verification은 안전하게 겹치며 각 작업의 예산을 지킨다.

### 구현 범위

#### Local Runtime 계약

- start, send, cancel, stop, resume
- session 상태와 message/tool/approval/usage 이벤트
- R1 timing schema를 모든 실행 경로에 적용

#### ResourceScheduler

- `model_generation`, `cpu_tool`, `io_tool`, `verification` 자원 클래스
- 모델 생성 기본 1-slot와 우선순위 queue
- ResourceLease 획득·반환·timeout
- CPU/tool/verification concurrency cap
- 취소·예외·앱 종료 시 lease 회수

#### Budget

- Dispatch와 RuntimeWorker별 token/time/step budget
- worker 수와 동시 worker cap
- queue deadline과 user priority
- budget 소진 사유를 결과에 기록

#### Model Lifecycle

- warm model server 유지와 idle 정책
- memory pressure 감지
- 시작한 backend/model PID 소유권
- crash, stale port, orphan reconciliation

### 출구 조건

- [x] 두 Task가 들어와도 model generation이 설정된 slot을 넘지 않는다.
- [x] 모델 생성 중 독립 tool I/O 또는 verification이 겹치는 timeline이 확인된다.
- [x] 높은 우선순위 Task가 starvation 없이 먼저 lease를 얻는다.
- [x] 한 Task 취소가 다른 Task의 session과 lease에 영향을 주지 않는다.
- [x] token/time/worker budget 초과가 해당 Dispatch만 종료한다.
- [x] 모든 예외·취소 테스트 후 ResourceLease가 0으로 돌아온다.
- [x] R1 TaskSuite에서 baseline 대비 처리량 변화가 수치로 보고된다.

---

## R4. ChangeSet, 검증, 리뷰 — ADE MVP

### 사용자 결과

에이전트가 끝나면 답변이 아니라 실제 diff와 verification이 결과로 나타난다. 사용자는 수정
요청을 보내거나 결과를 수락·폐기하고 branch에 보존할 수 있다.

### 구현 범위

#### ChangeSet

- base ref 대비 committed, staged, unstaged, untracked 변경
- 파일별 diff, rename, 삭제, binary/large file 처리
- Git 상태에서 매번 파생

#### Verification

- 프로젝트별 검사 명령
- exit code, duration, stdout/stderr 요약
- agent 주장과 Janus가 직접 실행한 검증 구분
- 사용자가 재실행 가능

#### Review

- 파일·hunk 탐색과 라인 코멘트
- 여러 코멘트를 한 revision message로 전송
- unresolved/resolved 상태
- accept, request changes, discard

#### 최소 출하

- Janus에서 commit 생성
- branch에 결과 보존
- main checkout으로 자동 복사하지 않음

### ADE MVP 출구 조건

- [x] Task 생성부터 worktree, agent 실행, diff review, commit까지 앱 안에서 완료된다.
- [x] review는 agent 답변과 독립적으로 실제 Git 변경을 정확히 보여준다.
- [x] 실패한 verification을 성공으로 표시하지 않는다.
- [x] review 코멘트 후 같은 workspace에서 수정하고 diff가 갱신된다.
- [x] 결과 폐기 시 미병합 변경이 확인 없이 손실되지 않는다.
- [x] 두 Task의 end-to-end 실행에서 변경 교차 오염이 없다.

---

## R5. Adaptive Orchestration과 Evaluation Lab

### 사용자 결과

Janus가 Task 특성과 현재 자원 상태에 따라 worker 수, 컨텍스트, 도구, budget을 조절한다.
사용자는 정책 변경이 실제 품질·시간·개입 비용을 개선했는지 재현 가능한 평가로 판단한다.

### 구현 범위

- Needs You / Working / Queue / Review dashboard
- 동일 Task의 여러 Dispatch와 후보 workspace
- model/profile/prompt/budget/worker-policy A/B
- worker에 필요한 최소 context만 전달
- worker fan-out, concurrency cap, backpressure 정책
- 결과 통합과 verifier 역할 분리
- Task dependency와 decision gate의 최소 DAG
- R1 TaskSuite 확장과 regression threshold
- context compaction, project summary, prompt/session cache 실험
- ChangeSet, acceptance, wall time, token, memory, attention 비교

### 출구 조건

- [x] 같은 Task를 두 AgentProfile로 실행하고 결과와 비용을 비교할 수 있다.
- [x] worker 사용이 없는 baseline 대비 개선 또는 악화가 자동 판정된다.
- [x] prompt/profile 변경이 acceptance를 악화시키면 regression으로 표시된다.
- [x] 모든 결과에 hardware, model, quantization, budget 조건이 기록된다.
- [x] 선택한 정책이 실제 Task 실행의 기본 AgentProfile로 승격된다.
- [x] 10개 Task의 queue와 attention 상태를 각 화면에 들어가지 않고 감독할 수 있다.

---

## R6. Ship과 협업 통합

### 사용자 결과

검토된 Task를 push하고 PR을 만들며 CI와 리뷰 상태를 Janus에서 추적한다.

### 구현 범위

- commit/push와 upstream 상태
- GitHub 우선 PR 생성·연결
- CI checks와 실패 로그
- PR 리뷰 상태와 재작업 session
- merge 후 Task 완료와 workspace archive 제안

### 출구 조건

- [x] review된 ChangeSet에서 PR을 만들고 URL과 checks를 Task에 연결한다.
- [x] push·인증·충돌 실패가 데이터 손실 없이 복구 가능하다.
- [x] merge/close가 반영되어도 로컬 branch를 임의 삭제하지 않는다.

---

## R7. 통합 개발 표면

### 사용자 결과

Task를 떠나지 않고 파일 편집, terminal, 앱 미리보기와 UI 피드백을 수행한다.

### 구현 범위

- worktree-scoped terminal과 split
- Monaco editor, file search, output copy
- worktree-scoped browser session
- console/network capture
- 요소 선택 → DOM/CSS/screenshot/source context
- 키보드 단축키와 긴 로그 가상화

### 출구 조건

- [x] terminal/editor/browser 탭이 명확한 Task workspace에 귀속된다.
- [x] Task 전환 후 탭과 session 상태가 복원된다.
- [x] browser profile과 저장소 context가 다른 Task와 섞이지 않는다.

---

## 현재 우선순위

### 완료 — R1/R2와 R3 ResourceScheduler

1. timing event schema, 실제 MLX smoke와 TaskSuite baseline
2. Task/worktree/WorkspaceContext와 영속 AgentSession
3. process-wide model generation 1-slot
4. CPU/IO/verification concurrency cap
5. priority aging과 독립 verification overlap

### 완료 — R3 ResourceLease

1. lease timeout과 queue 대기 원인
2. 취소·예외·앱 종료 시 자동 회수
3. 앱 종료 시 active cancel과 실제 idle 대기
4. Task runtime queue 원인 표시

### 완료 — R3 Budget

1. Dispatch/RuntimeWorker token·time·step budget
2. worker 총수·동시 worker cap
3. queue deadline과 user priority
4. budget 소진 사유 영속 기록

### 완료 — R3 Worker Backpressure와 Context 효율

1. queue와 자원 상태 기반 worker spawn 허용
2. 중복 worker 억제와 완료 결과 재사용
3. worker 최소 context·tool subset과 read-only verifier 역할 분리
4. tool call/result 블록을 보존하는 project summary/session compaction
5. stable-prefix cache 후보와 입력 절감량 계측

### 완료 — R3 처리량 재측정

1. scheduler/lease/budget/backpressure 적용 후보를 R1과 같은 TaskSuite로 실행
2. acceptance, wall time, prompt/completion token, queue wait 비교
3. regression이면 기본 정책 승격을 보류하고 원인 trace 저장

결과는 40/45로 R1의 44/45보다 acceptance가 악화됐다. `none`은 15/15를 유지했지만 평균
wall time이 9% 증가했고, `autonomous`는 15/15를 유지하면서도 불필요한 worker 선택으로 평균
wall time이 63% 증가했다. `fixed_one`은 작은 두 fixture에서 18~23% 빨라졌지만 조사 Task가
0/5로 실패했다. 따라서 후보를 기본 정책으로 승격하지 않는다.

### 지금 — R3 회귀 수정

1. Task 형태와 예상 작업량으로 첫 worker spawn의 최소 이득을 판정한다.
2. worker budget 소진 시 partial result를 버리지 않고 오케스트레이터가 통합하게 한다.
3. fixed-one worker 실패 뒤 같은 spawn을 반복하는 loop를 차단한다.
4. 실패 submatrix 5회와 전체 TaskSuite 순서로 회귀를 재검증한다.

### 현재 커밋하지 않는 선택적 확장

- 외부 API 모델
- Codex·Claude Code 같은 구독형 CLI agent
- SSH/원격 Janus runtime
- 외부 cloud VM

이 항목은 로컬 TaskSuite에서 병목이 확인되고 외부 실행이 그 병목을 해결한다는 근거가 생긴 뒤
별도 제품 결정으로 연다. 현재 도메인과 UI는 이를 미리 가정하지 않는다.

## 다음 코드 작업

다음 작업은 **R3 TaskSuite에서 발견된 worker 회귀 수정**이다.

1. `investigate_code_tests / fixed_one`의 worker token 소진 결과를 partial result로 통합한다.
2. worker 실패 후 반복 spawn을 억제하되 오케스트레이터가 직접 완료할 수 있는 결과를 준다.
3. `autonomous`가 작은 병렬 이득 없이 worker를 선택하는 것을 Task 적합성 gate로 제한한다.

acceptance 44/45 이상을 회복한 뒤 R4의 Git ChangeSet 구현으로 넘어간다.
