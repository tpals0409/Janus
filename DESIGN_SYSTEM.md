# Janus Design System

> Janus is a quiet, structural interface for configuring and operating local AI agents.

이 문서는 Janus UI의 공식 디자인 계약이다. 제품 구조와 충돌하는 일반적인 AI/SaaS 패턴보다
이 문서의 원칙을 우선한다.

초안에서 충돌하던 항목은 다음처럼 확정했다.

- `Tools` 전역 메뉴는 두지 않는다. 영속 자산은 AgentProfile의 `스킬`, 저수준 호출은 runtime `도구`다.
- `Context` 전역 메뉴는 두지 않는다. 정책은 AgentProfile, 실제 입력 검사는 Task Session에 속한다.
- 그래프의 `새 에이전트`는 제거한다. 워커 수명 주기는 오케스트레이터가 소유하고 그래프는 읽기 전용이다.
- Inspector는 고정 4열 레이아웃이 아니라 검사할 객체가 있을 때만 표시한다.
- `Dual structure`는 브랜드 장식이 아니라 실제 소유권·비교 관계가 있을 때만 사용한다.
- green은 선택 색이 아니라 살아 있는 시스템 상태 신호다. focus와 tab은 neutral로 유지한다.

## 1. 제품과 화면의 역할

Janus는 로컬 모델을 극한까지 효율적으로 사용하는 개발자를 위한 데스크톱 ADE다. 화면의 한 가지
핵심 역할은 **AgentProfile을 구성하고, Task 실행에서 오케스트레이터가 자원을 어떻게 사용했는지
검사하는 것**이다.

사용자가 느껴야 하는 인상은 “AI 앱”이 아니라 “새로운 개발 도구”다.

우선순위는 항상 다음과 같다.

1. Structure
2. Information
3. Interaction
4. State
5. Brand
6. Decoration

핵심 성격은 Quiet, Precise, Structural, Neutral, Technical, Desktop-first다.

## 2. 제품 용어와 소유권

디자인은 다음 도메인 경계를 감추거나 섞지 않는다.

| 사용자 용어 | 도메인 | 수명 | UI 소유권 |
|---|---|---|---|
| 에이전트 프로필 | `AgentProfile` | 영속 | 프롬프트, 스킬, 컨텍스트 정책, 실행 정책 |
| 오케스트레이터 | Task 실행의 루트 agent | Session | 실행 그래프의 고정 루트 |
| 워커 | 오케스트레이터가 만든 runtime worker | Dispatch/Session | 실제 실행 span으로만 표시 |
| 스킬 | 변환·설치된 `SkillVersion` | 영속/versioned | AgentProfile에서 활성화 |
| 도구 | 모델이 호출하는 저수준 runtime capability | 실행 | 활동 타임라인과 승인에서 표시 |
| 컨텍스트 정책 | 포함 소스와 압축 한도 | AgentProfile | 에이전트 내부 탭 |
| 컨텍스트 검사기 | 실제 조립된 입력과 token 상태 | Session | Task 실행 화면 |

다음 UI는 만들지 않는다.

- 그래프에서 `새 에이전트` 또는 `새 워커` 생성
- 영속 Worker 목록이나 Worker 설정 화면
- 전역 Context 메뉴
- 스킬과 runtime 도구를 같은 목록으로 표현
- 정적 Tool Map을 실행 그래프처럼 표현

## 3. 브랜드 모티프

공식 심볼의 두 프로필과 중앙 축은 세 가지 방식으로만 확장한다.

### Dual structure

실제 대립 관계가 있을 때 좌우 구조를 사용한다.

- Resource | Workspace
- Configuration | Execution
- Input | Output
- Human request | Agent activity

모든 화면을 억지로 좌우 분할하지 않는다.

### Central axis

중앙 축은 실행 관계에 사용한다.

```text
AgentProfile
     |
Orchestrator
     |
Runtime worker spans
```

그래프는 입력 도구가 아니라 읽기 전용 실행 뷰어다.

### Green signal

Muted green은 시스템이 살아 있고 정상적으로 진행 중임을 알리는 작은 신호다. 넓은 배경이나
브랜드 장식으로 사용하지 않는다.

## 4. 시각 원칙

