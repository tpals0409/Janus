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
- [x] 자연어 목표 위임에서 내부 Task의 title/objective/acceptance/base ref 자동 생성
- [x] 저장소 종류·프로젝트 검증 설정·현재 브랜치 기반 작업 계약 추론
- [x] 위임 후 worktree 준비·AgentSession 시작·첫 목표 전송 자동 연결
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
- [x] worker token/step 소진을 재사용 가능한 `completed_partial` 결과로 통합하고 반복 spawn 차단
- [x] 모델이 schema에 없는 도구를 호출해도 node tool subset 실행 경계에서 거부
- [x] 단일 model slot의 tight fixed-one implementer를 계측 가능한 1-step read-only scout로 전환
- [x] 명시적 위임·profile override 없는 autonomous worker를 생성 전 억제
- [x] fixed-one 15회에서 독립 acceptance·변경 파일·worker 정책 15/15 검증
- [x] 120초 제한 마지막 1회의 acceptance 후 응답 0.12초 초과를 결과에서 숨기지 않음

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

- [x] base ref 대비 committed/staged/unstaged/untracked 변경
- [x] 파일별 diff
- [x] rename/delete 표시
- [x] binary/large file 처리
- [x] Git에서 매번 파생하고 별도 진실 원천을 만들지 않음

### 16. Verification Runner

- [x] 프로젝트별 test/lint/typecheck 명령
- [x] exit code, duration, stdout/stderr 요약
- [x] agent 주장과 Janus 직접 검증 구분
- [x] 수동 재실행
- [x] verification concurrency 제한
- [x] 실패 결과를 성공으로 표시하지 않는 테스트

### 17. Review Loop

- [x] 파일·hunk 탐색
- [x] 라인 코멘트
- [x] 코멘트 일괄 revision 요청
- [x] unresolved/resolved 상태
- [x] accept/request changes/discard
- [x] revision 후 diff 갱신
- [x] 미병합 변경의 무확인 손실 방지

### 18. 최소 출하

- [x] Janus에서 commit 생성
- [x] Task branch에 결과 보존
- [x] push 또는 local apply/cherry-pick 흐름
- [x] main checkout 자동 수정 금지
- [x] Task 생성 → 실행 → 검증 → 리뷰 → commit E2E 테스트

---

## P4. 극한 효율 최적화

### 19. Evaluation Lab

> 2026-08-25 UI 재편: Evaluation Lab 화면은 제거됐고 백엔드 `/evaluations/*` API와
> 승격 게이트(`agent-profile/promote`)만 유지된다. 비교 결과는 API/CLI로 확인한다.

- [x] baseline/candidate 실행
- [x] AgentProfile·prompt·budget·worker policy A/B
- [x] 성공률과 분산
- [x] regression threshold
- [x] hardware/model/quantization 조건 포함
- [x] 결과 export

### 20. Adaptive Orchestration

- [x] Task 특성별 worker 전략
- [x] queue 상태 기반 fan-out
- [x] 동적 budget
- [x] 실패 유형별 retry
- [x] implementer/verifier 분리
- [x] baseline 대비 개선·악화 자동 판정

### 21. 운영 Dashboard

- [x] Queue/Working/Needs You/Review
- [x] model slot과 memory 상태
- [x] generation/tool/verification timeline
- [x] Task별 budget 소진율
- [x] 10개 Task 동시 감독 검증

---

## P5. 제품 완성도

### 22. PR·CI 통합

- [x] commit/push 상태
- [x] GitHub PR 생성·연결
- [x] CI checks와 실패 로그
- [x] merge/close 상태
- [x] merge 후 workspace archive 제안

### 23. 개발 표면

- [x] worktree-scoped terminal
- [x] Monaco editor와 file search
- [x] worktree-scoped browser
- [x] console/network capture
- [x] UI 요소 → DOM/CSS/screenshot/source context
- [x] 출력 복사, 키보드 단축키, 긴 로그 가상화

### 24. 견고성과 복구

- [x] crash recovery
- [x] DB migration 테스트
- [x] disk full과 write failure
- [x] worktree 충돌
- [x] model OOM
- [x] 대용량 diff/log
- [x] 장시간 soak test
- [x] 데이터 백업·초기화 정책

