import { useState } from 'react'
import { CheckCircle2, CircleDashed, Loader2, XCircle } from 'lucide-react'
import { useStore } from '../../store'
import SessionView from './SessionView'
import type { Span } from '../../types'

function pretty(v: unknown): string {
  if (v == null) return '—'
  if (typeof v === 'string') return v
  return JSON.stringify(v, null, 2)
}

function StatusIcon({ s }: { s: Span['status'] }) {
  if (s === 'running') return <Loader2 size={13} className="animate-spin text-accent-fg" />
  if (s === 'error') return <XCircle size={13} className="text-danger" />
  return <CheckCircle2 size={13} className="text-ok" />
}

export default function TracePanel() {
  const spans = useStore((s) => s.spans)
  const selectedSpanId = useStore((s) => s.selectedSpanId)
  const selectSpan = useStore((s) => s.selectSpan)
  const running = useStore((s) => s.running)
  const runError = useStore((s) => s.runError)
  const [io, setIo] = useState<'input' | 'output'>('output')

  const liveEvents = useStore((s) => s.liveEvents)
  const span = spans.find((s) => s.id === selectedSpanId)
  const total = spans.reduce((a, s) => Math.max(a, (s.started_ms ?? 0) + (s.duration_ms ?? 0)), 0)

  // 아직 안 끝난 agent 노드는 스팬에 events가 없다 — live에서 가져온다
  const events = span ? (span.events ?? liveEvents[span.node_id] ?? []) : []
  const isLive = Boolean(span && span.status === 'running' && liveEvents[span.node_id])

  if (!spans.length) {
    return (
      <div className="grid h-full place-items-center text-[12px] text-faint">
        {runError ? (
          <span className="text-danger">{runError}</span>
        ) : running ? (
          <span className="flex items-center gap-2">
            <Loader2 size={13} className="animate-spin" /> 실행 중…
          </span>
        ) : (
          <span className="flex items-center gap-2">
            <CircleDashed size={13} /> Run을 눌러 그래프를 실행하세요
          </span>
        )}
      </div>
    )
  }

  return (
    <div className="flex h-full">
      {/* 스팬 목록 */}
      <div className="w-[300px] shrink-0 overflow-y-auto border-r border-border">
        <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-[11px] text-muted">
          <span className={running ? 'text-accent-fg' : 'text-ok'}>
            {running ? '● Running' : '● Success'}
          </span>
          <span className="ml-auto font-mono">{(total / 1000).toFixed(2)}s</span>
        </div>
        {spans.map((s) => (
          <button
            key={s.id}
            onClick={() => selectSpan(s.id)}
            className="flex w-full items-center gap-2 border-l-2 px-3 py-2 text-left hover:bg-panel-2"
            style={{
              borderLeftColor: s.id === selectedSpanId ? 'var(--color-accent)' : 'transparent',
              background: s.id === selectedSpanId ? 'var(--color-panel-2)' : undefined
            }}
          >
            <StatusIcon s={s.status} />
            <span className="truncate text-[12px]">{s.node_id}</span>
            {(s.events?.length || liveEvents[s.node_id]?.length) ? (
              <span className="shrink-0 rounded bg-raised px-1 text-[9.5px] text-faint">
                {s.events?.length ?? liveEvents[s.node_id]?.length}
              </span>
            ) : null}
            <span className="ml-auto shrink-0 font-mono text-[11px] text-muted">
              {s.duration_ms == null ? '…' : `${(s.duration_ms / 1000).toFixed(1)}s`}
            </span>
          </button>
        ))}
      </div>

      {/* 선택 스팬 상세 */}
      <div className="min-w-0 flex-1 overflow-y-auto px-4 py-3">
        {span ? (
          <>
            <div className="mb-2 flex items-center gap-2">
              <StatusIcon s={span.status} />
              <span className="font-medium">{span.node_id}</span>
              <span className="ml-auto font-mono text-[11px] text-muted">
                +{span.started_ms}ms · {span.duration_ms ?? '…'}ms
              </span>
            </div>
            {events.length ? (
              <SessionView events={events} live={isLive} />
            ) : (
              <div className="rounded-md border border-border bg-panel-2 p-2">
                <div className="mb-1 text-[10px] tracking-wider text-faint">RESULT</div>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-fg">
                  {pretty(span.output)}
                </pre>
              </div>
            )}
          </>
        ) : (
          <div className="text-[12px] text-faint">스팬을 선택하세요</div>
        )}
      </div>

      {/* Input / Output */}
      <div className="w-[320px] shrink-0 overflow-y-auto border-l border-border">
        <div className="flex gap-1 border-b border-border px-3 py-2">
          {(['input', 'output'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setIo(t)}
              className="rounded px-2 py-0.5 text-[11px] capitalize"
              style={{
                background: io === t ? 'var(--color-accent-soft)' : 'transparent',
                color: io === t ? 'var(--color-accent-fg)' : 'var(--color-muted)'
              }}
            >
              {t}
            </button>
          ))}
        </div>
        <pre className="whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed text-muted">
          {span ? pretty(io === 'input' ? span.input : span.output) : '—'}
        </pre>
      </div>
    </div>
  )
}
