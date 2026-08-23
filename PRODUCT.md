# Janus 제품 정의

> Local Agent Development Environment for turning limited local compute into verified software work.

UI의 공식 시각·컴포넌트 규칙은 [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md)를 따른다.

Janus는 개발자가 로컬 하드웨어의 모델 에이전트에게 여러 소프트웨어 작업을 위임하고,
한정된 GPU·통합 메모리·CPU를 효율적으로 배분하며, 격리된 변경을 검토한 뒤 안전하게
출하하는 데스크톱 ADE다. 외부 모델과 외부 코딩 에이전트 지원은 현재 제품 목표가 아니다.

Janus의 제품 단위는 에이전트 설정이나 모델 호출이 아니라 **검증 가능한 작업 결과**다.
에이전트의 답변이 끝났다고 작업이 끝나는 것이 아니다. 변경이 격리되어 있고, 테스트와 diff가
보이며, 사람이 검토하고, 선택한 결과가 브랜치나 PR로 전달되어야 완료다.

---

## 1. 해결할 문제

코딩 에이전트가 강해질수록 병목은 코드 생성에서 운영으로 이동한다.

- 여러 에이전트가 같은 체크아웃을 수정해 변경이 충돌한다.
- 어느 작업이 실행 중이고, 입력을 기다리며, 실패했는지 한눈에 알기 어렵다.
- 대화 답변과 실제 파일 변경·테스트 결과가 분리되어 있다.
- 결과를 비교하고 검토해 브랜치·커밋·PR로 전달하는 과정이 수동이다.
- 로컬 모델은 메모리와 생성 슬롯이 제한되어 무작정 병렬화하면 오히려 처리량이 떨어진다.
- worker를 언제 만들고 어떤 컨텍스트·도구·토큰 예산을 줄지 측정하고 개선하기 어렵다.
- 모델 생성, 도구 I/O, 테스트 실행의 대기 시간이 구분되지 않아 병목을 찾기 어렵다.
- 높은 자율성을 주면 생산성은 오르지만 파일·셸·자격증명 접근 위험도 함께 커진다.

기존 IDE는 사람이 파일을 편집하는 흐름에 최적화되어 있고, 코딩 에이전트 CLI는 한 세션의
실행에 최적화되어 있다. Janus는 그 위에서 **작업 격리, 에이전트 감독, 결과 검토, 출하**를
하나의 흐름으로 만든다.

## 2. 대상 사용자와 핵심 작업

### 1차 사용자

- 로컬 코딩 에이전트를 매일 사용하는 개인 개발자
- 동시에 2개 이상의 기능·버그·조사를 진행하는 개발자
- 코드와 프롬프트를 외부 플랫폼에 맡기지 않고 로컬 실행을 선호하는 사용자
- 에이전트 결과를 그대로 수용하지 않고 diff와 테스트를 검토하는 사용자

### 핵심 Job-to-be-Done

> 저장소에서 해결할 작업을 등록하면 Janus가 안전한 작업 공간을 만들고 적합한 에이전트를
> 실행한다. Janus가 제한된 로컬 자원과 컨텍스트를 효율적으로 배분하는 동안 나는 필요한
> 질문만 처리하고, 완료된 변경을 검토한 뒤 결과를 커밋하거나 출하하고 싶다.

### 1차 사용 시나리오

1. 프로젝트를 등록한다.
2. 버그나 기능을 Task로 만든다.
3. 기준 ref, 로컬 AgentProfile, 시간·토큰 예산을 선택하면 Janus가 worktree를 만든다.
4. 자원 스케줄러가 모델 생성과 도구 실행을 배치하고 에이전트가 조사·수정·테스트한다.
5. Janus가 진행 상태, 질문, 로그, 세션 trace를 보여준다.
6. 사용자가 diff와 테스트 결과를 검토하고 피드백을 보낸다.
7. 결과를 커밋·push·PR 또는 로컬 적용으로 출하한다.
8. 작업 공간을 보관하거나 안전하게 정리한다.

## 3. 제품 포지셔닝

### 한 문장

**Orca의 worktree 중심 감독·리뷰 흐름에 로컬 모델 전용 자원 스케줄링, 동적 worker 위임,
도구 승인, 평가·trace를 결합한 ADE.**

### 차별점

1. **Local-only first**
   - 프로젝트 메타데이터, 세션 기록, trace, 승인 결정은 기본적으로 로컬에 저장한다.
   - Qwen/MLX 같은 로컬 모델이 기본이자 현재 유일한 실행 경로다.
   - 외부 모델 지원 가능성을 위해 현재 구조를 복잡하게 만들지 않는다.

