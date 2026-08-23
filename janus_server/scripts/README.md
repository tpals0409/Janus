# P0 검증 명령

## 실제 Qwen3.8-27B smoke

`janus_server` 디렉터리에서 실행한다.

```bash
.venv/bin/python scripts/p0_smoke_27b.py
```

검증 항목은 멀티턴 기억, 실제 worker 생성, 개별 worker 즉시 중단, turn 취소 후
다음 메시지 재개다. 성공·실패는 프로세스 exit code와
`artifacts/p0/smoke/<timestamp>/result.json`에 함께 기록된다. MLX 조기 종료와 timeout은
같은 폴더의 `mlx-server.log` tail을 실패 JSON에도 포함한다.

이 명령은 macOS Metal 장치를 사용하므로 샌드박스가 Metal을 차단하는 실행 환경에서는
호스트 권한으로 실행해야 한다. 8080의 정상 MLX 서버가 이미 있으면 `external`로 기록해
사용만 하고 종료하지 않는다. 포트가 비어 있으면 harness가 서버를 별도 프로세스 그룹으로
시작하고 종료 시 자신이 소유한 그룹만 정리한다.

## P5 robustness soak

`uv run python scripts/robustness_soak.py`는 기본 30분 동안 crash/reopen/backup
루프를 반복하고 SQLite 무결성과 transient state 누수를 검사한다.
`../RECOVERY.md`에 백업, 복원, 명시적 초기화 정책이 있다.

## 오케스트레이션 상태머신 27B E2E

`uv run python scripts/verify_workflow_27b.py`는 실제 Qwen3.8-27B로
Explore fan-out → 구조화 Plan → Plan 소유권 기반 worktree Implement → 워커별 테스트 →
순차 머지·통합 검증 → 클린 Review를 완주한다. `plan` 실행 경계 직후 크래시 재개,
모델 폴백, 충돌 fixer, 리뷰 2회 초과 사람 개입, YAML 출력 계약, 토큰 계측과
loopback 전용 에어갭 감사도 함께 검증한다. 모델 서버 소유권·정리 정책은 P0 smoke와
동일하며, 실행 원본은 `artifacts/orchestration/runs/`에 로컬로만 남는다.

## 오케스트레이션 에어갭 번들

`uv run python scripts/build_orchestration_airgap_bundle.py artifacts/orchestration-airgap.zip`
은 엔진, 격리·검증 의존성, 엄격한 YAML 로더, 표준 템플릿, 모델 역할 매핑,
네트워크 게이트와 SHA-256 manifest를 재현 가능한 ZIP으로 만든다.