### 25. 배포 품질

- [x] README와 제품 개념 설명
- [x] 설치와 모델 준비 과정
- [x] diagnostics와 로그 수집
- [x] production packaging
- [x] 버전·업데이트 정책
- [x] fresh machine 설치 smoke

---

## v1.0 완료 조건

- [x] 실제 27B TaskSuite 결과를 반복 재현할 수 있다.
- [x] 두 Task가 격리된 worktree에서 교차 오염 없이 진행된다.
- [x] 모델·도구 자원이 scheduler와 budget을 따른다.
- [x] Task 생성부터 diff review와 commit까지 앱 안에서 완료된다.
- [x] worker 정책 효율을 baseline 대비 숫자로 설명할 수 있다.
- [x] 실패·취소·재시작 후 workspace, lease, process가 고아로 남지 않는다.
- [x] 알려진 P0 보안·데이터 손실·거짓 상태 결함이 없다.
- [x] 외부 모델 없이 핵심 제품 가치가 완성된다.

---

## v1.1. GitHub Skill Compiler

### 26. 스킬 도메인과 변환

- [x] Skill·SkillVersion·AgentProfileSkill·SessionSkillSnapshot migration
- [x] Codex·Claude Code `SKILL.md`와 resource 발견
- [x] 변수·도구·실행 context를 Janus IR로 결정적 변환
- [x] native·partial·adapter-required·blocked 호환성 보고
- [x] 원본·변환물·출처·content hash를 불변 version으로 보존

### 27. GitHub 가져오기와 보안

- [x] GitHub repository/tree URL 분석과 commit SHA 고정
- [x] 설치 전 스킬·라이선스·capability·경고 미리보기
- [x] 하위 스킬 복수 선택 설치와 preview revision 재검증
- [x] ZIP path escape·symlink·중복 경로·파일/아카이브 용량 차단
- [x] 설치 후 기본 비활성과 shell·network 승인 경계

### 28. 로컬 에이전트 적용

- [x] 스킬 보관함과 AgentProfile 선택 UI
- [x] 호환되는 스킬만 자동·수동 활성화
- [x] 세션 시작 시 활성 version snapshot
- [x] 소형 catalog 선주입과 `load_skill`·`read_skill_resource` 지연 로딩
- [x] 실제 Qwen3.8 27B가 카탈로그에서 `load_skill` tool call을 선택
- [x] `openai/skills` `skill-creator`를 commit SHA로 고정해 다운로드·라이선스 파일·컴파일 검증
- [x] Python 166 tests(+migration subtest 16), Electron main 25 tests, renderer 10 tests,
      TypeScript/build 통과
- [x] 실제 GitHub 저장소를 이용한 앱 수동 QA

### 29. AgentProfile 프롬프트와 컨텍스트

- [x] 프롬프트 탭을 AgentProfile system prompt 저장 API와 연결
- [x] 프로필별 최대 용량·최근 블록·요약 용량 정책
- [x] Task 목표·수용 검증·workspace 경로 포함 선택
- [x] Task 시작 시 AgentProfile snapshot으로 기존 실행 불변 보장
- [x] Task 실행에 컨텍스트 소스·포함 상태·token 추정·최신 window 표시
- [x] 레거시 에이전트 생성·삭제·YAML 그래프 편집 진입점 제거
- [x] AgentProfile 루트와 실제 Task worker span으로 읽기 전용 실행 그래프 구성
- [x] 중복 전역 컨텍스트 메뉴 제거
- [x] migration·API·runtime snapshot 회귀 테스트
- [x] 실제 앱에서 프롬프트 저장·정책 적용·검사기 수동 QA

### 30. 공식 Janus 데스크톱 디자인 시스템