2. **Resource-efficient**
   - 모델 생성 슬롯, 메모리, CPU 작업, 도구 I/O를 서로 다른 자원으로 취급한다.
   - 단일 모델 서버를 따뜻하게 유지하고 worker fan-out에 backpressure와 예산을 적용한다.
   - 빠른 답변보다 고정 하드웨어에서 검증된 작업 처리량을 최적화한다.

3. **Delegation-aware**
   - worker 생성이 실제로 품질·시간을 개선했는지 Task 결과와 비용으로 측정한다.
   - worker에는 전체 대화가 아니라 하위 작업에 필요한 최소 컨텍스트와 도구만 전달한다.

4. **Review-first**
   - 성공의 기준은 답변 생성이 아니라 검토 가능한 ChangeSet과 검증 결과다.
   - diff, 테스트, 리뷰 코멘트, 출하 상태가 대화보다 상위에 있다.

5. **Capability-safe and observable**
   - worktree를 보안 sandbox로 오해하지 않는다.
   - 파일 jail, 위험 도구 승인, AgentProfile별 capability를 중첩한다.
   - Task 수준 상태와 모델 내부 trace를 모두 제공하되 서로 혼동하지 않는다.
   - 모델 대기, 생성, 도구 I/O, 검증 시간을 분리해 실제 병목을 보여준다.

## 4. 제품 원칙

### P1. Task가 최상위 작업 단위다

대화, 터미널, 모델 실행, trace는 Task를 수행하기 위한 수단이다. 모든 세션과 변경은 어떤
Task에 속하는지 설명할 수 있어야 한다.

### P2. 에이전트 실행은 기본적으로 격리한다

서로 다른 Task는 같은 쓰기 가능한 checkout을 공유하지 않는다. Git 프로젝트에서는 Task별
worktree와 branch를 기본값으로 사용한다.

### P3. Git이 코드 변경의 진실 원천이다

Janus DB가 별도 파일 스냅샷을 진실 원천으로 복제하지 않는다. ChangeSet은 base ref 대비 Git
diff에서 파생한다. Janus는 Task, Session, 승인, 리뷰 같은 제품 메타데이터만 소유한다.

### P4. 완료와 성공을 분리한다

- 에이전트 완료: 프로세스 또는 모델 턴이 끝남
- 작업 준비 완료: diff와 검증 결과가 리뷰 가능함
- 작업 완료: 사용자가 결과를 수락하거나 명시적으로 종료함
- 출하 완료: commit/push/PR/적용 중 선택한 전달 방식이 성공함

### P5. 사용자 주의가 필요한 상태를 숨기지 않는다

`working`, `needs-input`, `blocked`, `review`, `failed`를 구분한다. 인증 실패, 모델 부재,
프로세스 종료, 충돌을 포괄적인 “시작 중” 상태로 덮지 않는다.

### P6. 삭제는 변경 손실을 뜻하지 않는다

미병합 커밋이나 dirty diff가 있는 worktree를 조용히 삭제하지 않는다. 삭제 전에 보존 상태를
계산하고, branch 보존·archive·강제 삭제를 명확히 구분한다.

### P7. 세부 trace는 진단 도구다

Canvas와 span은 모델 동작을 이해하는 데 유용하지만 ADE의 홈 화면이 아니다. 기본 화면은
프로젝트와 Task 상태이며 trace는 Session 상세 화면에서 연다.

### P8. 효율은 고정된 조건에서 측정한다

“더 많은 worker”나 “더 많은 토큰”을 성능으로 간주하지 않는다. 같은 하드웨어, 같은 모델,
같은 Task suite에서 wall time, token, 사용자 개입, 검증 통과율을 함께 비교한다.

## 5. 핵심 도메인 모델

```text
Project
 ├─ ModelProfile
 ├─ AgentProfile
 └─ Task
     ├─ Workspace
     ├─ Dispatch ── ResourceLease ── AgentSession
     │                └─ RuntimeWorker / Trace
     ├─ ChangeSet (derived from Git)
     ├─ Verification
     └─ Review ── ShipResult
```

### Project

등록된 저장소와 기본 정책.

- `id`, `name`, `repo_path`, `default_ref`
- 기본 agent profile과 권한 정책
- worktree 보관 위치와 setup 명령

### Task

사용자가 완료하려는 독립된 작업.

- `id`, `project_id`, `title`, `objective`, `acceptance`
- `status`: `todo | preparing | working | needs-input | blocked | review | completed | failed | archived`
- `priority`, `created_at`, `updated_at`

### Workspace

Task의 파일·프로세스 실행 경계.

- MVP에서는 Git worktree 하나와 branch 하나
- `base_ref`, `branch`, `path`, `lifecycle_status`
- 현재 범위는 로컬 worktree이며, 강한 격리가 필요하면 로컬 컨테이너를 별도 검토

