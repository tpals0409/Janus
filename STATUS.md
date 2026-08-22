# Janus 상태 (오케스트레이터-워커 전환, 2026-08-22)

로컬 우선 Agent Development Environment. Electron 앱 + FastAPI 서버 +
로컬 MLX 모델(Qwen3.8-27B 4bit).

**판정: 아키텍처가 원래 계획과 일치하게 됐다.** 사람이 배선하는 정적 DAG(LangGraph)를
삭제하고 오케스트레이터-워커 런타임으로 전환했다. 에이전트 = 오케스트레이터 1개,
워커는 오케스트레이터가 `create_worker` 스킬로 런타임에 만들고 실행의 트레이스에만
존재한다. 캔버스는 스펙 에디터가 아니라 **트레이스 뷰어**다(그래프 = 출력).

**검증(2026-08-22)**: `pytest tests/` 26개 전부 통과(15회 반복 무플레이크) /
도구·spec 자체 검사 통과 / 렌더러 타입체크 통과 / 실서버 헤드리스 스모크
(인증 거부·message→스팬 스트림·MLX 부재 시 즉시 run_error) 통과.
**실 모델 스모크는 아직**: `pnpm dev`로 27B 상대 대화+워커 스폰은 사람 손 필요.

---

## 아키텍처 (전환 후)

- **스펙** (`spec.py`, ~120줄): YAML = 평평한 오케스트레이터 설정
  (name/model/system_prompt/tools/approval/max_steps). 그래프 없음.
- **런타임** (`runtime.py`, 신규): `Orchestration` = WS 연결당 대화 하나.
  지속 `Session`으로 멀티턴. `create_worker`는 실행별 클로저로 주입 —
  전역 REGISTRY 불변. 같은 턴의 도구 호출은 전부 병렬 스레드.
- **WS 프로토콜**: client→ `message`/`approval_response`/`cancel`/`stop_worker`.
  server→ `run_start`/`span_start`/`span_end`/`agent_event`(+`call_id`)/
  `approval_request`/`turn_end`(신설, 저장 후 발송)/`run_error`.
  `run`/`token`/`run_end` 폐기. 첫 message = 실행 시작, 소켓 close = 대화 종료.
- **취소 의미론**: `cancel` = 현재 턴만 중단, 세션 유지. `stop_worker` = 그 워커만.
- **UI**: 캔버스 노드 = 스팬(오케스트레이터 + 스폰된 워커). 노드 클릭 →
  오케스트레이터는 대화창(컴포저), 워커는 로그 + Stop. Run 버튼/RunBar 삭제.
  우측 패널 = 오케스트레이터 설정 폼.

## 안전 규칙 (전환 후에도 유지·강화)

- 위험 도구 승인 게이트는 `tools.dispatch` 단일 초크포인트 (C1 수정 유지).
  워커가 실행해도 같은 게이트를 지난다 — 회귀 테스트 있음.
- `create_worker`를 YAML tools에 적으면 검증 거부 (항상 런타임 주입).
- 워커 도구 = 요청 ∩ 오케스트레이터 spec.tools. 워커는 `create_worker`를
  못 받는다 → 스폰 깊이 1 보장. 부분집합·깊이 회귀 테스트 있음.
- 인증(기동별 토큰 + Origin, HTTP/WS)·CORS 순서(bb5b0d1)·워크스페이스 jail 무변경.

## 삭제된 것

`compile.py`(318줄), `trace.py`(213줄), 그래프 spec 검증(~250줄), 그래프 샘플 3개,
`test_trace.py`, langchain-openai·langgraph 의존성, 렌더러 그래프 편집
(팔레트·엣지·노드 추가/삭제/rename, RunBar, EdgeInspector 등 ~500줄). 순 디프 음수.

## 남은 것 (한눈에)

| | 내용 |
|---|---|
| **실 모델 스모크** | `pnpm dev`로 27B 상대: 대화+워커 스폰 / stop_worker / cancel 후 계속 / 과거 실행 재열람·A/B |
| **H5** | 강제 종료 시 27B 모델이 RAM에 고아로 남고, 다음 실행이 옛 코드 백엔드에 조용히 붙음 |
| **M4** | 에이전트 삭제 시 `runs/` 고아 → 같은 slug 새 에이전트가 남의 히스토리 상속 |
| **MLX 다운 UX** | 컴포저가 미리 안 잠김. 대신 보내면 즉시 run_error로 정직하게 실패 (행 없음) — 심각도 하락 |
| **IDE** | 검색, 출력 복사, 키보드 단축키, 긴 세션 가상화 (Undo/Delete 문제는 캔버스 편집 삭제로 소멸) |
| **리포** | README, 배포 패키징(`pnpm dev` 전용) |
| **ponytail 표시** | 고정 2열 캔버스 레이아웃(워커 ~8개↑면 dagre), cancel=stop-turn(대화 리셋=새 연결), 병렬은 도구 I/O만 겹침(MLX 1대 직렬) |

해결·소멸: M8(거짓 없는 즉시 실패로 강등), M10(`_make_llm` 자체가 삭제됨),
캔버스 Delete 사고(편집 제거로 소멸).

## 테스트

```bash
cd janus_server && uv run pytest tests/ -q      # 26 passed
uv run python -m janus_server.tools             # 도구 자체 검사 (jail 포함)
uv run python -m janus_server.spec              # 오케스트레이터 스펙 검증
cd janus && npx tsc --noEmit                    # 렌더러 타입체크
```

핵심 런타임 테스트(`test_runtime.py`, FakeClient·MLX 불필요): 단일 턴 스트림+저장 /
멀티턴 세션 유지+파일 덮어쓰기 / 워커 부분집합+깊이1 / Barrier로 병렬 스폰 증명 /
워커별 취소 / cancel 후 세션 유지. 보안(`test_security.py`): 인증·CORS·WS 핸드셰이크
+ 병렬 위험 도구 승인 2건 독립 왕복.

## 실행법

```bash
cd janus && pnpm dev     # 앱이 janus-server(:8765)와 MLX(:8080)를 직접 띄우고, 끄면 함께 종료
```
백엔드 로그: `/tmp/janus-server.log`, `/tmp/janus-mlx.log`
