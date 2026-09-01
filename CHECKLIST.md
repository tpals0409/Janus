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
- 외부 API 모델·원격 실행은 v1.0 범위에 넣지 않는다. 구독형 CLI는 v1.0.21에서
  실행기로 편입됐다 — 워커·예산·승인은 적용되지 않고 에이전트 계약만 공유한다.

---

## v1.1. GitHub Skill Compiler

### 30. 공식 Janus 데스크톱 디자인 시스템

- [ ] 실제 Electron 앱 전체 화면 수동 QA

### 30-A. 채팅·오케스트레이션 실사용 QA

완료 근거는 채팅 기록만으로 판단하지 않는다. Task·Dispatch·AgentSession 상태, 실행 이벤트,
워커 그래프, worktree 변경, 검증 결과가 서로 일치해야 한다.

#### P0. 거짓 실행 방지와 Backpressure

- [ ] 동시 실행 상한을 넘는 요청은 queue 또는 suppression으로 처리하고 사유를 표시한다.
- [ ] 모델 queue가 밀려도 메시지·도구 결과·워커 종료 이벤트가 유실되거나 중복되지 않는다.

#### P1. 채팅 기본 동작

- [ ] 한글 IME 조합 중 Enter는 메시지를 잘못 전송하지 않는다.
- [ ] 응답 중 `실행 중`, 응답 후 `대화 가능` 상태로 전이하며 상태가 반복 진동하지 않는다.
- [ ] 앱을 닫았다 다시 열어도 메시지의 순서와 개수가 그대로 복원된다.
- [ ] 새 대화·다른 프로젝트에 이전 대화의 컨텍스트나 워커 그래프가 섞이지 않는다.

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

### 32. 서명과 공증

- [ ] **보류 — 정식 외부 배포 시** Developer ID 서명 + hardened runtime + notarization 파이프라인
- [ ] **보류 — 정식 외부 배포 시** 서명된 공개 artifact와 checksum 발행
      (VERSIONING.md 릴리스 게이트 6단계)

### 33. 검증된 업데이트 피드

- [ ] **보류 — 정식 외부 배포 시** backup-first 수동 앱 교체 절차를 대체하는 업데이트 피드

## v1.3. 에이전트 워크플로우 개선

2026-09-01 코드 감사 결과. 현재 구조(모델 주도 `create_worker` 위임)는 유지하되,
비어 있는 보증 3개 — 자기 신고제 완료 판정, 우회 가능한 안전 가드, 실패 컨텍스트 없는
재시도 — 를 메운다. 각 항목은 근거 위치를 병기한다.

### 34. 버그급 수정 (워크플로우가 실제로 깨지는 것)

- [x] 서킷 브레이커가 `schemas`에서만 도구를 제거하고 실행 게이트 `allowed_tool_names`는
      그대로 둔다 — 3연속 실패 도구가 재실행 가능 (`agent.py:736`). `discard(name)` 한 줄
- [x] 턴이 예외로 죽으면 `_quiesce_turn_workers`가 호출되지 않아 고아 워커가 계속 파일을
      쓴다 — `finally`로 이동 (`runtime.py:1668`). quiesce 전체 2초 예산도 `run_bash`
      120초와 불일치 (`runtime.py:1558`)
- [x] 스폰 실패 시 `active_workers`·fingerprint가 롤백되지 않아 동시성 슬롯이 영구 소실되고
      같은 스폰이 전부 `duplicate_worker_running`으로 거부된다 (`runtime.py:918-933`)
- [x] `send_worker`가 워커 예산 풀 리셋·`role_limit`/`total_limit`/`concurrent_limit` 미검사·
      write lease 미재획득으로 모든 게이트를 우회한다 (`runtime.py:1392-1425`)
- [x] read-only 턴 가드가 워커 스폰으로 우회된다 — 워커 도구가 필터 전 프로필 목록에서
      계산된다 (`runtime.py:816-817` vs `runtime.py:1618`)
- [x] 승인 대기(300초)·`wait_worker`(60초)가 cpu_tool lease(cap 2)를 쥔 채 블로킹해
      `finish_turn`까지 막히는 라이브락 — 블로킹 제어 도구를 lease 밖으로
      (`agent.py:651-664`, `runtime.py:1373`, `scheduler.py:38`)
