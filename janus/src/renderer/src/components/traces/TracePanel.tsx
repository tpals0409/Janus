import { useState } from 'react'
import {
  CheckCircle2,
  CircleDashed,
  Clock,
  Columns2,
  Loader2,
  RefreshCw,
  Send,
  X,
  XCircle
} from 'lucide-react'
import { ORCH_ID, useStore } from '../../store'
import SessionView from './SessionView'
import type { Span } from '../../types'

/** 토큰을 '비용'이 아니라 '지연시간의 원인'으로 보여준다. 로컬은 tok/s가 벽. */
function perfLine(s: Span): string | null {
  if (!s.usage) return null
  const secs = (s.duration_ms ?? 0) / 1000
  const gen = s.usage.completion_tokens
  const rate = secs > 0 && gen ? ` · ${(gen / secs).toFixed(1)} tok/s` : ''
  return `프롬프트 ${s.usage.prompt_tokens} · 생성 ${gen}${rate}`
}

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

function shortAt(at: string): string {
  return /^\d{8}-\d{6}/.test(at) ? `${at.slice(4, 8)} ${at.slice(9, 15)}` : at
}

const isOrch = (s?: Span | null) => !s || s.node_id === ORCH_ID

/** 오케스트레이터에게 말을 거는 입력줄. 첫 메시지가 곧 실행 시작이다. */
function Composer() {
  const turnActive = useStore((s) => s.turnActive)
  const errors = useStore((s) => s.errors)
  const agentId = useStore((s) => s.agentId)
  const sendMessage = useStore((s) => s.sendMessage)
  const [draft, setDraft] = useState('')

  const disabled = turnActive || !agentId || errors.length > 0
  const submit = () => {
    if (disabled || !draft.trim()) return
    sendMessage(draft)
    setDraft('')
  }

  return (
    <div className="shrink-0 border-t border-border px-3 py-2">
      <div className="flex items-end gap-2">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
          rows={2}
          placeholder={
            errors.length > 0
              ? '스펙 오류를 먼저 고치세요'
              : turnActive
                ? '턴이 끝나면 이어서 보낼 수 있습니다…'
                : '오케스트레이터에게 메시지… (Enter 전송, Shift+Enter 줄바꿈)'
          }
          disabled={disabled}
          className="min-h-[38px] flex-1 resize-y rounded-md border border-border-strong bg-raised px-2.5 py-1.5 text-[12px] outline-none focus:border-border-emphasis disabled:opacity-50"
        />
        <button
          onClick={submit}
          disabled={disabled || !draft.trim()}
          title="전송"
          className="flex h-[38px] shrink-0 items-center gap-1.5 rounded-md px-3 text-[12px] font-medium text-white disabled:opacity-40"
          style={{ background: 'var(--color-accent)' }}
        >
          {turnActive ? <Loader2 size={13} className="animate-spin" /> : <Send size={13} />}
        </button>
      </div>
    </div>
  )
}

function ComparisonColumn({
  label,
  at,
  span,
  inputs,
  events = [],
  live = false
}: {
  label: 'A' | 'B'
  at: string
  span?: Span
  inputs: Record<string, string>
  events?: NonNullable<Span['events']>
  live?: boolean
}) {
  return (
    <div className="min-w-0 flex-1 overflow-y-auto px-4 py-3">
      <div className="mb-3 flex items-center gap-2 border-b border-border pb-2">
        <span
          className="rounded px-1.5 py-0.5 font-mono text-[10px] font-semibold"
          style={{
            color: label === 'A' ? 'var(--color-accent-fg)' : 'var(--color-warn)',
            background: label === 'A' ? 'var(--color-accent-soft)' : 'color-mix(in srgb, var(--warning) 10%, transparent)'
          }}
        >
          {label}
        </span>
        <span className="text-[11px] text-muted">{shortAt(at)}</span>
        {span && (
          <span className="ml-auto font-mono text-[10px] text-faint">
            {(span.duration_ms ?? 0) / 1000}s
          </span>
        )}
      </div>

      <details className="mb-3 rounded-md border border-border bg-panel-2 p-2">
        <summary className="cursor-pointer text-[10px] tracking-wider text-faint">실행 입력</summary>
        <pre className="mt-2 whitespace-pre-wrap break-words font-mono text-[11px] text-muted">
          {pretty(inputs)}
        </pre>
      </details>

      {span ? (
        <>
          <div className="mb-2 flex items-center gap-2">
            <StatusIcon s={span.status} />
            <span className="truncate text-[12px] font-medium">{span.label ?? span.node_id}</span>
          </div>
          {perfLine(span) && (
            <div className="mb-2 font-mono text-[10.5px] text-faint">{perfLine(span)}</div>
          )}
          <div className="mb-3 rounded-md border border-border bg-panel-2 p-2">
            <div className="mb-1 text-[10px] tracking-wider text-faint">출력</div>
            <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-fg">
              {pretty(span.output)}
            </pre>
          </div>
          {events.length > 0 && <SessionView events={events} live={live} />}
        </>
      ) : (
        <div className="rounded-md border border-dashed border-border p-3 text-[11px] text-faint">
          A에서 선택한 노드와 같은 노드가 이 실행에는 없습니다.
        </div>
      )}
    </div>
  )
}

