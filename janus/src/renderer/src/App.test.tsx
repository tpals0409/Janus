import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import App from './App'
import { useStore } from './store'
import { seedTaskRuntimeVisualFixture } from './visualFixture'

vi.mock('./components/FileView', () => ({
  default: () => <div>선택한 프로젝트 루트 · 읽기 전용</div>
}))

describe('Janus renderer fixture', () => {
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
    useStore.setState({ archiveTask })
    await user.click(screen.getByRole('button', { name: 'Make Task runtime restart-safe 제거' }))
    expect(archiveTask).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: '작업 제거' }))
    expect(archiveTask).toHaveBeenCalledWith(useStore.getState().tasks[0].id)

    const runtimeGraph = screen.getByLabelText('런타임 워커 그래프')
    expect(within(runtimeGraph).getByText('test_worker')).toBeVisible()
    expect(within(runtimeGraph).getByText('억제')).toBeVisible()
    expect(within(runtimeGraph).getByText('queue_worker')).toBeVisible()
    expect(within(runtimeGraph).getByText('모델 대기')).toBeVisible()
    await user.click(screen.getByRole('button', { name: '새 대화' }))
    expect(screen.getByRole('textbox', { name: 'Janus에게 위임할 목표' })).toBeVisible()
    await user.type(screen.getByRole('textbox', { name: 'Janus에게 위임할 목표' }), '인증 흐름을 검증해줘')
    await user.keyboard('{Enter}')
    expect(delegateTask).toHaveBeenCalledWith('인증 흐름을 검증해줘')
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
    expect(screen.getByRole('heading', { name: '시스템 프롬프트' })).toBeVisible()
  })
})