- [x] adaptive 분류가 `word in text` substring 매칭 — "test"가 latest에, "fix"가 fixture에
      매칭된다. `intent.has_any`로 교체 (`adaptive.py:105,108,113,117,119`)
- [x] `finish_reason`을 아무 데서도 읽지 않아 max_tokens 절단이 "JSON 파싱 실패"로
      위장된다 (`agent.py:456-460`, `agent.py:592-595`)
- [x] CLI 러너가 stdout EOF 후에야 stderr를 읽어 64KB 초과 시 파이프 데드락 —
      취소도 SIGKILL 승격 없음, 턴 타임아웃 없음 (`cli_runner.py:336-373`, `cli_runner.py:281`)
- [x] 첫 스트림 청크 전에는 취소가 무효 — prefill에 매달리면 20분 대기
      (`runtime.py:321`, `agent.py:510-511`)
- [x] 워커 스레드가 쓰는 `changed_paths` set을 부모가 락 없이 순회 — 순회 중 변경
      RuntimeError 가능 (`runtime.py:1275` vs `runtime.py:1238`)
- [x] 승인 복귀가 무조건 `running`으로 되돌려 병렬 승인 대기·예산 소진 상태를 가린다
      (`runtime.py:1278-1284`)

### 35. 구조적 갭 (설계 수준)

- [ ] **완료 판정을 검증과 연결**: `finish_turn(outcome="completed")`가 Task의
      `acceptance_command`를 동기 실행하고 실패 시 `partial`로 강등한다
      (`runtime.py:716-725`, 현재 `verification.run`은 UI 라우터에서만 호출)
- [ ] 검증 게이밍 차단: passed 판정 전 verification 대상 파일의 revision 비교 —
      테스트를 지워 green을 만든 통과가 confidence 0.95 영구 규칙으로 승격되는 경로 차단
      (`verification.py:81-86`, `self_improvement.py:30-43`)
- [ ] adaptive `retry.{strategy,evidence}` 블록을 컨텍스트 preamble에 주입 — 현재 계산·
      영속만 되고 재시도 모델은 이전 실패를 모른다 (`adaptive.py:414-420`, `routers/sessions.py:41-57`)
- [ ] write lease 기본 파티션 `"*"` 독점과 부모 면제 해소 — 병렬 write 워커가 실제로는
      불가능하고, 부모는 워커 소유 파일을 자유롭게 편집한다 (`runtime.py:868-869`, `runtime.py:912-917`)
- [ ] 프로젝트당 Task 1개 직렬화(worktree 철회의 대가) — per-Task worktree 복원 또는
      `ownership.py` 파티션 기반 병행을 별도 결정으로 연다 (`domain.py:2075-2087`)
- [ ] self-improvement 오염 경로: `PREFERENCE_CUES`의 "먼저" 제거, 동일 증거 재스캔의
      confidence 인플레(+0.04/턴) 차단, `avoidance` kind 생산 경로 추가
      (`self_improvement.py:13-16`, `domain.py:2216-2219`)
- [ ] evaluation: `regression` 판정 comparison의 promote 거부, 계산된 stdev를 비교에 사용,
      comparability 키의 OS 빌드 번호를 coarse 키로 (`evaluation.py:19-24`, `routers/projects.py:117-123`)
- [ ] `worker_outcomes` 소비 플래그(`delivered_at`) 추가 — 재접속마다 "결과를 통합하라"
      다이제스트가 무한 재주입된다 (`routers/sessions.py:468`, `runtime.py:1517-1519`)
- [ ] 크래시 시 실행 중 워커의 `changed_paths` 소실·스폰 카운터 리셋 — 스폰 시점에
      `running` outcome row를 영속하고 재개 시 카운터를 시드한다 (`runtime.py:1183-1198`, `domain.py:2594`)
- [ ] verification 실패의 최신성 판정: (kind, command)별 최신 run만 비교 — 나중에 통과한
      재실행이 있어도 옛 실패가 재시도 토폴로지를 영구 고정한다 (`adaptive.py:134-141`)

### 36. 로컬 소형 모델 컨텍스트·캐시 효율

- [ ] 도구 스키마 chars를 컨텍스트 회계에 포함 — 24,000자 임계가 요청당 5,000자+의
      스키마를 못 본다 (`agent.py:111-113`, `agent.py:468-469`)
- [ ] 압축 요약을 system prompt 밖 고정 위치에 append-only로 — 현재 매 압축이 KV prefix
      캐시를 첫 토큰부터 무효화한다 (`agent.py:196-204`). `prompt_cache_probe` 신호 소비도
      함께 (`agent.py:440-448`)
