# Janus 오케스트레이션 체크리스트 (역사 기록)

> **이 문서가 설명하는 엔진은 2026-08-25 `0d53440`에서 제거됐다.**
> `workflow.py`, `workflow_template.py`, `pipeline.py`, `airgap.py`,
> `model_router.py`, `orchestration_bundle.py`와 그 테스트가 모두 삭제됐다.
> 아래의 체크 표시는 **당시 실제로 통과했던 기록**이지 현재 보증이 아니다.
> 특히 §7의 폐쇄망 감사 게이트는 강제 모듈과 감사 테스트가 함께 사라져
> **지금은 어떤 네트워크 격리도 보증하지 않는다.**
>
> 남아 있는 것은 §2의 파일 소유권 락(`ownership.py`)뿐이고, 그것도 write worker
> 스폰 시점에만 검사한다. `config/workflows/standard.yaml`과 `config/models.yaml`은
> 이제 어떤 코드도 읽지 않는 고아 파일이다.
>
> 현재 실행 모델은 [PRODUCT.md](PRODUCT.md) §5와 [README.md](README.md)를 본다.
> 설계 근거로서의 가치 때문에 남긴다.


목표: **읽기는 병렬, 쓰기는 단일 스레드, 계획은 코드가 소유하는
결정론적 오케스트레이션을 폐쇄망 로컬 모델 위에서 완성**

- 마스터 체크리스트: [CHECKLIST.md](CHECKLIST.md)
- 근거 조사: Anthropic multi-agent research system, Cognition
  "Don't Build Multi-Agents" / "What's Actually Working" (2026-08 조사)

## 설계 원칙 (모든 항목의 판정 기준)

- 상태는 코디네이터 단일 루프에만 있다. 워커는 전부 stateless.
- 제어 흐름(fan-out·재시도·순서)은 엔진 코드가, 내용은 모델이 소유한다.
- 논리적 병렬도와 물리적 병렬도(GPU 슬롯)를 분리한다.
- 에이전트 간 직접 메시징 금지. 통신은 선언된 산출물 파일 → 엔진 → 다음 스테이지로만.
- 같은 파일에 대한 병렬 쓰기는 어떤 경로로도 불가능해야 한다.

## 진행 규칙

- 위에서 아래 순서로 진행한다. 1~5가 최소 동작 세트다.
- 각 항목은 테스트·실모델 검증 후 완료 처리한다.
- 외부 호출이 하나라도 생기면 해당 항목은 미완료로 되돌린다.

---

## 1. 워크플로우 엔진 (결정론적 코어)

- [x] 스테이지 상태머신: needs 기반 DAG, 스테이지 경계마다 체크포인트 저장
  - [x] 결정론적 DAG 실행·원자적 JSON 체크포인트·실패 경계 테스트
  - [x] 실제 로컬 모델 파이프라인에 연결하여 스테이지 경계 검증
- [x] 크래시 후 마지막 체크포인트부터 재개 (처음부터 재실행 금지)
  - [x] 재개 시 체크포인트와 현재 DAG 정의 불일치 거부
- [x] 워커별 타임아웃·툴콜 상한·재시도 횟수를 엔진이 강제
  - [x] 협력적 예산 검사만으로는 멈춘 워커를 종료할 수 없음 — 별도 프로세스 격리·기한 초과 종료 필수
  - [x] 모든 워커 툴콜은 엔진 브로커를 통해 계수되어야 함
- [x] 스폰 상한: 템플릿 선언값과 무관하게 엔진 레벨 절대 상한 존재
  - [x] 크래시 재개 후에도 이전 스폰 횟수를 누적하여 상한 우회 차단
- [x] 실패 분기: 재시도 소진 시 폴백 스테이지 또는 사람 개입으로 명시적 이관
- [x] 한 파이프라인의 전체 실행이 트레이스로 재구성되는 테스트

## 2. 실행 격리 (쓰기 단일 스레드)

- [x] worktree 자동 프로비저닝·정리 (쓰기 워커 1개당 1개)
  - [x] macOS Unicode 정규화가 다른 경로도 동일한 등록 worktree로 판정
  - [x] 생성 후 체크포인트 전 크래시로 남은 미기록 worktree 자동 회수
  - [x] commit·archive 후 완료 체크포인트 전 크래시에서 성공 branch 보존·복구
- [x] 파일 소유권 락 테이블: 파티션 밖 파일 쓰기를 엔진 레벨에서 차단
- [x] 동일 파일 병렬 쓰기가 불가능함을 증명하는 테스트 (경합 시나리오 포함)
- [x] worktree 순차 머지 + 머지 후 통합 검증 스테이지
- [x] 머지 충돌 시 전담 fixer 워커 1개 (단일 스레드), 한도 초과 시 사람 개입
  - [x] 재개 시 충돌 integration worktree를 미기록 고아로 오인하여 정리하지 않음
  - [x] fixer가 충돌 파일 밖을 수정하면 fingerprint 비교로 거부

