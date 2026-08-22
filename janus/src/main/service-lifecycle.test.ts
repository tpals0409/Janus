import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import test from 'node:test'
import { spawn, type ChildProcess } from 'node:child_process'

import {
  appendBoundedText,
  classifyServiceFailure,
  classifyEndpoint,
  createServiceRuntime,
  markExternal,
  markOwned,
  retryDelay,
  scheduleRestart,
  stopOwnedService
} from './service-lifecycle.ts'

test('service output tail stays bounded and preserves the newest failure evidence', () => {
  const value = appendBoundedText('a'.repeat(12), 'OOM:last', 10)
  assert.equal(value, 'aaOOM:last')
  assert.equal(value.length, 10)
})

test('model OOM is explicit and recoverable instead of a generic restart', () => {
  const failure = classifyServiceFailure('exit=137 signal=SIGKILL', 'Metal out of memory')
  assert.equal(failure.kind, 'model_oom')
  assert.equal(failure.retryable, true)
  assert.match(failure.action, /worker|context/)
})

test('disk-full service failure has a storage recovery action', () => {
  const failure = classifyServiceFailure('exit=1', 'ENOSPC: no space left on device')
  assert.equal(failure.kind, 'storage_write')
  assert.match(failure.action, /디스크/)
})

function fakeProcess(pid: number): ChildProcess {
  const process = new EventEmitter() as EventEmitter & Record<string, unknown>
  process.pid = pid
  process.exitCode = null
  process.signalCode = null
  process.kill = () => true
  return process as unknown as ChildProcess
}

function exit(process: ChildProcess, signal: NodeJS.Signals): void {
  ;(process as unknown as { signalCode: NodeJS.Signals | null }).signalCode = signal
  process.emit('exit', null, signal)
}

test('healthy external service and foreign/stale port are distinct', () => {
  assert.equal(classifyEndpoint(false, false), 'down')
  assert.equal(classifyEndpoint(true, true), 'healthy')
  assert.equal(classifyEndpoint(true, false), 'foreign')
})

test('restart backoff reaches failed after three repeated exits', () => {
  const service = createServiceRuntime()
  scheduleRestart(service, 'first', 100)
  assert.equal(service.phase, 'restarting')
  assert.equal(service.nextRetryAt, 100 + retryDelay(1))
  scheduleRestart(service, 'second', 200)
  assert.equal(service.phase, 'restarting')
  scheduleRestart(service, 'third', 300)
  assert.equal(service.phase, 'failed')
  assert.equal(service.attempts, 3)
})

test('external service is recorded but never signalled', async () => {
  const service = createServiceRuntime()
  markExternal(service)
  let signals = 0
  const result = await stopOwnedService(service, {
    killFn: () => {
      signals += 1
      return true
    }
  })
  assert.equal(service.ownership, 'external')
  assert.equal(signals, 0)
  assert.deepEqual(result, { signalled: false, forced: false, orphan: false, pid: null })
})

test('owned process group gets TERM and records pid ownership', async () => {
  const child = fakeProcess(4321)
  const service = createServiceRuntime()
  markOwned(service, child, 10)
  const signals: Array<[number, NodeJS.Signals]> = []
  const result = await stopOwnedService(service, {
    graceMs: 5,
    killFn: (pid, signal) => {
      signals.push([pid, signal])
      exit(child, signal)
      return true
    }
  })
  assert.equal(service.ownership, 'owned')
  assert.equal(service.pid, 4321)
  assert.equal(service.lastPid, 4321)
  assert.deepEqual(signals, [[-4321, 'SIGTERM']])
  assert.deepEqual(result, { signalled: true, forced: false, orphan: false, pid: 4321 })
})

test('stubborn owned process is force-killed and orphan result is explicit', async () => {
  const child = fakeProcess(9876)
  const service = createServiceRuntime()
  markOwned(service, child)
  const signals: NodeJS.Signals[] = []
  const result = await stopOwnedService(service, {
    graceMs: 1,
    forceMs: 1,
    killFn: (_pid, signal) => {
      signals.push(signal)
      if (signal === 'SIGKILL') exit(child, signal)
      return true
    }
  })
  assert.deepEqual(signals, ['SIGTERM', 'SIGKILL'])
  assert.equal(result.forced, true)
  assert.equal(result.orphan, false)
})

test('survivor after SIGKILL is reported as orphan', async () => {
  const child = fakeProcess(7777)
  const service = createServiceRuntime()
  markOwned(service, child)
  const result = await stopOwnedService(service, {
    graceMs: 1,
    forceMs: 1,
    killFn: () => true
  })
  assert.equal(result.orphan, true)
  assert.match(service.lastError ?? '', /survived SIGKILL/)
})

test('real detached process groups survive no repeated start-stop cycle', async () => {
  for (let cycle = 0; cycle < 3; cycle += 1) {
    const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], {
      detached: true,
      stdio: 'ignore'
    })
    await new Promise<void>((resolve, reject) => {
      child.once('spawn', resolve)
      child.once('error', reject)
    })
    const service = createServiceRuntime()
    markOwned(service, child)
    const result = await stopOwnedService(service, { graceMs: 1000, forceMs: 500 })
    assert.equal(result.orphan, false, `cycle ${cycle + 1}`)
    assert.equal(child.exitCode !== null || child.signalCode !== null, true, `cycle ${cycle + 1}`)
  }
})
