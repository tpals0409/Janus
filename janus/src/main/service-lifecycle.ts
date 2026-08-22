import type { ChildProcess } from 'child_process'

export type ServicePhase =
  | 'starting'
  | 'up'
  | 'restarting'
  | 'failed'
  | 'external'
  | 'blocked'
  | 'stopped'
export type ServiceOwnership = 'none' | 'owned' | 'external'
export type EndpointState = 'down' | 'healthy' | 'foreign'

export interface ServiceRuntime {
  process: ChildProcess | null
  phase: ServicePhase
  ownership: ServiceOwnership
  pid: number | null
  lastPid: number | null
  attempts: number
  nextRetryAt: number
  startedAt: number | null
  lastError: string | null
}

export function createServiceRuntime(): ServiceRuntime {
  return {
    process: null,
    phase: 'starting',
    ownership: 'none',
    pid: null,
    lastPid: null,
    attempts: 0,
    nextRetryAt: 0,
    startedAt: null,
    lastError: null
  }
}

export function processAlive(p: ChildProcess | null): p is ChildProcess {
  return Boolean(p && p.exitCode === null && p.signalCode === null)
}

export function retryDelay(attempt: number): number {
  return Math.min(30_000, 1000 * 2 ** Math.min(Math.max(attempt - 1, 0), 5))
}

export function scheduleRestart(service: ServiceRuntime, reason: string, now = Date.now()): void {
  service.lastPid = service.pid
  service.process = null
  service.ownership = 'none'
  service.pid = null
  service.startedAt = null
  service.attempts += 1
  service.nextRetryAt = now + retryDelay(service.attempts)
  service.lastError = reason
  service.phase = service.attempts >= 3 ? 'failed' : 'restarting'
}

export function classifyEndpoint(portUp: boolean, healthy: boolean): EndpointState {
  if (!portUp) return 'down'
  return healthy ? 'healthy' : 'foreign'
}

export function markOwned(service: ServiceRuntime, process: ChildProcess, now = Date.now()): void {
  service.process = process
  service.ownership = 'owned'
  service.pid = process.pid ?? null
  service.lastPid = process.pid ?? service.lastPid
  service.startedAt = now
  service.phase = service.attempts ? 'restarting' : 'starting'
  service.nextRetryAt = 0
}

export function markExternal(service: ServiceRuntime): void {
  service.process = null
  service.ownership = 'external'
  service.pid = null
  service.startedAt = null
  service.phase = 'external'
  service.nextRetryAt = 0
  service.lastError = null
}

export function markBlocked(service: ServiceRuntime, reason: string): void {
  service.process = null
  service.ownership = 'none'
  service.pid = null
  service.startedAt = null
  service.phase = 'blocked'
  service.nextRetryAt = 0
  service.lastError = reason
}

type KillFn = (pid: number, signal: NodeJS.Signals) => boolean

function waitForExit(process: ChildProcess, timeoutMs: number): Promise<void> {
  if (!processAlive(process)) return Promise.resolve()
  return new Promise((resolve) => {
    const timer = setTimeout(done, timeoutMs)
    function done(): void {
      clearTimeout(timer)
      process.removeListener('exit', done)
      resolve()
    }
    process.once('exit', done)
  })
}

function signalProcess(process: ChildProcess, signal: NodeJS.Signals, killFn: KillFn): void {
  if (process.pid == null) return
  try {
    killFn(-process.pid, signal)
  } catch {
    try {
      process.kill(signal)
    } catch {
      // 이미 종료됨
    }
  }
}

export interface StopResult {
  signalled: boolean
  forced: boolean
  orphan: boolean
  pid: number | null
}

export async function stopOwnedService(
  service: ServiceRuntime,
  options: { graceMs?: number; forceMs?: number; killFn?: KillFn } = {}
): Promise<StopResult> {
  const process = service.process
  const pid = service.pid
  service.phase = 'stopped'
  if (service.ownership !== 'owned' || !processAlive(process)) {
    return { signalled: false, forced: false, orphan: false, pid }
  }

  const killFn = options.killFn ?? globalThis.process.kill.bind(globalThis.process)
  const graceMs = options.graceMs ?? 5000
  const forceMs = options.forceMs ?? 2000
  signalProcess(process, 'SIGTERM', killFn)
  await waitForExit(process, graceMs)
  let forced = false
  if (processAlive(process)) {
    forced = true
    signalProcess(process, 'SIGKILL', killFn)
    await waitForExit(process, forceMs)
  }
  const orphan = processAlive(process)
  service.lastPid = pid
  service.lastError = orphan ? `owned pid ${pid ?? 'unknown'} survived SIGKILL` : null
  return { signalled: true, forced, orphan, pid }
}
