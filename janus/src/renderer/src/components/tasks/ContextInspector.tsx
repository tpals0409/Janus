import { Check, CircleGauge, Database, Layers3 } from 'lucide-react'
import type { AgentSessionDetail, SessionEvent } from '../../types'
import { EmptyState, Status } from '../ui'

function number(value: unknown): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

export default function ContextInspector({ session, events }: { session: AgentSessionDetail; events: SessionEvent[] }) {
  const context = session.context
  if (!context) {
    return <EmptyState title="컨텍스트 스냅샷 없음" description="이 세션에는 저장된 컨텍스트 스냅샷이 없습니다." />
  }

  const liveWindow = [...events].reverse().find((event) => (
    event.kind === 'agent_event' && event.payload.kind === 'context_window'
  ))?.payload
  const window = liveWindow ?? context.latest_window
  const sentTokens = number(window?.sent_token_estimate)
  const savedTokens = number(window?.saved_token_estimate)
  const limitTokens = Math.round(context.policy.max_chars / 4)
  const fill = Math.min(100, Math.round((sentTokens || context.estimated_static_tokens) / Math.max(1, limitTokens) * 100))

  return (
    <div className="workspace-surface min-h-0 overflow-y-auto p-4">
      <div className="grid grid-cols-[220px_minmax(0,1fr)] gap-4">
        <aside className="border border-border-subtle bg-panel p-4">
          <div className="flex items-center gap-2 text-[10px] font-semibold tracking-wider text-faint"><CircleGauge size={12} /> 컨텍스트 예산</div>
          <div className="mt-4 flex items-end gap-1 font-mono">
            <strong className="text-[24px] font-medium tracking-tight text-fg">{(sentTokens || context.estimated_static_tokens).toLocaleString()}</strong>
            <span className="pb-1 text-[9.5px] text-faint">/ {limitTokens.toLocaleString()} token</span>
          </div>
          <div className="context-meter mt-3"><span style={{ width: `${Math.max(2, fill)}%` }} /></div>
          <dl className="mt-4 space-y-2 border-t border-border pt-3 text-[9.5px]">
            <div className="flex justify-between"><dt className="text-faint">고정 예산</dt><dd className="font-mono text-muted">{context.estimated_static_tokens.toLocaleString()}</dd></div>
            <div className="flex justify-between"><dt className="text-faint">압축 절감</dt><dd className="font-mono text-ok">{savedTokens.toLocaleString()}</dd></div>
            <div className="flex justify-between"><dt className="text-faint">요약 크기</dt><dd className="font-mono text-muted">{number(window?.summary_chars).toLocaleString()}자</dd></div>
            <div className="flex justify-between"><dt className="text-faint">제외 블록</dt><dd className="font-mono text-muted">{number(window?.omitted_blocks)}</dd></div>
          </dl>
          <div className="mt-4 border border-border bg-base px-3 py-2 text-[9px] leading-relaxed text-faint">
            정책 · {context.policy.max_chars.toLocaleString()}자 · 최근 {context.policy.recent_blocks}블록 · 요약 {context.policy.summary_max_chars.toLocaleString()}자
          </div>
        </aside>

        <section className="overflow-hidden border border-border-subtle bg-panel">
          <header className="flex items-center gap-2 border-b border-border px-4 py-3">
            <Layers3 size={13} className="text-muted" />
            <div><h4 className="text-[11px] font-semibold">모델에 전달되는 조각</h4><p className="mt-0.5 text-[9px] text-faint">출처·포함 이유·예상 비용을 세션 snapshot 기준으로 표시합니다.</p></div>
            <span className="ml-auto font-mono text-[9px] text-faint">{context.items.filter((item) => item.status === 'included').length}/{context.items.length} INCLUDED</span>
          </header>
          <div className="max-h-[360px] overflow-y-auto px-4 py-2">
            {context.items.map((item, index) => (
              <details key={item.id} className="group relative border-b border-border py-2.5 last:border-b-0">
                {index < context.items.length - 1 && <span className="absolute left-[5px] top-6 h-[calc(100%-8px)] w-px bg-border-strong" />}
                <summary className="flex cursor-pointer list-none items-center gap-3">
                  <span className="relative z-10 grid h-[12px] w-[12px] shrink-0 place-items-center border border-border-strong bg-panel text-fg">
                    {item.status === 'included' && <Check size={8} strokeWidth={2} />}
                  </span>
                  <span className="min-w-0 flex-1"><strong className="block truncate text-[10.5px] font-medium text-fg">{item.label}</strong><span className="font-mono text-[8.5px] text-faint">{item.source}</span></span>
                  <Status tone="muted">{item.status === 'included' ? '포함' : '정책으로 제외'}</Status>
                  <span className="w-16 text-right font-mono text-[9px] text-muted">~{item.estimated_tokens.toLocaleString()} tok</span>
                </summary>
                <div className="ml-6 mt-2 border border-border bg-base px-3 py-2.5">
                  <pre className="max-h-36 overflow-auto whitespace-pre-wrap font-mono text-[9px] leading-relaxed text-muted">{item.content || '내용 없음'}</pre>
                  <div className="mt-2 flex items-center gap-1.5 border-t border-border pt-2 font-mono text-[8px] text-faint"><Database size={9} /> {item.chars.toLocaleString()}자 · {item.id}</div>
                </div>
              </details>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