## 3. 슬롯 스케줄러 (GPU 슬롯 경제)

- [x] 동시 추론 슬롯 세마포어: 계획상 워커 수 > 슬롯 수면 큐잉
- [x] 역할별 모델 라우팅: coder / reviewer / summarizer → models.yaml 매핑
- [x] 슬롯 대기 시간이 계측 스키마(queue_enter/lease_acquired)에 기록됨
- [x] VRAM 기반 정밀 슬롯 계산 — 세마포어가 실측으로 병목일 때만 착수
  - [x] 실모델 계측 9건, p95 0.185ms로 최소 10건·1,000ms 게이트 미달 — 현재 상태 deferred
  - [x] Explore 3-way fan-out 실모델 계측 11건, p95 2,960.708ms로 게이트 전환 — 현재 상태 recommended

## 4. 표준 파이프라인 템플릿 (explore → plan → implement → review → verify)

- [x] Explore: read 워커 2~3개 fan-out, 격리 컨텍스트, 요약 파일만 반환
  - [x] fan-out 상한 3 및 read 전용·summarizer 역할을 스키마 검증으로 강제
  - [x] 워커별 별도 프로세스·모델 슬롯 대기와 고유 인덱스를 단위 테스트 및 실제 27B E2E로 검증
  - [x] 반환값을 단일 `summary` 필드·8,000자 이하로 제한하고 체크포인트에는 요약 파일 참조만 저장
- [x] Plan: 태스크 스펙(목적·출력 형식·허용 툴·경계) + 파일 소유권 파티션 산출
  - [x] 정확한 필드 집합·태스크 ID·허용 툴·비어 있지 않은 경계를 검증하고 추가 대화 컨텍스트를 거부
  - [x] 안전한 상대 경로로 정규화하고 태스크 간 중첩 소유권을 Plan 단계에서 거부
  - [x] 검증된 Plan으로 write/worktree Implement 스테이지를 결정론적으로 생성하고 스펙 파일을 원자 저장
  - [x] 구조화 JSON 절단 문제를 Plan 512-token 예산과 JSON object 모드로 해소
  - [x] 완료된 Explore 요약을 구현 태스크로 재계획하는 과잉 fan-out을 단일 구현 태스크 제약으로 해소
  - [x] 각 구현 태스크가 성공해야 하는 워커별 `check` 명령을 Plan 계약에 포함
  - [x] 실제 27B Plan이 `workflow-e2e.txt` 단일 소유권 스펙을 산출하는 E2E 통과
- [x] Implement: 파티션당 write 워커 1개, worktree 격리, 워커별 테스트가 완료 조건
  - [x] Plan 태스크별 소유권·`check`를 write/worktree Stage로 결정론적으로 변환
  - [x] 소유권 검증 뒤 워커 worktree에서 `check`를 실행하고 성공한 경우에만 커밋·통합 허용
  - [x] 워커 테스트 실패 시 미커밋 worktree·브랜치를 정리하고 재시도/실패 분기로 이관
  - [x] 실제 27B write 워커의 개별 테스트와 후속 통합 테스트가 각각 1회 성공하는 E2E 통과
- [x] Review: 클린 컨텍스트 리뷰어 — diff와 스펙만 제공, 코더 대화 기록 차단
  - [x] 리뷰 패킷 필드를 검증된 Plan과 diff로 고정하고 추가 코더 대화·체크포인트 필드 거부
  - [x] 실제 통합 브랜치 diff를 별도 reviewer 프로세스와 reviewer 모델 라우트로 검토하는 27B E2E 통과
- [x] Review 루프: 구조화된 피드백 파일로 회송, 최대 2회, 초과 시 사람 개입
  - [x] verdict/findings 및 finding ID·경로·라인·심각도 계약을 검증하고 원자적 피드백 파일 저장
  - [x] 엔진 소유 고정 한도 2회: 1회차 revise, 2회차 needs_human, 이후 호출 거부
  - [x] 실제 27B reviewer 두 프로세스의 수정요청이 두 번째에서 needs_human으로 멈추는 E2E 통과
- [x] Verify: 머지 → 통합 테스트 1회 → 완료
  - [x] 보존된 write 브랜치를 순차 머지하고 integration worktree에서 검증 명령을 정확히 1회 실행
  - [x] 실제 모델 write 결과가 워커 테스트 후 머지·통합 테스트를 통과하는 E2E 검증
- [x] 파이프라인 전 구간을 실제 로컬 모델로 완주하는 E2E 테스트
  - [x] 실제 Explore 요약 → 구조화 Plan → Plan 산출 write Stage → 워커 테스트 → 머지·통합 검증 → 클린 Review 승인 완주
  - [x] 크래시 재개, 모델 폴백, 충돌 fixer, 리뷰 2회 초과 사람 개입 경로를 같은 로컬 모델 검증기에 포함
  - [x] 초기 Explore·Plan 전용 README의 문서 드리프트를 전체 검증 범위와 에어갭 감사 설명으로 갱신