### AgentProfile

재사용 가능한 에이전트 실행 설정.

- model profile, system instructions, tools, permission profile, token/time/worker budget
- 현재 YAML 오케스트레이터 설정은 `Janus Local` profile로 이동

### ModelProfile

로컬 모델 실행 특성과 자원 한계.

- backend, local path, quantization, context limit
- 모델 생성 동시성, 메모리 예상량, prompt/session cache 정책
- 초기에는 현재 Qwen3.8-27B MLX 한 구성만 지원한다.

### AgentSession

로컬 runtime과 이어지는 하나의 지속 대화.

- 입력·출력·상태·resume 정보
- `working | needs-input | idle | completed | failed | stopped`
- 세션은 Task가 아니며 한 Task에 여러 세션이 있을 수 있다.

### Dispatch

Task를 특정 Workspace와 AgentSession에 맡긴 한 번의 시도.

- 재시도와 A/B 비교를 구분하는 attempt 단위
- 오래된 세션이 새 시도를 완료 처리하지 못하도록 고유 ID를 사용

### ResourceLease

Dispatch가 로컬 자원을 사용하는 명시적 권한.

- model generation slot, CPU/tool slot, memory budget
- queue 진입·획득·해제 시각과 대기 원인
- 취소·실패·앱 종료 시 반드시 반환

### RuntimeWorker

한 AgentSession 내부에서 오케스트레이터가 만든 하위 실행자. 현재 Janus의 `create_worker`가
여기에 해당한다. RuntimeWorker는 별도 worktree나 Task를 소유하지 않으며 상위 Dispatch의
권한과 workspace를 공유한다.

### ChangeSet

Workspace의 `base_ref...HEAD + working tree`에서 파생한 변경 결과.

- 변경 파일, diff, commit, 테스트 결과
- 별도 복제본을 저장하지 않고 필요할 때 Git에서 다시 계산

### Review와 ShipResult

- Review: 라인 코멘트, 수정 요청, 승인·거부 결정
- ShipResult: commit, push, PR, local apply, archive 결과

## 6. 상태와 생명주기

```text
todo
  → preparing       worktree/setup 생성
  → working         dispatch 실행
  → needs-input     승인·질문 대기
  → review          에이전트 종료 + ChangeSet 준비
  → completed       사람이 결과 수락
  → archived        실행 환경 정리

어느 단계에서든 blocked / failed가 될 수 있으며 복구 동작을 함께 제시한다.
```

Task 상태는 단순히 마지막 이벤트로 추측하지 않는다. Workspace, Dispatch, Session, Review 상태를
입력으로 한 명시적 전이 규칙으로 계산한다.

## 7. 주요 제품 화면

### Task Board / Sidebar

- 프로젝트별 Task와 상태
- `Needs You`, `Working`, `Review`, `Failed`
- agent, branch, 마지막 활동, 변경량

### Task Workspace

- Task 목표와 acceptance criteria
- 대화 또는 agent terminal
- 변경 파일·diff·검증 결과
- editor와 file tree
- commit/push/archive 동작

### Session Detail

- 메시지, tool calls, 승인, 로그
- Janus Local의 orchestrator/worker trace Canvas
- token, latency, cancellation

### Review

- base ref 대비 diff
- 라인 코멘트와 일괄 수정 요청
- 테스트·lint 결과
- 수락, 재작업, 폐기, 출하

## 8. Local Runtime과 자원 스케줄링 계약

ADE 코어는 현재 MLX 구현의 세부사항 대신 Local Runtime 계약을 사용한다.

- `start(workspace, profile, task) -> session`
- `send(session, message)`
- `cancel(session)` / `stop(session)`
- `resume(session)`
- 상태 이벤트: working, needs-input, idle, completed, failed
- 메시지·도구·승인 이벤트 스트림
- usage, queue wait, generation, tool I/O, verification timing

Resource Scheduler는 모델 호출을 기본 1-slot로 직렬화하고 CPU·도구 I/O는 안전한 범위에서
병렬화한다. 측정 없이 동시 모델 생성을 늘리지 않으며, worker와 Task마다 token/time budget,
queue priority, cancellation을 적용한다. 다른 로컬 backend는 실제 필요가 생길 때 이 계약으로
추가하며 외부 모델 호환성은 현재 검증 조건이 아니다.

## 9. 안전 모델

### 스킬 공급망

Janus는 GitHub의 Codex·Claude Code `SKILL.md`를 로컬 에이전트에 바로 실행하는
플러그인으로 취급하지 않는다. `preview -> compile -> install -> activate -> load`의
다섯 단계를 거친다.

