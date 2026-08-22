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
