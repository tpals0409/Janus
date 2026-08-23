import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  url: string
  protocols: string[]
  onopen: (() => void) | null = null
  onmessage: ((message: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false

  constructor(url: string, protocols: string[]) {
    this.url = url
    this.protocols = protocols
    FakeWebSocket.instances.push(this)
  }

  close() {
    if (this.closed) return
    this.closed = true
    this.onclose?.()
  }

  emit(value: object) {
    this.onmessage?.({ data: JSON.stringify(value) })
  }
}

describe('domain event client', () => {
  beforeEach(() => {
    vi.resetModules()
    FakeWebSocket.instances = []
    vi.stubGlobal('WebSocket', FakeWebSocket)
    Object.defineProperty(window, 'janus', {
      configurable: true,
      value: { authToken: 'renderer-token' }
    })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('shares one authenticated socket and dispatches only matching topics', async () => {
    const { subscribeDomainEvent } = await import('./domainEvents')
    const terminal = vi.fn()
    const workspace = vi.fn()
    const stopTerminal = subscribeDomainEvent('terminal', terminal)
    const stopWorkspace = subscribeDomainEvent('workspace', workspace)

    expect(FakeWebSocket.instances).toHaveLength(1)
    const socket = FakeWebSocket.instances[0]
    expect(socket.protocols).toEqual(['janus', 'renderer-token'])
    socket.emit({ topic: 'terminal', event: 'output', output: 'ok' })
    expect(terminal).toHaveBeenCalledOnce()
    expect(workspace).not.toHaveBeenCalled()

    stopTerminal()
    expect(socket.closed).toBe(false)
    stopWorkspace()
    expect(socket.closed).toBe(true)
  })
})