Janus는 `IDE + system tool + modern desktop application`으로 보인다.

사용한다.

- Panel, divider, split view
- Toolbar, tree, inspector
- Command palette, context menu
- Inline status, activity timeline
- 작은 색 차이와 얇은 경계선

사용하지 않는다.

- Purple AI gradient, glow, glassmorphism
- 모든 섹션을 감싸는 카드
- 과도한 radius, shadow, spacing
- 큰 CTA와 AI sparkle icon
- 컬러가 많은 sidebar와 아이콘
- 로고 회전, bouncing, animated gradient

## 5. Color tokens

Neutral 95% + state color 5%를 유지한다. 영역은 색 면보다 border와 spacing으로 구분한다.

```css
:root {
  --bg-base: #0c0d0e;
  --bg-canvas: #101112;
  --bg-panel: #131516;
  --bg-surface: #17191b;
  --bg-hover: #1c1f21;
  --bg-active: #202326;

  --border-subtle: #1d2022;
  --border-default: #282c2f;
  --border-strong: #383d41;
  --focus-border: #52585d;

  --text-primary: #f0f1f1;
  --text-secondary: #a3a7aa;
  --text-muted: #686d71;
  --text-disabled: #464a4d;

  --accent: #83a995;
  --accent-hover: #91b5a2;
  --accent-muted: rgb(131 169 149 / 12%);
  --success: #83a995;
  --warning: #c1a36b;
  --danger: #c97878;
  --info: #7796ad;

  --diff-add-bg: rgb(100 160 120 / 10%);
  --diff-remove-bg: rgb(190 100 100 / 10%);
}
```

Accent는 연결, 준비, 저장, 정상 실행과 active runtime edge에만 사용한다. 선택된 내비게이션,
탭 indicator, 일반 버튼, AI 메시지에는 사용하지 않는다.

색만으로 상태를 전달하지 않는다.

| 상태 | 표식 | 색 |
|---|---|---|
| 준비 | `● Ready` | success |
| 실행 | `◉ Running` | success + 작은 pulse |
| 저장 | `✓ Saved` | success |
| 완료 | `✓ Complete` | success |
| 유휴 | `○ Idle` | muted |
| 승인 대기 | `△ Waiting` | warning |
| 실패 | `× Failed` | danger |

## 6. Typography

```css
--font-ui: Inter, Pretendard, system-ui, sans-serif;
--font-mono: "Geist Mono", "JetBrains Mono", ui-monospace, monospace;
```

- 11px: metadata, status bar
- 12px: secondary UI
- 13px: default UI
- 14px: important UI
- 16px: section heading
- 20px: page heading; 드물게 사용

Regular 400, Medium 500, Semibold 600을 쓴다. Bold 700은 사용하지 않는 것이 기본이다.

경로, 모델명, 명령, 로그, token, port, 스킬과 도구 이름에는 mono를 사용한다.

## 7. Spacing, radius, motion

4px grid를 사용한다.

```css
--space-1: 4px;
--space-2: 8px;
--space-3: 12px;
--space-4: 16px;
--space-5: 20px;
--space-6: 24px;
--space-8: 32px;

--radius-xs: 3px;
--radius-sm: 4px;
--radius-md: 6px;
--radius-lg: 8px;

--motion-fast: 100ms;
--motion-default: 160ms;
--motion-slow: 220ms;
```

Panel radius는 0이다. Button/Input은 4–6px, floating layer는 최대 8px다. 12px 이상 radius는
사용하지 않는다.

기본 UI는 shadow를 쓰지 않는다. Modal, dropdown, context menu, command palette만 다음 elevation을
사용한다.

```css
box-shadow: 0 16px 40px rgb(0 0 0 / 38%);
```

Motion은 hover, menu, panel, execution state 변화에만 짧게 사용하며 `prefers-reduced-motion`을
지원한다.

## 8. App shell

```text
+------------------------------------------------------------+
| { | } Janus                           model        ● Ready  |
+-----+----------------+-----------------------+--------------+
| NAV | RESOURCE       |                       | INSPECTOR*   |
|     |                |       WORKSPACE       |              |
|     |                |                       |              |
+-----+----------------+-----------------------+--------------+
| TASK CONTEXT         | ACTIVITY / CONTEXT INSPECTOR         |
+------------------------------------------------------------+
| ● server :8765  ● model :8080  ~/project          v1.0     |
+------------------------------------------------------------+
```

