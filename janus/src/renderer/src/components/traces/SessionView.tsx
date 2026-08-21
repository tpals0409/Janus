import { useState } from 'react'
import { ChevronRight, MessageSquareText, User, Wrench } from 'lucide-react'
import type { AgentEvent } from '../../types'

/** tool_start와 뒤따르는 tool_result를 한 줄로 짝지은 것 */
interface ToolRun {
  name: string
  args?: Record<string, unknown>
  value?: Record<string, unknown>
  pending: boolean
}

type Row =
  | { kind: 'user'; text: string }
  | { kind: 'assistant'; text: string; streaming?: boolean }
  | { kind: 'tools'; runs: ToolRun[] }
  | { kind: 'llm_call'; messages: { role: string; content: string }[]; total: number }

/** 이벤트 스트림을 화면 줄로 접는다. 연속된 툴 호출은 한 묶음으로 모은다. */
function toRows(events: AgentEvent[]): Row[] {
  const rows: Row[] = []
  for (const e of events) {
    if (e.kind === 'user') {
      rows.push({ kind: 'user', text: e.content ?? '' })
    } else if (e.kind === 'text_delta') {
      // 토큰이 도착하는 대로 마지막 streaming 행에 붙인다 — 로컬 모델이 느려서
      // 완성본을 기다리면 몇 초간 빈 화면이 된다.
      const last = rows[rows.length - 1]
      if (last && last.kind === 'assistant' && last.streaming) last.text += e.text ?? ''
      else rows.push({ kind: 'assistant', text: e.text ?? '', streaming: true })
    } else if (e.kind === 'assistant' && e.content) {
      // 완성된 응답 — 방금까지 쌓던 streaming 행을 확정한다 (중복 방지)
      const last = rows[rows.length - 1]
      if (last && last.kind === 'assistant' && last.streaming) {
        last.text = e.content
        last.streaming = false
      } else {
        rows.push({ kind: 'assistant', text: e.content })
      }
    } else if (e.kind === 'llm_call') {
      rows.push({ kind: 'llm_call', messages: e.messages ?? [], total: e.total_messages ?? (e.messages?.length ?? 0) })
    } else if (e.kind === 'tool_start') {
      const last = rows[rows.length - 1]
      const run: ToolRun = { name: e.name ?? '?', args: e.args, pending: true }
      if (last && last.kind === 'tools') last.runs.push(run)
      else rows.push({ kind: 'tools', runs: [run] })
    } else if (e.kind === 'tool_result') {
      // 가장 최근의 미완료 호출에 결과를 붙인다
      for (let i = rows.length - 1; i >= 0; i--) {
        const r = rows[i]
        if (r.kind !== 'tools') continue
        const run = [...r.runs].reverse().find((x) => x.pending && x.name === e.name)
        if (run) {
          run.value = e.value
          run.pending = false
        }
        break
      }
    }
  }
  return rows
}

function preview(v: unknown, n = 90): string {
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '…' : s
}