- [x] 제품 도메인과 충돌하던 Tools·Context·Graph·Inspector 정의 교정
- [x] 공식 SVG 심볼과 Graphite 글로벌 token 적용
- [x] 공식 심볼 기반 macOS 앱 아이콘과 packaged bundle 적용
- [x] Button·Tabs·Field·Status·Panel·Dialog 공통 primitive 도입
- [x] Title bar·icon navigation·resource sidebar·status bar 구조 개편
- [x] Task·AgentProfile·Evaluation·Monitor 화면을 compact panel UI로 통일
- [x] `⌘K` command palette와 keyboard focus·reduced motion 적용
- [x] TSX raw hex 제거와 accent의 runtime signal 한정
- [x] TypeScript·Electron main test·production build·Python 회귀 테스트
- [ ] 실제 Electron 앱 전체 화면 수동 QA

### 30-A. 채팅·오케스트레이션 실사용 QA

완료 근거는 채팅 기록만으로 판단하지 않는다. Task·Dispatch·AgentSession 상태, 실행 이벤트,
워커 그래프, worktree 변경, 검증 결과가 서로 일치해야 한다.

#### QA 안전 기준

- [x] 실제 진행 중인 프로젝트를 오케스트레이션 QA 대상에서 제외한다.
- [x] 원격 저장소가 없는 전용 `janus-qa-fixture` 저장소를 Janus에 등록한다.
      — 기준 commit `b1f354d`, dependency-free unit test 2개 통과
- [x] 각 QA 시작 전 선택 프로젝트가 `janus-qa-fixture`인지 확인한다.
      — 2026-08-23 두 워커 QA 시작 전 선택 프로젝트와 격리 worktree 경로 확인
- [x] QA가 끝날 때 원본 fixture의 `git status`가 깨끗한지 확인한다.
      — 두 워커 QA 중 worktree의 `README.md`만 변경됐고 원본 fixture는 clean 유지

실행 순서는 `단일 워커 → 두 워커 분할 → 거짓 실행 방지 → 권한·복구 → 채팅 경계 조건`이다.
입력기 편의성보다 모델의 위임 판단과 실행 진실성을 먼저 검증한다.

#### P0. 단일 워커 위임

- [x] `README.md를 읽는 verifier 워커 1개를 배치하고 결과를 요약해`를 전송한다.
- [x] 현재 메시지의 명시적 워커 요청을 인식하고 실제 워커를 정확히 1개 생성한다.
- [x] 실행 그래프가 워커의 `대기 → 실행 → 완료/실패` 상태를 실제 이벤트와 동일하게 표시한다.
- [x] 워커에는 역할에 필요한 읽기 도구와 최소 컨텍스트만 전달된다.
- [x] 오케스트레이터가 워커 결과를 기다린 뒤 최종 답변에 출처와 핵심 결과를 통합한다.
- [x] `workers_started=1`, span 시작·종료, Task·Dispatch·Session 최종 상태가 서로 일치한다.
      — 2026-08-23 Task `task_65aa12e2f7dc4f3e81593bc4949c5793`, worker span
      `w1-readme-reader` success, session `idle`, 실제 README 내용 일치
- [x] 단순 읽기 위임에서는 오케스트레이터가 워커와 동일한 `read_file`을 다시 실행하지 않는다.
      — 2026-08-27 해소: 근본 원인은 모델 규율이 아니라 렌더링 결함이었다.
      `T.render()`가 error 키 존재로 판정해 worker view(`error: None`)를 전부
      "ERROR: None"으로 렌더링 → 모델이 워커 보고를 못 보고 재독(b2807b3에서 값 판정으로
      수정). 수정 후 Task `task_0deec509579647a5b980914f9fca1ce4`에서
      create_worker→wait_worker→finish_turn 3콜, parent 재독 0회 확인.
      두 워커 재검증(`task_59d359bcf7a44bda8142cc7e7a509496`)의 parent read 1회는
      completed_partial 복구 계약(변경 경로 1회 읽기)에 따른 정상 동작

#### P0. 두 워커 분할과 결과 통합

- [x] `워커 2개를 배치해. 첫 번째는 README 구조를, 두 번째는 테스트 구성을 조사하고 결과를 합쳐`를 전송한다.
- [x] 서로 다른 역할의 워커가 정확히 2개 생성되고 그래프에서 별도 노드로 보인다.
      — 2026-08-27 재검증 통과: 역할은 모두 scout이나 목표(README/테스트)가 분리된
      별도 노드 2개(w1-test-scout, w2-readme-scout) 생성