## 5. 워크플로우 템플릿 포맷

- [x] YAML 선언형 스키마 확정 (튜링 완전 DSL·스크립트 금지 — 감사 가능성)
  - [x] `yaml.safe_load`와 정확한 top-level/stage 필드 집합으로 임의 실행 객체·확장 키 거부
  - [x] 5단계 표준 템플릿을 `config/workflows/standard.yaml`에 저장하고 컴파일 테스트
- [x] 동적 값은 `${...}` 바인딩 하나로만: 이전 스테이지 산출물·inputs 참조
  - [x] `${inputs.name}`와 의존 선행 단계의 `${stages.id.output}` 외 문법·미선언·비의존 참조 거부
- [x] 스테이지 필드: id / role / needs / fanout(max 필수) / write(none|worktree) / context / task / output / check / on_fail
- [x] output 계약: 파일 경로 + 스키마, 엔진이 검증하고 불일치 시 재시도
  - [x] JSON object·required·properties·additionalProperties=false 계약과 안전한 로컬 파일 경로 강제
  - [x] 단일 출력 불일치가 재시도 예산을 소비하고 성공 출력만 원자 저장되는 테스트
  - [x] fan-out 요약별 계약 검증의 체크포인트 속성명 결함을 전용 회귀 테스트로 해소
  - [x] 저장된 표준 YAML Explore 계약을 실제 27B fan-out E2E에 적용
- [x] 템플릿에 엔진 소관 사항(체크포인트·슬롯·모델명) 등장 금지 — 스키마 검증으로 강제
- [x] 템플릿 로드 시 정적 검증: 순환 needs, 미선언 산출물 참조, fanout 상한 누락 거부

## 6. 컨텍스트 엔지니어링

- [x] 서브에이전트 요약-반환 규약: 탐색 본문은 워커 컨텍스트와 함께 폐기
- [x] 컨텍스트 예산 계측: 워커별 토큰 사용량 기록, 임계 도달 시 압축·체크포인트
  - [x] 모델 출력과 분리된 IPC로 input/output/total tokens를 stage·fanout index별 기록
  - [x] 8,192-token 임계와 압축 여부를 스테이지 경계 체크포인트에 저장하고 크래시 재개 시 복원
  - [x] 임계 초과 표시만으로 완료되던 검증 공백을 해소 — 8,000자 이하 압축 컨텍스트 IPC가 없으면 워커 실패
  - [x] 8,500-token 합성 워커의 압축 표시 테스트와 실제 27B 전 워커 usage 기록 E2E 통과
- [x] 리뷰어 컨텍스트 오염 방지 테스트: 코더 대화 기록이 리뷰어에 유입되지 않음을 검증

## 7. 폐쇄망 게이트 (전 항목 공통 완료 조건)

- [x] 오케스트레이션 전 구간 아웃바운드 네트워크 호출 제로 — 자동 감사 테스트
  - [x] 모든 격리 워커와 실모델 검증기 부모 프로세스에서 loopback·Unix socket 외 DNS/TCP 연결 차단
  - [x] 비연결 UDP `sendto`·주소 지정 `sendmsg` 우회도 동일한 목적지 정책으로 차단
  - [x] 외부 DNS·IP 차단 회귀 테스트와 실제 27B E2E의 local 연결 8건·blocked 0건 감사 통과
- [x] 모든 산출물·트레이스·체크포인트가 로컬 저장소에만 기록됨
  - [x] 영속 상태 전체를 순회해 HTTP(S)·FTP·S3·GS 원격 URI를 거부하는 완료 전 감사 게이트 적용
  - [x] 실모델 원본은 git 제외된 로컬 `artifacts/orchestration/runs/`에만 원자 저장
- [x] 에어갭 번들에 오케스트레이션 컴포넌트 포함 (엔진·템플릿·스키마)
  - [x] 런타임·격리·검증·에어갭·YAML 로더·표준 템플릿·모델 역할 설정을 고정 목록으로 번들
  - [x] 파일별 SHA-256 manifest와 byte-for-byte 재현성 테스트
  - [x] 로컬 `artifacts/orchestration-airgap.zip` 생성 및 bundle SHA-256 검증

---

## 완료 조건

- 표준 파이프라인이 실제 로컬 모델로 기능 개발 1건을 사람 개입 없이 완주한다.
- 완주 실패 시(리뷰 한도 초과·머지 실패) 사람 개입 지점에서 정확히 멈춘다.
- 크래시 후 재개가 중단 스테이지부터 이어진다.
- 외부 호출 제로 감사가 통과한다.
