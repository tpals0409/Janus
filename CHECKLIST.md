# Janus 완성 체크리스트

목표: **제한된 로컬 하드웨어에서 로컬 에이전트가 가장 적은 시간·토큰·사용자 개입으로
검증된 코드 변경을 만들도록 지원하는 ADE 완성**

- 제품 기준: [PRODUCT.md](PRODUCT.md)
- 단계별 설계와 출구 조건: [ROADMAP.md](ROADMAP.md)
- 현재 구현 상태: [STATUS.md](STATUS.md)

## 진행 규칙

- 위에서 아래 순서로 진행한다. 뒤 단계가 앞 단계의 미완성 계약을 우회하지 않는다.
- 성능 변경 전 baseline을 남기고 동일 TaskSuite로 변경 후 결과를 비교한다.
- 각 큰 항목은 테스트·실모델 검증·문서 갱신 후 완료 처리한다.
- 외부 모델·구독형 CLI·원격 실행은 v1.0 범위에 넣지 않는다.

---

## P0. 측정 가능한 로컬 에이전트

### 1. 실행 계측 스키마

발견된 분기·문제:

- [x] scheduler 도입 전의 즉시 획득도 `queue_enter`/`lease_acquired`로 기록해 P2와 스키마를 공유
- [x] 영속 Task 모델 도입 전에는 실행별 추적 ID를 생성하고, 이후 외부 ID 주입을 허용
- [x] 병렬·중첩 model/tool 구간은 wall time에 단순 합산하지 않고 `active_turn`/`user_wait`만 배타 합산
- [x] P3 Verification Runner 전까지 smoke/TaskSuite acceptance 실행을 verification 구간으로 계측
- [x] 구간별 ms 선반올림으로 생긴 0.001ms 회계 오차를 ns 원값 합산으로 제거

- [x] monotonic clock 기반 duration 도입
- [x] queue 진입·lease 획득 시각 기록
- [x] model generation 시작·종료 기록
- [x] tool별 queue/run duration 기록
- [x] verification duration 기록
- [x] 사용자 입력 대기 시간 분리
- [x] task/session/dispatch/worker ID 귀속
- [x] token, worker 수, memory snapshot 기록
- [x] 한 Task의 전체 시간이 구간별로 설명되는 테스트

### 2. 실제 27B Smoke Harness

발견된 분기·문제:

- [x] headless 실행이 기존 8080 서버를 소유 프로세스로 오인해 종료하지 않도록 ownership 분리
- [x] worker `span_start` 직후 stop 요청이 cancel 등록보다 먼저 도착할 수 있는 race 제거
- [x] 실제 모델의 비결정적 문장 대신 marker·trace·상태를 조합해 성공 판정
- [x] 샌드박스에서 Metal device가 차단되는 환경을 감지하고 host 실행 경로·실패 로그를 명확히 제공

- [x] UI 없이 실제 MLX + Janus runtime을 실행하는 명령
- [x] 멀티턴 대화 검증
- [x] worker spawn 검증
- [x] 개별 worker stop 검증
- [x] turn cancel 후 다음 메시지로 대화 재개 검증
- [x] timeout과 실패 로그 저장
- [x] 자동 성공·실패 판정

### 3. TaskSuite v0

발견된 분기·문제:

- [x] 전역 `tools.WORKSPACE` 때문에 v0 반복은 순차 실행하고, 각 실행마다 새 복사본으로 reset
- [x] agent가 보고한 성공과 별도로 harness가 고정 acceptance command를 직접 실행
- [x] fixture 요구 변경 파일을 acceptance와 별도 검사해 우회 통과 방지
- [x] 의도적으로 실패하는 fixture 테스트가 제품 pytest 수집에 섞이지 않도록 testpaths 격리
- [x] host Metal 실행에서 model `run_bash` 자동 승인은 workspace 탈출이 가능하므로 제거하고 harness만 acceptance 실행

- [x] 단일 파일 버그 수정 fixture
- [x] 여러 파일 리팩터링 fixture
- [x] 조사 후 코드·테스트 수정 fixture
- [x] 각 Task objective와 constraints 고정
- [x] 각 Task acceptance command 고정
- [x] hardware/model/quantization/budget 조건 기록
- [x] 동일 Task 5회 반복 결과 저장

### 4. 현재 정책 Baseline

발견된 분기·문제:

- [x] 항상 주입되던 `create_worker`를 `none`/`fixed_one`/`autonomous` 정책으로 제한
- [x] 자동 승인 횟수와 실제 사용자 입력을 구분해 baseline에 기록
- [x] 비결정적 worker 선택은 정책 준수 여부와 acceptance 성공을 별도 지표로 저장
- [x] 조사 Task의 `fixed_one` 1회가 180초 timeout: 실패를 baseline에 유지하고 telemetry로 원인 분석
- [x] baseline 상세 trace가 상위·개별 JSON에 중복돼 28MB가 되는 문제를 semantic event 중심으로 압축

