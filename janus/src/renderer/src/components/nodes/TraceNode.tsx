import { Handle, Position } from '@xyflow/react'
import { Bot, Network } from 'lucide-react'
import type { Span } from '../../types'

/** 트레이스 노드 카드 — 스팬(실행 사실)을 그린다. 설정 편집은 우측 Config 패널. */
export default function TraceNodeCard({
  data,
  selected
}: {
  data: { span: Span | null; title: string; isOrchestrator: boolean; pending?: boolean; pendingLabel?: string }
  selected?: boolean
}) {
  const s = data.span
  const color = data.isOrchestrator ? 'var(--text-primary)' : 'var(--text-secondary)'
  const Icon = data.isOrchestrator ? Network : Bot
  const running = s?.status === 'running'

  return (
    <div className="runtime-node" data-running={running} data-error={s?.status === 'error'} data-selected={selected}>
      {!data.isOrchestrator && <Handle type="target" position={Position.Left} />}

      <div className="flex items-center gap-2">
        <Icon size={13} style={{ color }} />
        <span className="max-w-[160px] truncate font-medium text-fg">{data.title}</span>
        {running && (
          <span
            className="ml-auto h-1.5 w-1.5 animate-pulse rounded-full"
            style={{ background: color }}
          />
        )}
        {s && !running && (
          <span
            className="ml-auto h-1.5 w-1.5 rounded-full"
            style={{
              background: s.status === 'error' ? 'var(--color-danger)' : 'var(--color-ok)'
            }}
          />
        )}
      </div>

      <div className="mt-1 space-y-0.5 text-[11px] text-muted">
        {data.pending && <div>{data.pendingLabel ?? '메시지를 보내면 시작합니다'}</div>}
        {s?.usage && (
          <div className="truncate font-mono text-[10px]">
            {s.usage.prompt_tokens}+{s.usage.completion_tokens} 토큰
          </div>
        )}
        {s?.duration_ms != null && <div className="font-mono text-[10px]">{(s.duration_ms / 1000).toFixed(1)}s</div>}
      </div>

      {data.isOrchestrator && <Handle type="source" position={Position.Right} />}
    </div>
  )
}