- [x] 독립 작업은 함께 스케줄되며, 단일 로컬 모델 슬롯 대기는 유실이 아닌 명시적 queue 상태로 표시된다.
      — 슬롯 5에서 두 워커 동시 running, resource_queue 이벤트 정상
- [x] 각 워커의 도구 호출·결과·종료 상태가 상대 워커와 섞이지 않는다.
- [x] 오케스트레이터가 두 결과를 모두 받은 뒤 중복을 제거하고 하나의 답변으로 통합한다.
      — 워커 보고가 부실("!"·step_limit partial)했지만 부모가 보완 조사 후 통합 답변 735자 생성
- [x] `workers_started=2`, 실행 이벤트, 그래프, 최종 답변이 실제 실행 수와 일치한다.
- [x] 읽기 전용 조사 지시에서는 오케스트레이터와 워커 모두 파일을 수정하지 않는다.
      — worktree clean, 승인 요청 0건

      — 2026-08-27 재검증 통과: Task `task_97b0558c21b74077b8b0c50d48cbc6b2`,
      Session `session_3136d4d7288f4609b65735c73f023043` (v1.0.11 + circuit breaker fix).
      08-23의 token_limit 실패·fixed_one 억제·부모 edit_file 문제는 재현되지 않음.
      새 관찰 2건: (a) create_worker에 스키마 외 인자(objective 등)를 보내 인자 오류
      2회 후 자가 교정 — 엔진 거부는 정상 동작. (b) scout 보고 품질 부실(w2 결과
      "!" 1자, w1 step_limit partial) — 부모가 보완 조사로 회복, 모델 품질 이슈로 기록.
      아래는 08-23 최초 실패의 기록이다.
      — 2026-08-23 실패 재현: Task `task_0fc0f78675764e65883fb17d0d447a41`,
      Session `session_8fc549395b5d484f834b884f7e2a453d`. `test-researcher` 1개만 생성된 뒤
      `worker:w1-test-researcher:token_limit`으로 실패했고, 목표가 다른 `readme-researcher`는
      `worker_policy_fixed_one`으로 억제됐다. 부모는 `edit_file`을 2회 실행해 격리 worktree의
      `README.md`를 수정했다. 워커 실패·억제 뒤 UI가 `작업 중 / 연결 준비`에 오래 머물다가 최종적으로
      `budget exhausted: dispatch:token_limit` 실패로 종료됐으며 통합 답변은 생성하지 못했다.

#### P0. 거짓 실행 방지와 Backpressure

- [x] 워커를 요구하지 않은 단순 질문에는 불필요한 워커를 만들지 않는다.
      — 2026-08-27 통과: Task `task_89a836aae9ef4814b9073592d64f79b8`, 워커 0개·finish_turn 직접 답변
- [x] 같은 역할·같은 목표의 중복 워커 요청은 억제하고 억제 사유를 실행 기록에 남긴다.
      — 2026-08-27 통과: Task `task_9bc0f1213bc84bcba9a4e30d6966e581`, 2번째 동일 스폰이
      `duplicate_worker_running`으로 억제되고 `worker_spawn_suppressed` 이벤트에 fingerprint 기록
- [ ] 동시 실행 상한을 넘는 요청은 queue 또는 suppression으로 처리하고 사유를 표시한다.
- [x] 생성이 억제된 워커를 오케스트레이터가 `배치했다`, `실행 중이다`라고 답하지 않는다.
      — 2026-08-27 통과: 같은 Task에서 최종 답변이 "suppress됐고 기존 워커 결과를 통합"으로 정확 보고
- [ ] 모델 queue가 밀려도 메시지·도구 결과·워커 종료 이벤트가 유실되거나 중복되지 않는다.

#### P1. 채팅 기본 동작

- [x] 새 대화를 만들고 첫 메시지를 Enter로 전송하면 사용자 메시지가 정확히 한 번만 표시된다.
      — 2026-08-23 `QA-CHAT-ENTER-20260823-1934`, 대화 본문 1회 표시 확인