/** 툴 호출은 상자가 아니라 접히는 한 줄이다 — 긴 세션이 스캔 가능해지는 이유. */
function ToolLine({ run }: { run: ToolRun }) {
  const [open, setOpen] = useState(false)
  const failed = Boolean(run.value && 'error' in run.value)

  return (
    <div className="group">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1.5 py-0.5 text-left font-mono text-[11px]"
      >
        <ChevronRight
          size={11}
          className="shrink-0 text-faint transition-transform group-hover:opacity-100"
          style={{ transform: open ? 'rotate(90deg)' : 'none', opacity: open ? 1 : 0.35 }}
        />
        <Wrench size={10} className="shrink-0" style={{ color: 'var(--color-node-agent)' }} />
        <span className="shrink-0" style={{ color: failed ? 'var(--color-danger)' : undefined }}>
          {run.name}
        </span>
        <span className="truncate text-faint">{preview(run.args)}</span>
        {run.pending && <span className="ml-auto shrink-0 text-[10px] text-accent-fg">…</span>}
      </button>

      {open && (
        <div className="ml-4 space-y-1 border-l border-border pl-2.5">
          <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words font-mono text-[10.5px] text-muted">
            {JSON.stringify(run.args, null, 2)}
          </pre>
          {run.value && (
            <pre
              className="max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[10.5px]"
              style={{ color: failed ? 'var(--color-danger)' : 'var(--color-fg)' }}
            >
              {JSON.stringify(run.value, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}

/** 모델에 실제로 보낸 메시지 — 템플릿 치환이 끝난 전문. 접힌 채로는 한 줄이다. */
function PromptLine({ messages, total }: { messages: { role: string; content: string }[]; total: number }) {
  const [open, setOpen] = useState(false)
  const chars = messages.reduce((a, m) => a + (m.content?.length ?? 0), 0)
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-1.5 py-0.5 text-left font-mono text-[11px] text-faint hover:text-muted"
      >
        <ChevronRight size={11} className="shrink-0 transition-transform"
          style={{ transform: open ? 'rotate(90deg)' : 'none', opacity: open ? 1 : 0.35 }} />
        <MessageSquareText size={10} className="shrink-0 text-accent-fg" />
        <span>prompt</span>
        <span className="truncate">
          {messages.map((m) => m.role).join('+')} · {chars}자
          {total > messages.length ? ` (증분, 전체 ${total}개)` : ''}
        </span>
      </button>
      {open && (
        <div className="ml-4 space-y-1.5 border-l border-border pl-2.5">
          {messages.map((m, i) => (
            <div key={i}>
              <div className="text-[9.5px] uppercase tracking-wider text-faint">{m.role}</div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono text-[10.5px] text-muted">
                {m.content}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function SessionView({
  events,
  live
}: {
  events: AgentEvent[]
  live?: boolean
}) {
  const rows = toRows(events)
  const steps = events.filter((e) => e.kind === 'step').length

  if (!rows.length) {
    return (
      <div className="py-4 text-[12px] text-faint">
        {live ? '에이전트 시작 중…' : '이 노드는 세션이 없습니다 (에이전트 노드가 아님)'}
      </div>
    )
  }

  return (
    <div className="space-y-2.5">
      <div className="text-[10px] tracking-wider text-faint">
        SESSION · step {steps} {live && <span className="text-accent-fg">· 진행 중</span>}
      </div>

      {rows.map((row, i) =>
        row.kind === 'llm_call' ? (
          <PromptLine key={i} messages={row.messages} total={row.total} />
        ) : row.kind === 'tools' ? (
          <div key={i} className="space-y-0.5">
            {row.runs.map((r, j) => (
              <ToolLine key={j} run={r} />
            ))}
          </div>
        ) : row.kind === 'user' ? (
          <div key={i} className="rounded-md border border-border bg-raised px-2.5 py-1.5">
            <div className="mb-0.5 flex items-center gap-1.5 text-[10px] text-faint">
              <User size={10} /> TASK
            </div>
            <div className="whitespace-pre-wrap break-words text-[11.5px] leading-relaxed text-muted">
              {row.text}
            </div>
          </div>
        ) : (
          <div
            key={i}
            className="whitespace-pre-wrap break-words text-[12px] leading-relaxed text-fg"
          >
            {row.text}
            {row.streaming && (
              <span className="ml-0.5 inline-block h-3.5 w-[2px] animate-pulse bg-accent-fg align-middle" />
            )}
          </div>
        )
      )}

      {live && (
        <div className="flex gap-1 pt-1">
          {[0, 1, 2].map((i) => (
            <span
              key={i}
              className="h-1 w-1 animate-bounce rounded-full bg-accent-fg"
              style={{ animationDelay: `${i * 160}ms` }}
            />
          ))}
        </div>
      )}
    </div>
  )
}
