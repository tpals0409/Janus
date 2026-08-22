# TaskSuite v0

세 fixture는 각각 단일 파일 버그 수정, 여러 파일 refactor, 문서 조사 후 코드·테스트
수정을 고정된 objective/constraints/acceptance로 검증한다. 원본 fixture는 항상 실패하며
매 실행은 새 workspace 복사본에서 순차 실행된다.

전체 baseline 명령:

```bash
cd janus_server
.venv/bin/python scripts/run_tasksuite_v0.py
```

기본값은 `none`, `fixed_one`, `autonomous` 정책을 각 Task에서 5회씩, 총 45회 실행한다.
모델에는 workspace jail이 적용되는 파일 도구만 제공하고 shell은 제공하지 않는다. 고정
acceptance command는 harness가 직접 실행하고 verification 구간으로 기록한다.

2026-08-22의 실제 27B baseline은
`artifacts/p0/tasksuite/20260822-115844/`에 저장돼 있다. 45회 중 44회가 통과했고,
유일한 실패는 조사 Task의 fixed-one이 코드와 독립 acceptance는 성공했지만 최종 모델
생성이 180초 turn budget을 넘긴 경우다. 보수적으로 실행 실패로 유지했다.