- [x] worker 없는 실행 5회
- [x] worker 1개 고정 실행 5회
- [x] 자율 worker 실행 5회
- [x] acceptance 성공률 비교
- [x] wall time과 분산 비교
- [x] prompt/completion token 비교
- [x] 사용자 개입 횟수 비교
- [x] baseline 결과표 저장

### 5. 프로세스 생명주기와 H5

발견된 분기·문제:

- [x] 단순 port-open 판정이 foreign/stale 점유를 정상 external 서비스로 오인하는 문제
- [x] 앱 종료가 SIGTERM 후 자식 종료를 기다리지 않아 orphan 여부를 확인할 수 없는 문제
- [x] supervisor 상태 머신이 Electron entry에 결합돼 restart/ownership 단위 테스트가 불가능한 문제
- [x] Node 실행 테스트는 통과하지만 test fixture readonly 필드 때문에 `tsc --noEmit`이 실패하는 문제
- [x] 최종 `git diff --check`에서 기존 문서 trailing whitespace 2건 제거

- [x] backend/MLX PID 소유권 기록
- [x] Janus가 시작한 프로세스만 종료
- [x] stale port와 정상 외부 프로세스 구분
- [x] 강제 종료 후 orphan 탐지
- [x] restart backoff와 반복 실패 상태
- [x] 종료·재시작 반복 테스트
- [x] smoke 종료 후 orphan process 0 확인

### P0 완료 감사

- [x] `STATUS.md`의 실모델·baseline·H5 미검증 문구를 실제 P0 완료 증거로 갱신
- [x] Python/Node/type/build/diff/P0 체크박스 최종 검증

---

## P1. ADE 작업 기반

### 6. 영속 도메인 모델

발견된 분기·문제:

- [x] 기존 agent YAML/run JSON은 파괴적으로 이동하지 않고 P1 DB 전환 동안 legacy 호환 입력으로 유지
- [x] WebSocket·worker thread 동시 접근을 위해 요청별 SQLite transaction, WAL, foreign key를 강제
- [x] Task/Dispatch/Session 상태 전이를 route가 아닌 도메인 저장소에서 검증
- [x] DB 경로와 migration을 import 시점 전역 상태로 고정하지 않아 테스트·앱 재시작 격리

- [x] Project schema
- [x] Task schema와 상태 전이
- [x] Workspace schema
- [x] Dispatch schema
- [x] AgentSession schema
- [x] AgentProfile과 ModelProfile schema
- [x] SQLite 또는 동등한 트랜잭션 저장소
- [x] schema version과 migration
- [x] 앱 재시작 후 상태 복원 테스트

### 7. WorkspaceContext

- [x] 전역 `tools.WORKSPACE` 제거
- [x] 모든 파일 도구에 workspace root 전달
- [x] 모든 셸·검증 호출에 workspace root 전달
- [x] 승인·이벤트에 task/workspace/dispatch ID 포함
- [x] 다른 workspace 접근 기본 거부
- [x] 두 context 병렬 실행 파일 격리 테스트

### 8. WorkspaceService

발견된 분기·문제:

- [x] 준비 중 branch/root 소유권을 즉시 저장하지 않으면 재시작 후 orphan branch가 남는 문제
- [x] safe archive와 dirty worktree 강제 제거를 하나의 삭제 경로로 다루면 사용자 변경을 잃는 문제
- [x] Task 수정 API의 `PATCH`가 CORS allow-methods에서 누락된 문제

- [x] repo와 base ref 검증
- [x] 충돌 없는 branch/worktree 생성
- [x] background create 진행 상태
- [x] 실패·retry·기존 worktree 복구
- [x] dirty/untracked/unmerged 검사
- [x] archive와 safe delete
- [x] branch 보존과 force delete 분리
- [x] M4 run 소유권·slug 재사용 문제 해결
- [x] main checkout 무수정 통합 테스트

### 9. Task 중심 UI

- [x] Project/Task sidebar
- [x] Task 생성: title/objective/acceptance/base ref
- [x] Todo/Preparing/Working/Needs You/Review/Failed 상태
- [x] Workspace 준비·실패·복구 표시
- [x] 안전한 archive/delete UI
- [x] 앱 첫 화면의 중심을 Agent에서 Task로 전환

### 10. Task–Runtime 연결

발견된 분기·문제:

- [x] 새 attempt 생성과 이전 실행 폐기를 한 transaction으로 묶어 늦은 이벤트의 소유권 경쟁 제거
- [x] 서버 재시작 중 `running`이던 Session/Dispatch/Task를 resumable 상태로 원자 복구
- [x] 활성 AgentSession이 있는 Workspace의 archive/force remove 기본 거부

- [x] 현재 오케스트레이터를 AgentSession 뒤로 이동
- [x] AgentProfile 선택과 저장
- [x] Dispatch attempt ID 도입
- [x] Session 상태와 로그 영속화
- [x] 오래된 Dispatch 이벤트 거부
- [x] Task 단위 start/send/cancel/stop/resume
- [x] 한 Task 취소가 다른 Task에 영향 없는 테스트

---

## P2. 로컬 자원 효율 엔진

### 11. ResourceScheduler

발견된 분기·문제:

- [x] 기존 병렬 worker 테스트가 model generation 동시 진입을 전제해 1-slot 계약으로 교정
- [x] Task A가 model slot을 점유한 동안 Task B가 완료된다는 테스트를 queue 후 독립 재개로 교정
- [x] 취소·scheduler 오류로 lease 획득 전 종료해도 queue interval이 닫히도록 `resource_queue_end` 추가

- [x] `model_generation` 자원 클래스와 기본 1-slot
- [x] `cpu_tool`, `io_tool`, `verification` 자원 클래스
- [x] 우선순위 queue
- [x] starvation 방지
- [x] 자원별 concurrency cap
- [x] 모델 생성과 독립 tool/verification 병렬 timeline 검증

### 12. ResourceLease

발견된 분기·문제:

- [x] scheduler 종료 시 카운터만 지우지 않고 active cancel → 실제 반환 → idle 대기 순서로 처리
- [x] 종료 시 active lease뿐 아니라 queued waiter가 모두 빠질 때까지 drain
- [x] FastAPI deprecated shutdown hook 대신 lifespan 계약으로 앱 종료 연결
- [x] queue 대기를 `capacity_exhausted`와 `higher_priority_waiter`로 구분해 Task UI에 표시

- [x] lease 획득·반환·timeout
- [x] queue 대기 원인 표시
- [x] 취소 시 자동 회수
- [x] 예외 시 자동 회수
- [x] 앱 종료 시 자동 회수
- [x] 모든 실패 테스트 후 활성 lease 0 확인

### 13. 실행 Budget

발견된 분기·문제:

- [x] AgentProfile 수정이 실행 중 attempt를 바꾸지 않도록 Dispatch 생성 시 budget snapshot 저장
- [x] 멀티턴·재시작 후에도 누적 사용량이 이어지도록 Dispatch usage를 매 turn 영속화
- [x] worker 요청 max_steps와 profile worker step limit 중 더 작은 값을 실제 worker budget으로 적용
- [x] worker cap 거부는 전체 Dispatch budget 소진으로 오인하지 않고 해당 spawn만 거부

- [x] Dispatch별 token/time/step budget
- [x] RuntimeWorker별 token/time/step budget
- [x] worker 총수와 동시 worker cap
- [x] queue deadline과 user priority
- [x] budget 소진 사유 기록
- [x] 한 Task 폭주가 다른 Task를 고갈시키지 않는 테스트

### 14. Worker Backpressure와 Context 효율

발견된 분기·문제:

- [x] message 수 기준 단순 절단이 assistant tool call과 tool result 쌍을 찢지 않도록 블록 단위 압축
- [x] 같은 task라도 worker name·role·system·context·tool subset이 다르면 중복으로 오인하지 않도록 fingerprint 구성
- [x] verifier가 요청한 쓰기 도구는 오케스트레이터 도구에 있어도 읽기 전용 교집합에서 제거
- [x] prompt cache hit을 알 수 없는 MLX 경로에서 실제 hit로 표시하지 않고 stable-prefix 재사용 후보만 계측
- [x] 샌드박스 Metal 차단 실패와 host 실모델 성공을 분리하고 owned process orphan 0 확인

- [x] queue와 자원 상태 기반 spawn 허용
- [x] 불필요한 worker 생성 억제
- [x] worker별 최소 context 전달
- [x] worker별 tool subset 유지
- [x] 결과 통합과 verifier 역할 분리
- [x] project summary와 session compaction
- [x] prompt/session cache 실험
- [x] acceptance 유지 + 입력 token 감소 검증

---

## P3. 검토 가능한 ADE MVP

### 15. Git ChangeSet

- [ ] base ref 대비 committed/staged/unstaged/untracked 변경
- [ ] 파일별 diff
- [ ] rename/delete 표시
- [ ] binary/large file 처리
- [ ] Git에서 매번 파생하고 별도 진실 원천을 만들지 않음

