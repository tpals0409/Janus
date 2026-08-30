/** 준비 과도 상태 폴링 판정 — workspace ready 이벤트를 놓쳐도 화면이 수렴해야 한다. */
import { describe, expect, it } from 'vitest'
import { awaitsPreparation } from './TaskWorkspace'

const task = (status: string, workspaceState: string | null) => ({
  id: 'task_1',
  status,
  workspace: workspaceState ? { state: workspaceState } : null
})

describe('awaitsPreparation', () => {
  it('preparing 과도 상태는 폴링한다 (task 또는 workspace 어느 쪽이든)', () => {
    expect(awaitsPreparation(task('preparing', null), null, false)).toBe(true)
    expect(awaitsPreparation(task('todo', 'preparing'), null, false)).toBe(true)
  })

  it('세션 없는 pendingDelegation은 자동 시작 대기 — 스토어가 stale이어도 폴링한다', () => {
    // 실측 52ms 준비가 이벤트 소켓 핸드셰이크보다 먼저 끝난 레이스의 재현:
    // 서버는 ready인데 스토어 refresh가 그 전 스냅샷에 머무른 상태.
    expect(awaitsPreparation(task('todo', null), 'task_1', false)).toBe(true)
    expect(awaitsPreparation(task('todo', 'ready'), 'task_1', false)).toBe(true)
  })

  it('세션이 시작됐거나 다른 태스크의 위임이면 폴링하지 않는다', () => {
    expect(awaitsPreparation(task('todo', 'ready'), 'task_1', true)).toBe(false)
    expect(awaitsPreparation(task('todo', 'ready'), 'task_2', false)).toBe(false)
    expect(awaitsPreparation(task('todo', 'ready'), null, false)).toBe(false)
    expect(awaitsPreparation(null, 'task_1', false)).toBe(false)
  })
})