- [x] Shift+Enter는 메시지를 전송하지 않고 줄바꿈만 수행한다.
      — 2026-08-23 사용자 직접 QA로 줄바꿈·Enter 전송 정상 확인
- [ ] 한글 IME 조합 중 Enter는 메시지를 잘못 전송하지 않는다.
- [ ] 응답 중 `실행 중`, 응답 후 `대화 가능` 상태로 전이하며 상태가 반복 진동하지 않는다.
- [x] 후속 질문이 같은 대화의 앞선 지시와 결과를 기억한다.
      — 2026-08-27 통과: Task `task_6fa23dcd9a5d4132ba893de62c6ab9a5`, 2턴째 도구 호출
      0회로 1턴 나열 결과(2번 항목)를 정확히 기억으로 답변
- [ ] 앱을 닫았다 다시 열어도 메시지의 순서와 개수가 그대로 복원된다.
- [ ] 새 대화·다른 프로젝트에 이전 대화의 컨텍스트나 워커 그래프가 섞이지 않는다.

#### P1. 도구 권한과 승인

- [x] 읽기 전용 verifier 워커에는 파일 수정 도구가 제공되지 않는다.
      — 2026-08-27 통과: 단일 워커 QA에서 verifier가 `read_file`만 수령
- [x] 파일 수정 요청은 승인 UI를 표시하고 Reject 시 워크스페이스가 변경되지 않는다.
      — 2026-08-27 통과: Task `task_e6da42993d9e41fb8408c472ebc15689`, 승인 4건 전부 거부 후
      `git status`/`git diff` 모두 clean. 후속 개선(같은 날): 서킷 브레이크가 턴을
      죽이는 대신 해당 도구만 회수하고 보고 지시를 주입 — 재검증
      Task `task_0a3dc0966f8f4a598228b541b6557c29`에서 finish_turn으로 "3회 연속
      거부되어 미완료, 승인 필요" 최종 보고 확인
- [x] Allow 시 승인한 변경만 워크스페이스에 적용된다.
      — 문구 재정의(2026-08-27): MIGRATION_24부터 Task는 프로젝트 체크아웃에서 직접
      작업한다(worktree 격리 폐기). Task `task_c62752e9bb3b4ce0a4cc381ca942e851`에서
      승인한 multiply 추가 + 테스트만 적용되고 `git diff --stat`과 일치, 검증
      run_bash(unittest) 통과 후 정직한 최종 보고 확인. QA 쓰기 시나리오는 종료 후
      fixture를 git checkout으로 복구한다
- [x] 프로젝트 디렉토리 밖 파일 접근은 차단되고 사용자에게 실패 이유가 표시된다.
      — 2026-08-27 통과: Task `task_03d79f5271724b989c9113e539fe5526`, `/etc/hosts`
      read_file이 "워크스페이스 밖 경로"로 거부되고 사유가 최종 답변에 표시. 셸 우회
      시도는 승인 게이트에 걸려 거부됨

#### P1. 취소·오류·재시작 복구

- [x] 실행 중 취소하면 활성 워커와 lease가 정리되고 다음 메시지를 정상 전송할 수 있다.
      — 2026-08-27 통과: Task `task_43fdec526e3c4f8abbc003dcc1bdbe45`, 취소 후 turn_end
      cancelled=true·세션 idle, 같은 세션 후속 질문 정상 답변, active_leases 0
- [x] 워커 하나가 실패해도 다른 워커 결과를 보존하고 오케스트레이터가 부분 실패를 보고한다.
      — 2026-08-27 통과: 두 워커 재검증(`task_59d359bcf7a44bda8142cc7e7a509496`)에서
      w2 completed_partial에도 w1 결과 보존·통합 답변에 워커별 상태 명시
- [x] 비정상 WebSocket 종료를 오류로 표시하고 중복 재연결·중복 세션을 만들지 않는다.
      — 2026-08-27 서버측 통과: Task `task_40a8d2d2e81b4a9983abd40120f70d11`, close
      핸드셰이크 없는 소켓 절단에도 턴이 정리되어 세션 idle, 세션 1개 유지, 재연결 시
      transcript 1회 복원 후 후속 턴 정상. 렌더러의 "오류 표시"는 수동 확인 항목