### 16. Verification Runner

- [ ] 프로젝트별 test/lint/typecheck 명령
- [ ] exit code, duration, stdout/stderr 요약
- [ ] agent 주장과 Janus 직접 검증 구분
- [ ] 수동 재실행
- [ ] verification concurrency 제한
- [ ] 실패 결과를 성공으로 표시하지 않는 테스트

### 17. Review Loop

- [ ] 파일·hunk 탐색
- [ ] 라인 코멘트
- [ ] 코멘트 일괄 revision 요청
- [ ] unresolved/resolved 상태
- [ ] accept/request changes/discard
- [ ] revision 후 diff 갱신
- [ ] 미병합 변경의 무확인 손실 방지

### 18. 최소 출하

- [ ] Janus에서 commit 생성
- [ ] Task branch에 결과 보존
- [ ] push 또는 local apply/cherry-pick 흐름
- [ ] main checkout 자동 수정 금지
- [ ] Task 생성 → 실행 → 검증 → 리뷰 → commit E2E 테스트

---

## P4. 극한 효율 최적화

### 19. Evaluation Lab

- [ ] baseline/candidate 실행
- [ ] AgentProfile·prompt·budget·worker policy A/B
- [ ] 성공률과 분산
- [ ] regression threshold
- [ ] hardware/model/quantization 조건 포함
- [ ] 결과 export

### 20. Adaptive Orchestration

- [ ] Task 특성별 worker 전략
- [ ] queue 상태 기반 fan-out
- [ ] 동적 budget
- [ ] 실패 유형별 retry
- [ ] implementer/verifier 분리
- [ ] baseline 대비 개선·악화 자동 판정

### 21. 운영 Dashboard

- [ ] Queue/Working/Needs You/Review
- [ ] model slot과 memory 상태
- [ ] generation/tool/verification timeline
- [ ] Task별 budget 소진율
- [ ] 10개 Task 동시 감독 검증

---

## P5. 제품 완성도

### 22. PR·CI 통합

- [ ] commit/push 상태
- [ ] GitHub PR 생성·연결
- [ ] CI checks와 실패 로그
- [ ] merge/close 상태
- [ ] merge 후 workspace archive 제안

### 23. 개발 표면

- [ ] worktree-scoped terminal
- [ ] Monaco editor와 file search
- [ ] worktree-scoped browser
- [ ] console/network capture
- [ ] UI 요소 → DOM/CSS/screenshot/source context
- [ ] 출력 복사, 키보드 단축키, 긴 로그 가상화

### 24. 견고성과 복구

- [ ] crash recovery
- [ ] DB migration 테스트
- [ ] disk full과 write failure
- [ ] worktree 충돌
- [ ] model OOM
- [ ] 대용량 diff/log
- [ ] 장시간 soak test
- [ ] 데이터 백업·초기화 정책

### 25. 배포 품질

- [ ] README와 제품 개념 설명
- [ ] 설치와 모델 준비 과정
- [ ] diagnostics와 로그 수집
- [ ] production packaging
- [ ] 버전·업데이트 정책
- [ ] fresh machine 설치 smoke

---

## v1.0 완료 조건

- [ ] 실제 27B TaskSuite 결과를 반복 재현할 수 있다.
- [ ] 두 Task가 격리된 worktree에서 교차 오염 없이 진행된다.
- [ ] 모델·도구 자원이 scheduler와 budget을 따른다.
- [ ] Task 생성부터 diff review와 commit까지 앱 안에서 완료된다.
- [ ] worker 정책 효율을 baseline 대비 숫자로 설명할 수 있다.
- [ ] 실패·취소·재시작 후 workspace, lease, process가 고아로 남지 않는다.
- [ ] 알려진 P0 보안·데이터 손실·거짓 상태 결함이 없다.
- [ ] 외부 모델 없이 핵심 제품 가치가 완성된다.

## 지금 시작할 작업

- [x] **P0-1 실행 계측 스키마 설계 및 테스트**
- [x] P0-2 실제 27B Smoke Harness
- [x] P0-3 TaskSuite v0
- [x] P0-4 현재 worker 정책 baseline
- [x] P0-5 backend/model orphan 정리
- [x] P1 Task/worktree/영속 AgentSession 기반
- [x] P2-11 ResourceScheduler
- [x] P2-12 ResourceLease 회수·timeout·대기 원인
- [x] P2-13 Dispatch/RuntimeWorker 실행 Budget
- [x] P2-14 Worker Backpressure와 Context 효율
- [x] R3 TaskSuite scheduler/budget/backpressure 처리량 재측정
- [ ] **R3 회귀 수정: Task 적합성 spawn gate와 실패 worker 결과 통합**
