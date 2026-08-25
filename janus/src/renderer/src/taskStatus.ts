import type { Task, TaskStatus } from './types'

export interface TaskStatusMeta {
  label: string
  color: string
}

/** Task 상태 표기의 단일 소스 — 사이드바·작업 화면이 함께 쓴다. */
export const TASK_STATUS_META: Record<TaskStatus, TaskStatusMeta> = {
  todo: { label: '할 일', color: 'var(--color-muted)' },
  preparing: { label: '준비 중', color: 'var(--color-warn)' },
  working: { label: '작업 중', color: 'var(--color-accent-fg)' },
  needs_you: { label: '응답 대기', color: 'var(--color-warn)' },
  review: { label: '검토', color: 'var(--color-ok)' },
  failed: { label: '실패', color: 'var(--color-danger)' },
}

/** attention_reason까지 반영한 실제 표시 상태. */
export function taskStatusMeta(task: Task): TaskStatusMeta {
  if (task.status === 'needs_you' && task.attention_reason === 'conversation_idle') {
    return { ...TASK_STATUS_META.needs_you, label: '대화 가능' }
  }
  if (task.status === 'needs_you' && task.attention_reason === 'mockup_review') {
    return { ...TASK_STATUS_META.needs_you, label: '목업 검토 필요' }
  }
  return TASK_STATUS_META[task.status]
}