`INSPECTOR*`는 선택한 객체의 세부 정보가 있을 때만 나타난다. 프롬프트와 컨텍스트 정책처럼
중앙 편집기가 충분한 화면에는 빈 Inspector를 유지하지 않는다.

### Title bar

- 공식 심볼과 `Janus`를 한 번만 노출한다.
- 현재 Task 또는 AgentProfile 이름을 보여준다.
- 상태는 우측에 작은 inline signal로 표시한다.
- 큰 브랜딩 영역으로 사용하지 않는다.

### Navigation rail

- 폭 48–56px, icon only
- icon 16–18px, 1.5px stroke, monochrome
- active는 `rgba(255 255 255 / 5%)`와 primary text
- accent를 selection에 사용하지 않는다.

기본 항목은 `작업`, `에이전트`, `평가`, `모니터`다. 구현되지 않은 `배포`는 비활성 버튼으로
상시 노출하지 않는다. `스킬`과 `컨텍스트 정책`은 AgentProfile 내부에 둔다.

### Resource sidebar

- 폭 220–260px
- Agent 화면에서는 AgentProfile 목록
- Task 화면에서는 Project/Task tree
- 선택은 2px neutral marker 또는 아주 약한 active background
- 보라색 outline과 영속 Worker 목록을 사용하지 않는다.

Profile 생성 기능이 생기면 `새 에이전트`가 아니라 `새 프로필`이라고 부른다.

### Workspace

- 가장 중요한 영역이며 `--bg-canvas`를 사용한다.
- 모든 내용을 카드로 감싸지 않는다.
- empty state는 심볼, 상태 설명, 정확한 다음 행동만 제공한다.

### Inspector

- 폭 280–340px
- Card가 아니라 section과 separator로 구성
- 선택한 Task, span, tool call, file 등 실제 객체만 검사
- 편집 책임이 다른 화면에 있는 설정을 복제하지 않는다.

### Status bar

- 높이 22–24px, 11px typography
- server, model, workspace, version을 표시
- 색과 함께 텍스트 상태를 제공

## 9. Core components

디자인 요소는 화면별 Tailwind 조합으로 복제하지 않고 다음 primitive를 사용한다.

| Component | 역할 |
|---|---|
| `Button` | primary, secondary, ghost 세 variant |
| `IconButton` | toolbar와 compact action |
| `Tabs` | 1px primary indicator를 갖는 화면 전환 |
| `Field` | label, input/select/textarea, help, error |
| `Checkbox` | neutral check, 접근 가능한 label |
| `SegmentedControl` | 작은 모드 선택 |
| `Panel` | radius 없는 구조 영역 |
| `Section` | inspector heading과 separator |
| `Status` | glyph + label + semantic tone |
| `Toolbar` | 36px compact control row |
| `EmptyState` | 상태 설명과 하나의 다음 행동 |
| `Menu` | dropdown/context menu 공통 row |
| `Dialog` | modal shell과 focus management |

컴포넌트 내부를 제외한 TSX에는 raw hex 색상을 쓰지 않는다. 같은 Tailwind class 조합이 세 번
반복되면 primitive 또는 recipe로 승격한다.

## 10. Controls

### Button

- 높이 30–32px, radius 5–6px
- Primary: 최종 실행 액션만, 밝은 neutral 배경과 어두운 text
- Secondary: surface + default border
- Ghost: transparent + secondary text
- Accent green button은 만들지 않는다.

### Input

- 높이 32–36px
- surface background, default border, 6px radius
- focus는 `--focus-border`; purple/blue ring 금지
- 오류는 danger border와 명시적 문장으로 표현

### Tabs

- 높이 34px
- active text primary + 1px primary indicator
- inactive text muted
- accent indicator 금지

### Checkbox

- checked는 밝은 neutral fill + dark check
- accent는 시스템 상태를 직접 제어할 때만 사용

### Toggle / segmented control

- radius 6px 이하
- selected는 active background + primary text
- pill 형태를 사용하지 않는다.

## 11. AgentProfile 화면