- [ ] 스킬 카탈로그 주입 상한(top-N, 설명 절단)과 `activation.paths` 필터 연결 — 현재
      무제한 주입, paths는 컴파일만 되고 죽어 있다 (`runtime.py:493-505`, `skills.py:257`)
- [ ] `load_skill` 렌더 16,000자 vs 컨텍스트 24,000자 — 스킬 instructions를 안정 prefix로
      이동 (`runtime.py:629,641`, `agent.py:40`)
- [ ] 압축 시 tool result 240자 일괄 요약이 재읽기 루프 유발 — 도구별 차등 요약
      (`agent.py:141-144`)
- [ ] `read_file` 렌더에 줄 번호 부재 + 4,000자 중간 클립 — `edit_file` old_string 실패의
      직접 원인 (`tools.py:237`, `tools.py:61-66`)
- [ ] janus 롤만 8,000자 프롬프트 가드 면제 — 가장 큰 프롬프트가 유일하게 무상한
      (`runtime.py:136-139`)
- [ ] 마지막 블록 하나가 임계를 넘으면 압축이 무력 — 블록 내 head/tail 절단 폴백
      (`agent.py:220`)
- [ ] 페르소나가 인라인된 번들 파일 경로를 인용해 소형 모델의 존재하지 않는 경로
      read_file을 유발 (`personas/*.md:15`, `runtime.py:130-132`)

### 37. 처리량·관측

- [ ] 토큰 델타마다 SQLite IMMEDIATE 트랜잭션 + fsync — 전 워커 토큰이 단일 writer에
      직렬화된다. 델타 비영속화 또는 배칭 + WAL/`synchronous=NORMAL`
      (`agent.py:343,346`, `domain.py:2664`, `domain.py:749-757`)
- [ ] `telemetry.events`·`node_events`·`worker_records`·`session_events` 무상한 성장과
      스팬별 전체 복사 O(n²) — 상한/프루닝 (`telemetry.py:66`, `runtime.py:671,706,1085`)
- [ ] 압축 발동 스텝의 실제 프롬프트가 기록되지 않아 사후 재구성 불가 (`agent.py:451`)
- [ ] MCP 브리지 경로(CLI 세션 write/bash)에 이벤트·스팬·토큰 회계 부재
      (`mcp_bridge.py:98-103`, `routers/mcp.py:32`)
- [ ] event bus: `"operations"` 이중 발행으로 큐 깊이 반감, overflow 시 gap 신호 없는
      drop-oldest, 죽은 구독자 미제거 (`shared.py:93-101`, `event_bus.py:56-70`)
- [ ] 모델 스트림 예외 시 부분 결과 폐기·재시도 없음 — `recovery.classify_failure`의
      retryable 분류가 agent 루프에서 미사용 (`agent.py:509-517`, `recovery.py:22-50`)

### 38. 죽은 코드 정리

- [ ] `effective_worker_role` + `role_adaptation` 분기 ~40줄 — 호출처가 테스트뿐
      (`runtime.py:262-285`, `runtime.py:808,831-843,1017-1020,1036-1043`)
- [ ] `worker_role_sequence` — 계산·영속·미소비. 스폰 순서로 강제하거나 삭제
      (`adaptive.py:410`, `runtime.py:370`)
- [ ] `create_dispatch` — `create_execution`에 대체된 무호출 함수 (`domain.py:1994-2031`)
- [ ] skills IR 장식 메타데이터: `trust_state` 미강제, `capabilities.approval_required`·
      `execution.context` 미소비 — 강제하거나 삭제 (`domain.py:466`, `skills.py:255-265`)
- [ ] `mock_order_lookup`/`search_docs` 데모 도구의 프로덕션 레지스트리 상주
      (`tools.py:176-207,317-320`)
- [ ] evaluation stdev·worker/memory mean 5종 — 계산 후 비교·CSV에서 폐기
      (`evaluation.py:107-118,143-158,237-241`)

## 지금 시작할 작업

- [ ] **현재 P0: 실제 Electron 앱 전체 화면 및 30-A 채팅·오케스트레이션 수동 QA**
- [ ] **보류: v1.2-32 서명·공증 파이프라인**
- [ ] **보류: v1.2-33 검증된 업데이트 피드**