export default function TracePanel() {
  const spans = useStore((s) => s.spans)
  const selectedSpanId = useStore((s) => s.selectedSpanId)
  const selectSpan = useStore((s) => s.selectSpan)
  const turnActive = useStore((s) => s.turnActive)
  const connected = useStore((s) => s.connected)
  const runError = useStore((s) => s.runError)
  const cancelled = useStore((s) => s.cancelled)
  const pastRuns = useStore((s) => s.pastRuns)
  const viewingRunId = useStore((s) => s.viewingRunId)
  const loadRun = useStore((s) => s.loadRun)
  const comparisonRun = useStore((s) => s.comparisonRun)
  const loadComparison = useStore((s) => s.loadComparison)
  const clearComparison = useStore((s) => s.clearComparison)
  const rerun = useStore((s) => s.rerun)
  const rerunRun = useStore((s) => s.rerunRun)
  const firstMessage = useStore((s) => s.firstMessage)
  const stopWorker = useStore((s) => s.stopWorker)
  const [showHistory, setShowHistory] = useState(false)
  const [io, setIo] = useState<'input' | 'output'>('output')

  const liveEvents = useStore((s) => s.liveEvents)
  const span = spans.find((s) => s.id === selectedSpanId) ?? spans.find((s) => s.node_id === ORCH_ID)
  const total = spans.reduce((a, s) => Math.max(a, (s.started_ms ?? 0) + (s.duration_ms ?? 0)), 0)
  const failed = Boolean(runError || spans.some((s) => s.status === 'error'))
  const runState = turnActive
    ? 'running'
    : cancelled
      ? 'cancelled'
      : failed
        ? 'error'
        : connected
          ? 'idle'
          : 'success'
  const comparisonSpan = comparisonRun?.spans.find((s) => s.node_id === span?.node_id)
  const primaryAt = viewingRunId
    ? (pastRuns.find((r) => r.id === viewingRunId)?.at ?? viewingRunId)
    : turnActive
      ? '실행 중'
      : '현재 대화'
  const inputsA = { task: firstMessage ?? '' }

  // 아직 안 끝난 노드는 스팬에 events가 없다 — live에서 가져온다
  const events = span ? (span.events ?? liveEvents[span.node_id] ?? []) : []
  const isLive = Boolean(span && span.status === 'running' && liveEvents[span.node_id])
  const showComposer = isOrch(span) && !viewingRunId

  const History = () =>
    pastRuns.length ? (
      <div className="border-b border-border">
        <button
          onClick={() => setShowHistory((v) => !v)}
          className="flex w-full items-center gap-1.5 px-3 py-1.5 text-[11px] text-muted hover:text-fg"
        >
          <Clock size={11} /> 지난 대화 {pastRuns.length}
        </button>
        {showHistory && (
          <div className="max-h-40 overflow-y-auto pb-1">
            {pastRuns.map((r) => (
              <div
                key={r.id}
                className="flex items-center border-l-2 hover:bg-panel-2"
                style={{ borderLeftColor: r.id === viewingRunId ? 'var(--color-accent)' : 'transparent' }}
              >
                <button
                  disabled={turnActive}
                  onClick={() => {
                    loadRun(r.id)
                    setShowHistory(false)
                  }}
                  className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1 text-left disabled:opacity-40"
                >
                  <span className="font-mono text-[10px] text-faint">{shortAt(r.at)}</span>
                  {r.cancelled && <span className="text-[9.5px] text-warn">중단</span>}
                  <span className="truncate text-[11px] text-muted">{r.summary || '—'}</span>
                  <span className="ml-auto shrink-0 font-mono text-[10px] text-faint">
                    {(r.duration_ms / 1000).toFixed(1)}s
                  </span>
                </button>
                <button
                  onClick={() => loadComparison(r.id)}
                  title={comparisonRun?.id === r.id ? '비교 해제' : 'B로 비교'}
                  className="p-1.5 text-faint hover:text-fg"
                >
                  <Columns2 size={11} />
                </button>
                <button
                  disabled={turnActive}
                  onClick={() => rerunRun(r.id)}
                  title="이 첫 메시지로 새 대화를 시작하고 이전 결과와 비교"
                  className="p-1.5 text-faint hover:text-fg disabled:opacity-30"
                >
                  <RefreshCw size={11} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    ) : null

  if (!spans.length) {
    return (
      <div className="flex h-full flex-col">
        <History />
        <div className="grid flex-1 place-items-center text-[12px] text-faint">
          {runError ? (
            <span className="text-danger">{runError}</span>
          ) : turnActive ? (
            <span className="flex items-center gap-2">
              <Loader2 size={13} className="animate-spin" /> 실행 중…
            </span>
          ) : (
            <span className="flex items-center gap-2">
              <CircleDashed size={13} /> 아래에 메시지를 보내 대화를 시작하세요
            </span>
          )}
        </div>
        <Composer />
      </div>
    )
  }

  return (
    <div className="flex h-full">
      {/* 스팬 목록 */}
      <div className="flex w-[300px] shrink-0 flex-col border-r border-border">
        <History />
        <div className="flex items-center gap-2 border-b border-border px-3 py-2 text-[11px] text-muted">
          <span
            className={
              runState === 'running'
                ? 'text-accent-fg'
                : runState === 'cancelled'
                  ? 'text-warn'
                  : runState === 'error'
                    ? 'text-danger'
                    : 'text-ok'
            }
          >
            {runState === 'running'
              ? '● 실행 중'
              : runState === 'cancelled'
                ? '● 중단됨'
                : runState === 'error'
                  ? '● 실패'
                  : runState === 'idle'
                    ? '● 대기 중'
                    : '● 성공'}
          </span>
          {viewingRunId && <span className="text-[10px] text-muted">과거 대화 보기</span>}
          <button
            disabled={turnActive || !firstMessage}
            onClick={rerun}
            title="같은 첫 메시지로 새 대화를 시작하고 이 결과를 B에 고정"
            className="ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 text-faint hover:bg-raised hover:text-fg disabled:opacity-30"
          >
            <RefreshCw size={10} /> 재실행
          </button>
          {comparisonRun && (
            <button
              onClick={clearComparison}
              title="비교 닫기"
              className="rounded p-0.5 text-warn hover:bg-raised"
            >
              <X size={11} />
            </button>
          )}
          <span className="font-mono">{(total / 1000).toFixed(2)}s</span>
        </div>
        {runError && (
          <div className="border-b border-danger/40 bg-danger/10 px-3 py-2 text-[11px] text-danger">
            {runError}
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto">
        {spans.map((s) => (
          <button
            key={s.id}
            onClick={() => selectSpan(s.id)}
            className="flex w-full items-center gap-2 border-l-2 px-3 py-2 text-left hover:bg-panel-2"
            style={{
              borderLeftColor: s.id === span?.id ? 'var(--color-accent)' : 'transparent',
              background: s.id === span?.id ? 'var(--color-panel-2)' : undefined
            }}
          >
            <StatusIcon s={s.status} />
            <span className="truncate text-[12px]">{s.label ?? s.node_id}</span>
            {s.usage ? (
              <span className="shrink-0 font-mono text-[9.5px] text-faint" title="프롬프트+생성 토큰">
                {s.usage.prompt_tokens}+{s.usage.completion_tokens}
              </span>
            ) : (s.events?.length || liveEvents[s.node_id]?.length) ? (
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
      </div>

      {comparisonRun ? (
        <div className="flex min-w-0 flex-1 divide-x divide-border">
          <ComparisonColumn
            label="A"
            at={primaryAt}
            span={span}
            inputs={inputsA}
            events={events}
            live={isLive}
          />
          <ComparisonColumn
            label="B"
            at={comparisonRun.at}
            span={comparisonSpan}
            inputs={comparisonRun.inputs}
            events={comparisonSpan?.events ?? []}
          />
        </div>
      ) : (
        <>
      {/* 선택 스팬 상세 — 오케스트레이터면 대화창, 워커면 로그 + Stop */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {span ? (
          <>
            <div className="mb-2 flex items-center gap-2">
              <StatusIcon s={span.status} />
              <span className="font-medium">{span.label ?? span.node_id}</span>
              {!isOrch(span) && span.status === 'running' && connected && (
                <button
                  onClick={() => stopWorker(span.node_id)}
                  title="이 워커만 중단"
                  className="flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px]"
                  style={{ borderColor: 'var(--color-danger)', color: 'var(--color-danger)' }}
                >
                  <X size={10} /> 중단
                </button>
              )}
              <span className="ml-auto font-mono text-[11px] text-muted">
                +{span.started_ms}ms · {span.duration_ms ?? '…'}ms
              </span>
            </div>
            {perfLine(span) && (
              <div className="mb-2 font-mono text-[10.5px] text-faint">{perfLine(span)}</div>
            )}
            {events.length ? (
              <SessionView events={events} live={isLive || (isOrch(span) && turnActive)} />
            ) : (
              <div className="rounded-md border border-border bg-panel-2 p-2">
                <div className="mb-1 text-[10px] tracking-wider text-faint">결과</div>
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
        {showComposer && <Composer />}
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
              {t === 'input' ? '입력' : '출력'}
            </button>
          ))}
        </div>
        <pre className="whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11px] leading-relaxed text-muted">
          {span ? pretty(io === 'input' ? span.input : span.output) : '—'}
        </pre>
      </div>
        </>
      )}
    </div>
  )
}
