import { createHash } from 'crypto'

export interface CapturedConsole {
  at: string
  level: string
  message: string
  line?: number
  source?: string
}

export interface CapturedNetwork {
  at: string
  method: string
  url: string
  status?: number
  error?: string
}

export function normalizePreviewUrl(value: string): string {
  const raw = value.trim()
  const parsed = new URL(raw.includes('://') ? raw : `http://${raw}`)
  const local = parsed.hostname === 'localhost' || parsed.hostname === '127.0.0.1'
    || parsed.hostname === '::1' || parsed.hostname === '[::1]'
  if (!local || !['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error('Task preview는 localhost HTTP(S) URL만 열 수 있습니다')
  }
  parsed.username = ''
  parsed.password = ''
  return parsed.toString()
}

export function taskBrowserPartition(taskId: string): string {
  if (!/^task_[A-Za-z0-9]+$/.test(taskId)) throw new Error('올바르지 않은 Task ID입니다')
  const digest = createHash('sha256').update(taskId).digest('hex').slice(0, 24)
  return `persist:janus-task-${digest}`
}

export class BoundedCapture<T> {
  readonly limit: number
  private values: T[] = []

  constructor(limit = 500) {
    this.limit = Math.max(1, limit)
  }

  add(value: T): void {
    this.values.push(value)
    if (this.values.length > this.limit) this.values.splice(0, this.values.length - this.limit)
  }

  snapshot(): T[] {
    return this.values.slice()
  }
}
