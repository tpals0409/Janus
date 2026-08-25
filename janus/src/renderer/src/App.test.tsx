import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import { useStore } from './store'
import { seedTaskRuntimeVisualFixture } from './visualFixture'
import type { AgentProfileSkill } from './types'

vi.mock('./components/FileView', () => ({
  default: () => <div>선택한 프로젝트 루트 · 읽기 전용</div>
}))

describe('Janus renderer fixture', () => {
  it('shows durable turn evidence and completes slash skill names', async () => {
    window.history.replaceState({}, '', '/?fixture=task-runtime')
    seedTaskRuntimeVisualFixture()
    const session = useStore.getState().taskSession!
    const fixtureEvent = useStore.getState().taskSessionEvents.at(-1)!
    const revokeTaskApprovalScope = vi.fn().mockResolvedValue(undefined)
    useStore.setState({
      taskConnected: true,
      serverUp: true,
      mlxUp: true,
      backendStatus: {
        server: {
          phase: 'up', attempts: 0, retryInMs: 0, lastError: null,
          logPath: '/logs/server.log'
        },
        mlx: {
          phase: 'up', attempts: 0, retryInMs: 0, lastError: null,
          logPath: '/logs/mlx.log',
          acceleration: {
            policy: 'required', configured: true, active: true, kind: 'mtp',
            draftModelPath: '/models/mtp', lastError: null
          }
        }
      },
      revokeTaskApprovalScope,
      taskSession: {
        ...session,
        approval_scopes: [{
          session_id: session.id,
          workspace_id: session.workspace_id,
          scope: 'workspace_write',
          created_at: new Date().toISOString()
        }],
        skills: [{
          skill_version_id: 'skill-version-review',
          name: 'review',
          namespace: 'local',
          description: 'Review changes',
          activation_mode: 'manual',
          version: 1,
          loaded_at: null
        } as AgentProfileSkill]
      },
      taskSessionEvents: [
        ...useStore.getState().taskSessionEvents,
        {
          ...fixtureEvent,
          seq: 20,
          kind: 'agent_event',
          payload: {
            type: 'agent_event', kind: 'speculative_metrics',
            acceptance_rate: 0.75, accepted_tokens: 9, draft_tokens: 12,
            predicted_tokens_per_second: 31.5
          }
        },
        {
          ...fixtureEvent,
          seq: 21,
          kind: 'turn_end',
          payload: {
            type: 'turn_end',
            outcome: {
              outcome: 'partial',
              summary: '구현은 끝났고 패키징 검증이 남았습니다.',
              evidence: ['pnpm test: 11 passed']
            }
          }
        }
      ]
    })
    const user = userEvent.setup()

    render(<App />)

    const executionRail = screen.getByText('최근 실행')
    expect(executionRail).toBeVisible()
    // 도구 실행이 대화 흐름 안에 행으로 보인다 (계약 §13)
    const readRow = screen.getByText('read_file').closest('.task-tool-row')!
    expect(readRow).toHaveAttribute('data-status', 'done')
    expect(within(readRow as HTMLElement).getByText('janus_server/recovery.py')).toBeVisible()
    const testRow = screen.getByText('run_bash').closest('.task-tool-row')!
    expect(within(testRow as HTMLElement).getByText('17.6s')).toBeVisible()
    expect(screen.getByText('도구 2회')).toBeVisible()
    expect(screen.getByText('모델 :8080 · MTP 활성')).toBeVisible()
    await user.click(executionRail)
    expect(screen.getByText(/구현은 끝났고 패키징 검증이 남았습니다/)).toBeVisible()
    expect(screen.getByText('pnpm test: 11 passed')).toBeVisible()
    await user.click(screen.getByText('실행 설정'))
    expect(screen.getByText(/MTP · 승인 75.0%.*9\/12.*31.5 tok\/s/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: '파일 수정 권한 취소' }))
    expect(revokeTaskApprovalScope).toHaveBeenCalledWith('workspace_write')
    const composer = screen.getByRole('textbox', { name: '작업 지시' })
    await user.type(composer, '/')
    expect(screen.getByRole('listbox', { name: '사용 가능한 스킬' })).toBeVisible()
    expect(screen.getByText('Review changes')).toBeVisible()
    await user.keyboard('{Enter}')
    expect(composer).toHaveValue('/review ')
  })

  it('completes slash skill names on the new task composer', async () => {
    window.history.replaceState({}, '', '/?fixture=task-runtime')
    seedTaskRuntimeVisualFixture()
    useStore.setState({
      task: null,
      taskId: null,
      taskConnected: false,
      agentProfileSkills: [{
        skill_id: 'skill-debug',
        skill_version_id: 'skill-version-debug',
        name: 'debug',
        namespace: 'janus',
        description: 'Find the root cause first',
        activation_mode: 'manual',
        version: 1,
        loaded_at: null
      } as AgentProfileSkill]
    })
    const user = userEvent.setup()

    render(<App />)

    const composer = screen.getByRole('textbox', { name: 'Janus에게 위임할 목표' })
    await user.type(composer, '/')
    expect(screen.getByRole('listbox', { name: '사용 가능한 스킬' })).toBeVisible()
    expect(screen.getByText('Find the root cause first')).toBeVisible()
    await user.keyboard('{Enter}')
    expect(composer).toHaveValue('/debug ')
  })

  it('opens a worker as a chat modal with its state badge and elapsed time', async () => {
    window.history.replaceState({}, '', '/?fixture=task-runtime')
    seedTaskRuntimeVisualFixture()
    const fixtureEvent = useStore.getState().taskSessionEvents.at(-1)!
    const started = new Date(Date.now() - 90_000).toISOString()
    useStore.setState({
      taskConnected: true,
      taskSessionEvents: [
        ...useStore.getState().taskSessionEvents,
        {
          ...fixtureEvent, seq: 20, kind: 'span_start', created_at: started,
          payload: {
            type: 'span_start',
            span: { id: 'span-w2', node_id: 'w2', label: 'impl_worker', status: 'running' }
          }
        },
        {
          ...fixtureEvent, seq: 21, kind: 'agent_event',
          payload: {
            type: 'agent_event', kind: 'reasoning_delta', worker_id: 'w2',
            text: '호출부를 먼저 찾는다.'
          }
        },
        {
          ...fixtureEvent, seq: 22, kind: 'agent_event',
          payload: {
            type: 'agent_event', kind: 'tool_start', worker_id: 'w2',
            name: 'grep', args: { pattern: 'BudgetCancel' }
          }
        },
        {
          ...fixtureEvent, seq: 23, kind: 'agent_event',
          payload: {
            type: 'agent_event', kind: 'assistant', worker_id: 'w2',
            content: '세 곳을 고쳤습니다.'
          }
        }
      ]
    })
    const user = userEvent.setup()

    render(<App />)

    // 워커 활동은 대화창에서 걸러진다 — 모달을 열기 전에는 보이면 안 된다.
    expect(screen.queryByText('호출부를 먼저 찾는다.')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '워커 impl_worker 상세' }))
    const modal = screen.getByRole('dialog', { name: '워커 impl_worker' })
    expect(within(modal).getByText('호출부를 먼저 찾는다.')).toBeVisible()
    expect(within(modal).getByText('grep')).toBeVisible()
    expect(within(modal).getByText('세 곳을 고쳤습니다.')).toBeVisible()
    expect(within(modal).getByText(/실행 중/)).toBeVisible()
    expect(within(modal).getByText(/1분 3[0-9]초/)).toBeVisible()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: '워커 impl_worker' })).not.toBeInTheDocument()
  })

  it('answers a worker approval from inside the worker modal', async () => {
    window.history.replaceState({}, '', '/?fixture=task-runtime')
    seedTaskRuntimeVisualFixture()
    const fixtureEvent = useStore.getState().taskSessionEvents.at(-1)!
    const respondTaskApproval = vi.fn()
    useStore.setState({
      taskConnected: true,
      respondTaskApproval,
      taskApprovals: [{
        id: 'req-1',
        node_id: 'w2',
        tool: 'edit_file',
        args: { path: 'Card.tsx' },
        task_id: fixtureEvent.task_id,
        workspace_id: fixtureEvent.workspace_id ?? '',
        dispatch_id: fixtureEvent.dispatch_id,
        rememberable: true,
        approval_scope: 'workspace_write',
        deadline_epoch_ms: Date.now() + 125_000
      }],
      taskSessionEvents: [
        ...useStore.getState().taskSessionEvents,
        {
          ...fixtureEvent, seq: 30, kind: 'span_start',
          payload: {
            type: 'span_start',
            span: { id: 'span-w2', node_id: 'w2', label: 'impl_worker', status: 'running' }
          }
        },
        {
          ...fixtureEvent, seq: 31, kind: 'agent_event',
          payload: {
            type: 'agent_event', kind: 'worker_state', worker_id: 'w2',
            status: 'waiting_approval', tool: 'edit_file'
          }
        }
      ]
    })
    const user = userEvent.setup()

    render(<App />)
    expect(screen.getAllByText(/남은 시간 2:0\d/).length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: '워커 impl_worker 상세' }))
    const modal = screen.getByRole('dialog', { name: '워커 impl_worker' })
    expect(within(modal).getByText('edit_file')).toBeVisible()
    await user.click(within(modal).getByRole('button', { name: '이 세션에서 파일 수정 허용' }))
    expect(respondTaskApproval).toHaveBeenCalledWith('req-1', true, 'session_workspace')
  })

  it('gates implementation on explicit mockup approval', async () => {
    window.history.replaceState({}, '', '/?fixture=task-runtime')
    seedTaskRuntimeVisualFixture()
    const task = useStore.getState().task!
    const approveTaskMockup = vi.fn().mockResolvedValue(undefined)
    useStore.setState({
      task: { ...task, workflow_stage: 'mockup' },
      tasks: useStore.getState().tasks.map((item) => (
        item.id === task.id ? { ...item, workflow_stage: 'mockup' } : item
      )),
      taskConnected: true,
      approveTaskMockup
    })
    const user = userEvent.setup()

    render(<App />)

    expect(screen.getByText('프론트 목업 승인 대기')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '거절 · 수정 요청' }))
    expect(screen.getByRole('textbox', { name: '작업 지시' })).toHaveValue('목업 수정 요청: ')
    expect(approveTaskMockup).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '목업 승인 · 구현 진행' }))
    expect(approveTaskMockup).toHaveBeenCalledOnce()
  })

  it('offers a visible restart action after a session stops', async () => {
    window.history.replaceState({}, '', '/?fixture=task-runtime')
    seedTaskRuntimeVisualFixture()
    const session = useStore.getState().taskSession!
    const startTaskSession = vi.fn().mockResolvedValue(undefined)
    useStore.setState({
      taskSession: { ...session, status: 'stopped' },
      taskConnected: false,
      startTaskSession
    })
    const user = userEvent.setup()

    render(<App />)

    const restart = screen.getByRole('button', { name: '다시 시작' })
    expect(restart).toBeVisible()
    await user.click(restart)
    expect(startTaskSession).toHaveBeenCalledWith({ priority: 0, queue_timeout_ms: 300000 })
  })

  it('renders the Task-first shell and navigates to AgentProfile configuration', async () => {
    window.history.replaceState({}, '', '/?fixture=task-runtime')
    seedTaskRuntimeVisualFixture()
    const fixtureEvents = useStore.getState().taskSessionEvents
    const fixtureEvent = fixtureEvents.at(-1)!
    useStore.setState({
      taskSessionEvents: [
        ...fixtureEvents,
        { ...fixtureEvent, seq: 6, kind: 'optimistic_transcript', payload: { kind: 'user', content: '중복 테스트' } },
        { ...fixtureEvent, seq: 7, kind: 'agent_event', payload: { type: 'agent_event', kind: 'user', content: '중복 테스트' } },
        { ...fixtureEvent, seq: 8, kind: 'agent_event', payload: { type: 'agent_event', kind: 'worker_spawn_suppressed', name: 'test_worker', role: 'verifier', reason: 'model_queue_backpressure' } },
        { ...fixtureEvent, seq: 9, kind: 'agent_event', payload: { type: 'agent_event', kind: 'user', worker_id: 'w1-researcher', content: '워커에게 보낸 지시' } },
        { ...fixtureEvent, seq: 10, kind: 'agent_event', payload: { type: 'agent_event', kind: 'reasoning_delta', text: '먼저 파일을 ' } },
        { ...fixtureEvent, seq: 11, kind: 'agent_event', payload: { type: 'agent_event', kind: 'reasoning_delta', text: '읽어야 한다.' } },
        { ...fixtureEvent, seq: 12, kind: 'agent_event', payload: { type: 'agent_event', kind: 'text_delta', text: '## 결과\n\n`build` 를 ' } },
        { ...fixtureEvent, seq: 13, kind: 'agent_event', payload: { type: 'agent_event', kind: 'text_delta', text: '**고쳤습' } },
        { ...fixtureEvent, seq: 14, kind: 'agent_event', payload: { type: 'agent_event', kind: 'assistant', content: '## 결과\n\n`build` 를 **고쳤습니다**.\n\n| 항목 | 상태 |\n| --- | --- |\n| 번들 | 통과 |' } },
        { ...fixtureEvent, seq: 15, kind: 'agent_event', payload: { type: 'agent_event', kind: 'assistant', content: '\n\n' } },
        { ...fixtureEvent, seq: 16, kind: 'span_start', payload: { type: 'span_start', span: { id: 'span-worker-queued', node_id: 'w1-queued', worker_id: 'w1-queued', label: 'queue_worker', status: 'running', input: { role: 'implementer', task: '구현', tools: ['run_bash'] } } } },
        { ...fixtureEvent, seq: 17, kind: 'agent_event', payload: { type: 'agent_event', kind: 'worker_state', worker_id: 'w1-queued', status: 'queued' } }
      ]
    })
    const delegateTask = vi.fn().mockResolvedValue(undefined)
    useStore.setState({ delegateTask })
    const user = userEvent.setup()

    render(<App />)

    expect(screen.getByRole('heading', { name: 'Make Task runtime restart-safe' })).toBeVisible()
    expect(screen.getByRole('navigation', { name: '기본 탐색' })).toBeVisible()
    expect(screen.getByText('janus-server :8765')).toBeVisible()
    expect(screen.getByRole('complementary', { name: '실행 컨텍스트' })).toBeVisible()
    expect(screen.getByRole('textbox', { name: '작업 지시' })).toBeVisible()
    expect(screen.getAllByText('중복 테스트')).toHaveLength(1)
    // worker 지시가 대화에 섞이면 사용자가 두 번 입력한 것처럼 보인다
    expect(screen.queryByText('워커에게 보낸 지시')).toBeNull()
    // 사고 조각은 한 덩어리로 이어 붙여 접힌 블록으로 보여준다
    expect(screen.getByText('사고 과정')).toBeVisible()
    expect(screen.getByText('먼저 파일을 읽어야 한다.')).toBeInTheDocument()
    // 개행뿐인 답은 빈 말풍선이 된다 — 아예 만들지 않는다
    expect(document.querySelectorAll('.task-message[data-role="assistant"]')).toHaveLength(2)
    // 답변은 흘러나오는 조각을 이어 붙이고, 완결 이벤트가 오면 그 자리를 대체한다 — 두 번 뜨면 안 된다
    expect(screen.getAllByText(/고쳤습니다/)).toHaveLength(1)
    // 답변은 마크다운으로 렌더된다 — 렌더러를 지연 로딩하므로 기다린다
    expect(await screen.findByRole('heading', { name: '결과' })).toBeVisible()
    expect(screen.getByText('고쳤습니다')).toBeVisible()
    expect(screen.queryByText(/## 결과/)).toBeNull()
    // 표는 CommonMark가 아니라 GFM — 플러그인이 빠지면 파이프 문자가 그대로 남는다
    expect(screen.getByRole('table')).toBeVisible()
    expect(screen.getByRole('columnheader', { name: '항목' })).toBeVisible()
    expect(screen.getByRole('cell', { name: '통과' })).toBeVisible()
    // 턴이 도는 동안 진행 중임이 보여야 한다 — 마지막 이벤트가 단계를 말해준다
    useStore.setState({ taskTurnActive: false })
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
    useStore.setState({ taskTurnActive: true })
    expect(await screen.findByRole('status')).toHaveTextContent('답하는 중')
    useStore.setState({
      taskSessionEvents: [...useStore.getState().taskSessionEvents,
        { ...fixtureEvent, seq: 16, kind: 'agent_event', payload: { type: 'agent_event', kind: 'reasoning_delta', text: '다시 생각' } }]
    })
    expect(await screen.findByRole('status')).toHaveTextContent('사고 중')
    useStore.setState({ taskTurnActive: false })
    await waitFor(() => expect(screen.queryByRole('status')).toBeNull())

    // 프로젝트는 목록이 아니라 상단 스위쳐다 — 아래 모든 것이 그 프로젝트에 속한다
    const switcher = screen.getByRole('button', { name: /Janus P1 Demo/ })
    expect(switcher).toHaveAttribute('aria-expanded', 'false')
    await user.click(switcher)
    expect(await screen.findByRole('menu', { name: '프로젝트 전환' })).toBeVisible()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu', { name: '프로젝트 전환' })).toBeNull()

    // 작업은 사이드바에서 바로 제거할 수 있어야 한다 — 확인을 거친 뒤에만 지운다
    const archiveTask = vi.fn().mockResolvedValue(undefined)
    const stopTaskSession = vi.fn().mockImplementation(async () => {
      const session = useStore.getState().taskSession
      if (session) useStore.setState({ taskSession: { ...session, status: 'stopped' }, taskRuntimeError: null })
    })
    useStore.setState({ archiveTask, stopTaskSession })
    await user.click(screen.getByRole('button', { name: 'Make Task runtime restart-safe 제거' }))
    expect(archiveTask).not.toHaveBeenCalled()
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: '세션 중단' }))
    expect(stopTaskSession).toHaveBeenCalledTimes(1)
    expect(await screen.findByRole('button', { name: '작업 제거' })).toBeVisible()
    await user.click(screen.getByRole('button', { name: '작업 제거' }))
    expect(archiveTask).toHaveBeenCalledWith(useStore.getState().tasks[0].id)

    const runtimeGraph = screen.getByLabelText('Janus 실행 흐름')
    expect(within(runtimeGraph).getByText('요청')).toBeVisible()
    expect(within(runtimeGraph).getByText('JANUS')).toBeVisible()
    expect(within(runtimeGraph).getByText('test_worker')).toBeVisible()
    expect(within(runtimeGraph).getByText('억제')).toBeVisible()
    expect(within(runtimeGraph).getByText(/검증/)).toBeVisible()
    expect(within(runtimeGraph).getByText('queue_worker')).toBeVisible()
    expect(within(runtimeGraph).getByText('모델 대기')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '새 대화' }))
    expect(screen.getByRole('textbox', { name: 'Janus에게 위임할 목표' })).toBeVisible()
    await user.type(screen.getByRole('textbox', { name: 'Janus에게 위임할 목표' }), '인증 흐름을 검증해줘')
    await user.keyboard('{Enter}')
    expect(delegateTask).toHaveBeenCalledWith('인증 흐름을 검증해줘', 'direct')
    expect(screen.queryByRole('button', { name: '새 작업' })).not.toBeInTheDocument()

    useStore.setState({
      tree: { '': [{ name: 'README.md', type: 'file', size: 1200 }] },
      openedFile: { path: 'README.md', content: '# Janus' }
    })
    await user.click(screen.getByRole('tab', { name: '파일' }))
    expect(screen.getByText('Janus · 프로젝트 루트')).toBeVisible()
    expect(screen.getByRole('button', { name: 'README.md' })).toBeVisible()
    expect(await screen.findByText('선택한 프로젝트 루트 · 읽기 전용')).toBeVisible()

    await user.click(screen.getByRole('button', { name: '에이전트' }))
    expect(screen.getByRole('tablist', { name: '에이전트 프로필' })).toBeVisible()
    expect(screen.getByRole('tab', { name: '대시보드' })).toBeVisible()
    expect(screen.getByLabelText('에이전트 운영 현황')).toBeVisible()
    expect(screen.getByText('실행 방식')).toBeVisible()
    expect(screen.queryByText('작업 하나를 완료하는 방법')).not.toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: '지침' }))
    expect(screen.queryByRole('heading', { name: '지침' })).not.toBeInTheDocument()
    expect((screen.getByLabelText('코딩 규칙') as HTMLTextAreaElement).value).toContain('# Coding Rules')

    await user.click(screen.getByRole('tab', { name: '스킬' }))
    expect(screen.getByText('설치된 스킬이 없습니다')).toBeVisible()
    expect(screen.getByText('0개 활성', { selector: '.ui-status span:last-child' })).toBeVisible()

    expect(screen.queryByRole('button', { name: '모니터' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '작업' }))
    await user.click(screen.getByRole('tab', { name: /작업/ }))
    const development = screen.getByRole('button', { name: '개발 도구' })
    await user.click(development)
    expect(development).toHaveAttribute('aria-current', 'page')
  })

  it('marks an unanswered approval as expired and lets the user dismiss it', async () => {
    window.history.replaceState({}, '', '/?fixture=task-runtime')
    seedTaskRuntimeVisualFixture()
    const fixtureEvent = useStore.getState().taskSessionEvents.at(-1)!
    useStore.setState({
      taskConnected: true,
      taskApprovals: [{
        id: 'req-expired',
        node_id: 'w1',
        tool: 'write_file',
        args: {},
        task_id: fixtureEvent.task_id,
        workspace_id: fixtureEvent.workspace_id ?? '',
        dispatch_id: fixtureEvent.dispatch_id,
        rememberable: false,
        deadline_epoch_ms: Date.now() - 1_000
      }]
    })
    const user = userEvent.setup()

    render(<App />)

    const hint = screen.getByText('제한 시간 안에 응답이 없어 거부로 처리했습니다.')
    expect(hint).toBeVisible()
    expect(screen.queryByText('이번만 허용')).toBeNull()
    const card = hint.closest('.task-decision-card')!
    await user.click(within(card as HTMLElement).getByRole('button', { name: '닫기' }))
    expect(useStore.getState().taskApprovals).toHaveLength(0)
  })

  it('shows elapsed and remembered duration while the model loads', async () => {
    window.history.replaceState({}, '', '/?fixture=task-runtime')
    seedTaskRuntimeVisualFixture()
    localStorage.setItem('janus.model-load-seconds', '74')
    useStore.setState({
      taskConnected: true,
      serverUp: true,
      mlxUp: false,
      backendStatus: {
        server: {
          phase: 'up', attempts: 0, retryInMs: 0, lastError: null,
          logPath: '/logs/server.log'
        },
        mlx: {
          phase: 'starting', attempts: 1, retryInMs: 0, lastError: null,
          logPath: '/logs/mlx.log',
          acceleration: {
            policy: 'required', configured: true, active: false, kind: 'mtp',
            draftModelPath: '/models/mtp', lastError: null
          }
        }
      }
    })

    render(<App />)

    expect(screen.getByText(/모델·MTP 로딩 중 · 0초 \(지난번 74초\)/)).toBeVisible()
    localStorage.removeItem('janus.model-load-seconds')
  })

  it('keeps the composer open during an active turn and queues for the next one', async () => {
    window.history.replaceState({}, '', '/?fixture=task-runtime')
    seedTaskRuntimeVisualFixture()
    useStore.setState({ taskConnected: true, taskTurnActive: true })

    render(<App />)

    const composer = screen.getByRole('textbox', { name: '작업 지시' })
    expect(composer).toBeEnabled()
    expect(composer).toHaveAttribute('placeholder', '지금 보내면 이 턴이 끝난 뒤 실행됩니다')
    expect(screen.getByRole('button', { name: '턴 취소' })).toBeVisible()
    expect(screen.getByRole('button', { name: '다음 턴으로 보내기' })).toBeVisible()
  })

  it('opens the Evaluation Lab from the navigation', async () => {
    window.history.replaceState({}, '', '/?fixture=task-runtime')
    seedTaskRuntimeVisualFixture()
    useStore.setState({ taskConnected: true })
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } })
    ))
    const user = userEvent.setup()

    render(<App />)
    await user.click(screen.getByRole('button', { name: '평가' }))

    expect(await screen.findByText('실험이 아직 없습니다')).toBeVisible()
    expect(screen.getAllByRole('button', { name: /새 실험/ }).length).toBeGreaterThan(0)
    vi.unstubAllGlobals()
  })
})
