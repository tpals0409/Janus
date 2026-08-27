import { afterEach, describe, expect, it, vi } from 'vitest'
import { useStore } from './store'

class FakeSocket {
  static created: string[] = []
  onopen: (() => void) | null = null
  onmessage: unknown = null
  onerror: unknown = null
  onclose: unknown = null
  readyState = 0
  constructor(url: string, _protocols?: string[]) {
    FakeSocket.created.push(url)
  }
  close() {}
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
    FakeSocket.created = []
    useStore.setState({ taskWs: null, taskSession: null })
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
})