AgentProfile은 캐릭터가 아니라 실행 계약이다.

```text
Assistant                                      ● Ready
General orchestrator
```

탭은 `프롬프트`, `스킬`, `컨텍스트 정책`, `그래프`다.

- 프롬프트: 실제 system prompt 편집
- 스킬: 설치된 SkillVersion 활성화
- 컨텍스트 정책: 고정 소스와 압축 한도
- 그래프: 읽기 전용 runtime ownership

그래프는 선택한 AgentProfile을 고정 루트로 두고, 같은 프로필의 실제 Task Session에서 생성된
worker span만 자식으로 표시한다. worker 생성·삭제·설정 control과 YAML 편집기를 두지 않는다.

## 12. Skill과 runtime tool

Skill row는 이름, 출처/version, activation mode, compatibility를 표시한다. 저수준 runtime tool은
대화와 활동, 승인, span inspector에서 mono 이름으로 표시한다.

```text
✓  read_file
✓  glob
△  edit_file                         승인 필요
```

개별 아이콘에 색을 주지 않는다. approval은 작은 warning label로 표현한다.

## 13. Task execution

Task 화면은 chatbot이 아니라 실행 관제대다.

```text
12:43  You          API authentication 구조를 수정해줘.
12:43  Assistant    Inspecting authentication flow.
       → read_file   src/auth.ts
       → grep        verifyToken
       △ edit_file   승인 필요
```

Activity glyph는 pending `○`, active `◉`, complete `✓`, failed `×`, waiting `△`를 사용한다.

### Approval

Panel 전체를 warning 색으로 채우지 않는다. tool, path, 변경량과 `△ 승인 필요`를 표시하고
`거부`, `검토`, `허용`의 명확한 행동을 제공한다.

### Context inspector

실제 포함 소스, 제외 이유, 정적 token 추정, 최신 context-window와 압축 상태를 보여준다. 정책
편집을 이 화면에 복제하지 않는다.

### Diff

색 면적을 작게 유지하고 text를 중심으로 한다. 추가/삭제 배경은 지정된 diff token만 사용한다.

## 14. Floating interaction

### Command palette

- `⌘K`, 폭 520–600px
- 가장 강한 elevation을 갖는 UI
- 작업 검색, Task 실행, AgentProfile 전환, 스킬 열기, 로그 열기 같은 실제 기능만 제공
- 아직 존재하지 않는 추상 명령은 노출하지 않는다.

### Context menu

- row 28–32px, radius 6–8px
- 구조 변경선으로 destructive action을 분리
- 선택한 객체에 가능한 행동만 표시

## 15. Logo usage

- Title bar: 심볼 + `Janus`
- Collapsed navigation: 심볼
- Splash: 심볼 + `Janus`
- 한 화면에서 반복 노출하지 않는다.
- 로고를 loader로 회전시키거나 glow를 적용하지 않는다.
- 공식 SVG의 geometry는 수정하지 않고 theme별 stroke variant만 파생한다.

## 16. Density와 접근성

- Nav item 32px
- Sidebar row 30–32px
- Input/Button 32px
- Tab 34px
- Inspector row 32px
- Toolbar 36px

Janus는 desktop-first이며 기본 최소 폭은 1024px로 본다. 좁은 폭에서는 Inspector를 숨기거나
overlay로 전환하되 Workspace를 먼저 보존한다.

모든 interactive control은 keyboard focus, accessible name, disabled state를 제공한다. 상태는 색과
glyph와 label을 함께 사용하며 reduced motion을 지원한다.

## 17. 최종 검수 기준

화면마다 다음 질문을 순서대로 확인한다.

1. 이 영역의 소유권과 경계가 명확한가?
2. 가장 중요한 정보가 primary text인가?
3. 반복되는 조작이 공통 component인가?
4. 상태가 색 없이도 구분되는가?
5. accent가 실제 system signal에만 쓰였는가?
6. 제거해도 의미가 유지되는 장식이 남아 있는가?
7. AgentProfile과 runtime Worker를 혼동하게 하는 UI가 없는가?

Visual formula:

> Graphite + Monochrome + Thin Borders + Compact Density + Precise Typography + Muted Green Signal + Minimal Motion