- [x] 유휴 상태에서 앱을 재시작하면 대화가 한 번만 복원되고 자동 연결도 한 번만 수행된다.
      — 2026-08-27 통과: 재시작 전후 세션 이벤트 85건·seq 동일(중복 0), 세션 1개 유지,
      재연결 후 후속 질문이 이전 답을 기억해 정답
- [x] 실행 중 앱을 재시작해도 Task를 재개 또는 명시적 실패 상태로 복구하며 워커를 중복 배치하지 않는다.
      — 2026-08-27 통과: Task `task_62ea4b20a7ef41e386dbc260179ca045`, 워커 running 중
      앱 강제 종료 → 재시작 후 세션 idle·dispatch needs_you로 명시 복구, 새 세션에서
      워커 자발 재배치 0건·정직한 상태 보고. 워커가 종료 전이라 성과 기록이 없는 것은
      terminal-시점 기록 설계대로

#### P1. 변경·검증·최종 판정

- [ ] 워커가 만든 파일 변경이 변경 패널과 실제 `git diff`에 동일하게 표시된다.
- [ ] 검증 명령의 exit code와 요약이 워커 주장과 분리되어 표시된다.
- [ ] 최종 답변의 성공·실패·변경 파일·검증 결과가 실제 상태와 일치한다.
- [ ] 각 결함은 재현 프롬프트, 기대 결과, 실제 결과, Task·Session ID와 함께 기록한다.
- [ ] 위 P0 항목을 모두 통과한 뒤에만 채팅·오케스트레이션 핵심 QA를 완료로 표시한다.

## v1.2. 서비스 배포 성숙도 — 정식 외부 배포 시 재개

성숙도 분석(2026-08-23) 결론: 자동 검증은 GA 후보 수준이나 실기기·배포 축은 알파 수준이다.
"혼자 쓰는 도구"에서 "남에게 주는 서비스"로 넘어가는 경계 작업만 남았다.

현재 제품 목표는 단일 사용자 로컬 ADE 완성이다. 아래 서명·공증·공개 artifact·자동 업데이트는
로컬 실행이나 unsigned 패키지 검증의 선행 조건이 아니므로 정식 외부 배포를 결정할 때 재개한다.

### 31. CI

- [x] push/PR에서 Python·Electron main·renderer·TypeScript·production build를 실행하는 CI workflow
- [x] 3개 버전 위치 일치 검증을 CI에서 실행 — VERSIONING.md는 이미 "CI tests this invariant"라고
      약속하지만 현재 로컬 `test_version.py`만 존재한다
- [x] `pnpm audit`·`pip-audit`를 CI 게이트로 승격

### 32. 서명과 공증

- [ ] **보류 — 정식 외부 배포 시** Developer ID 서명 + hardened runtime + notarization 파이프라인
- [ ] **보류 — 정식 외부 배포 시** 서명된 공개 artifact와 checksum 발행
      (VERSIONING.md 릴리스 게이트 6단계)

### 33. 검증된 업데이트 피드

- [x] 서명 검증·rollback·schema 호환·중단된 다운로드 복구의 자동 테스트 (VERSIONING.md 선행 조건)
- [ ] **보류 — 정식 외부 배포 시** backup-first 수동 앱 교체 절차를 대체하는 업데이트 피드

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
- [x] **R3 회귀 수정: Task 적합성 spawn gate와 실패 worker 결과 통합**
- [x] P3-15 Git-derived ChangeSet
- [x] P3-16 Independent Verification Runner
- [x] P3-17 revision-aware Review Loop
- [x] P3-18 Task branch commit/push/handoff
- [x] **v1.2-31 CI workflow와 버전 불변식 CI 검증**
- [ ] **현재 P0: 실제 Electron 앱 전체 화면 및 30-A 채팅·오케스트레이션 수동 QA**
- [ ] **보류: v1.2-32 서명·공증 파이프라인**
- [ ] **보류: v1.2-33 검증된 업데이트 피드**