- GitHub ref를 정확한 commit SHA로 고정하고 라이선스·요구 capability·변환 경고를 먼저 보여준다.
- 외부 스킬을 Janus IR로 결정적으로 컴파일하며, shell·network·MCP는 묵시적으로 실행하지 않는다.
- 설치와 AgentProfile 활성화를 분리하고, 호환되지 않는 capability는 활성화를 거부한다.
- 세션 시작 시 스킬 버전을 snapshot하고 목록만 prompt에 넣은 뒤, 필요할 때 `load_skill`로 본문을 지연 로드한다.
- 설치된 원본은 불변 version으로 남겨 재현성과 감사 가능성을 보장한다.

### 에이전트 작성과 컨텍스트

- `프롬프트`는 선택한 AgentProfile의 실제 system prompt를 편집하며 새 Task 시도부터 적용한다.
- `컨텍스트 정책`은 목표·수용 검증·workspace 경로의 포함 여부와 대화 압축 한도를 프로필별로 관리한다.
- Task를 시작할 때 AgentProfile과 활성 SkillVersion을 Dispatch/Session에 snapshot하여 이후 프로필 수정이 기존 실행을 바꾸지 않게 한다.
- 실행 화면의 `컨텍스트 검사기`는 주입된 소스, 포함·제외 이유, 정적 token 추정치와 최신 context-window 사용량을 보여준다.
- `그래프`는 작성 도구가 아니라 실행 뷰어다. AgentProfile을 고정 루트로 두고 오케스트레이터가 Task 실행 중 생성·종료한 worker span만 표시한다.
- 별도의 전역 컨텍스트 편집기는 두지 않는다. 정책은 AgentProfile에, 실제 조립 결과는 Task 실행에 귀속한다.

### 경계

- Worktree: Task 간 Git 변경 충돌 방지
- Workspace jail: Janus Local 도구의 파일 접근 제한
- Tool approval: 셸·쓰기 같은 위험 기능의 사용자 승인
- AgentProfile capability: 로컬 agent가 사용할 수 있는 도구와 예산
- Process isolation: 선택적 로컬 컨테이너; worktree와 별개의 보안 계층

### 불변 조건

- Task A의 기본 파일 도구는 Task B workspace를 수정하지 못한다.
- 승인 대기와 응답은 session/dispatch ID로 귀속된다.
- 앱 종료 시 자신이 시작한 프로세스만 정리한다.
- dirty 또는 미병합 변경이 있는 workspace는 확인 없이 삭제하지 않는다.
- ResourceLease는 예외·취소·종료 경로에서도 누수되지 않는다.

## 10. 성공 기준

### North Star

**고정된 로컬 하드웨어에서 시간당 검증·수락된 Task 수**

단순 모델 호출 수, worker 수, 생성 토큰 수는 핵심 성공 지표가 아니다. 같은 Task suite와
acceptance 기준에서 처리량과 품질이 함께 올라야 개선이다.

### 보조 지표

- Task 생성부터 첫 reviewable diff까지 걸린 시간
- `needs-input` 상태에서 사용자가 응답하기까지 걸린 시간
- 결과 수락률과 재작업 횟수
- 수락된 Task당 prompt/completion token과 모델 생성 시간
- model queue wait, generation, tool I/O, verification 시간 비율
- worker 사용 대비 품질·wall-time 개선량
- peak memory와 orphan model/process 수
- Task당 사용자 개입 횟수와 attention time
- 병렬 Task 간 파일 충돌·교차 오염 건수
- 실패 원인이 명확한 상태와 복구 동작으로 표시된 비율
- 삭제·종료 후 남은 orphan workspace/process 수

## 11. 비목표

초기 Janus는 다음을 목표로 하지 않는다.

- 범용 프로젝트 관리 또는 Jira 대체
- 클라우드 모델·호스팅 판매
- 외부 API 모델 또는 구독형 CLI 통합 — 필요성이 검증되기 전까지 보류
- 자체 Git 서버나 코드 호스팅
- 무제한 재귀형 자율 에이전트 조직
- IDE 전체 언어 서버 기능의 재구현
- worktree를 OS 보안 sandbox라고 주장하는 것
- 모델의 사고 과정 원문 저장·노출

## 12. 현재 구현의 위치

Janus v1.0의 Task·worktree·scheduler·review·평가·배포 기반은 완료됐다. v1.1은
로컬 모델의 문맥 효율을 높이기 위해 Skill Library, 외부 `SKILL.md` 컴파일,
AgentProfile 활성화, 세션별 지연 로딩을 추가했다. 외부 모델·구독형 CLI agent는
여전히 제품 가정이 아니며, 로컬 TaskSuite가 필요를 입증할 때만 다시 판단한다.
