import { afterEach, describe, expect, it, vi } from 'vitest'
import { closeAllLiveRuntimes, useStore } from './store'

class FakeSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static created: string[] = []
  static closed = 0
  onopen: (() => void) | null = null
  onmessage: unknown = null
  onerror: unknown = null
  onclose: unknown = null
  readyState = 0
  constructor(url: string, _protocols?: string[]) {
    FakeSocket.created.push(url)
  }
  close() { FakeSocket.closed += 1 }
  send() {}
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' }
  })
}

const task = {
  id: 'task_1', title: 'T', workspace: { id: 'ws_1', state: 'ready' }
}

function sessionDetail(status: string, dispatchStatus: string): unknown {
  return {
    id: 'session_1', task_id: 'task_1', dispatch_id: 'dispatch_1',
    status, dispatch: { id: 'dispatch_1', status: dispatchStatus },
    events: []
  }
}

function stubFetch(latest: unknown): void {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/tasks/task_1')) return jsonResponse(task)
    if (url.endsWith('/sessions/latest')) return jsonResponse(latest)
    return jsonResponse([])
  }))
}

describe('selectTask auto-reconnect', () => {
  afterEach(() => {
    closeAllLiveRuntimes()
    FakeSocket.created = []
    FakeSocket.closed = 0
    useStore.setState({ taskWs: null, taskSession: null, taskId: null })
    vi.unstubAllGlobals()
  })

  it('reconnects a resumable session so leaving the screen does not strand it', async () => {
    vi.stubGlobal('WebSocket', FakeSocket)
    stubFetch(sessionDetail('idle', 'needs_you'))
    await useStore.getState().selectTask('task_1')
    expect(FakeSocket.created).toEqual([
      expect.stringContaining('/tasks/task_1/sessions/session_1')
    ])
  })

  it('does not connect when the dispatch is terminal', async () => {
    vi.stubGlobal('WebSocket', FakeSocket)
    stubFetch(sessionDetail('idle', 'succeeded'))
    await useStore.getState().selectTask('task_1')
    expect(FakeSocket.created).toEqual([])
  })

  it('keeps the session socket alive across navigation and reattaches on return', async () => {
    vi.stubGlobal('WebSocket', FakeSocket)
    stubFetch(sessionDetail('idle', 'needs_you'))
    await useStore.getState().selectTask('task_1')
    expect(FakeSocket.created).toHaveLength(1)
    const socket = useStore.getState().taskWs

    // 다른 Task로 이동: 소켓을 닫지 않는다. (task_2에는 세션이 없다)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/tasks/task_2')) {
        return jsonResponse({ id: 'task_2', title: 'T2', workspace: { id: 'ws_2', state: 'ready' } })
      }
      if (url.endsWith('/sessions/latest')) return jsonResponse({ detail: 'none' }, 404)
      return jsonResponse([])
    }))
    await useStore.getState().selectTask('task_2')
    expect(FakeSocket.closed).toBe(0)
    expect(useStore.getState().taskWs).toBeNull()

    // 돌아오면 새 소켓 없이 기존 런타임에 재부착된다.
    stubFetch(sessionDetail('idle', 'needs_you'))
    await useStore.getState().selectTask('task_1')
    expect(FakeSocket.created).toHaveLength(1)
    expect(useStore.getState().taskWs).toBe(socket)
  })
})
