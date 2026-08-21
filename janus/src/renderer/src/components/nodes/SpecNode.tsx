import { Handle, Position } from '@xyflow/react'
import { Bot, Play, Sparkles, Wrench, Send } from 'lucide-react'
import type { SpecNode as TNode } from '../../types'

const STYLE = {
  start: { color: 'var(--color-node-start)', Icon: Play },
  llm: { color: 'var(--color-node-llm)', Icon: Sparkles },
  tool: { color: 'var(--color-node-tool)', Icon: Wrench },
  agent: { color: 'var(--color-node-agent)', Icon: Bot },
  end: { color: 'var(--color-node-end)', Icon: Send }
} as const

/** 노드 아래에 흐리게 붙는 설명 줄 — 목업의 "gpt-4o / Generate response / Tone: Friendly" */
function details(n: TNode): string[] {
  switch (n.type) {
    case 'start':
      return (n.outputs ?? []).slice(0, 3)
    case 'llm':
      return [n.model?.name ?? 'no model', `temp ${n.model?.temperature ?? 0}`].filter(Boolean)
    case 'tool':
      return [n.tool ?? 'no tool', `→ ${n.output?.name ?? 'result'}`]
    case 'agent': {
      const t = n.tools ?? []
      return [
        n.model?.name ?? 'no model',
        t.length ? `${t.length} tools: ${t.slice(0, 2).join(', ')}${t.length > 2 ? '…' : ''}`
                 : 'no tools',
        n.approval === 'ask' ? '⚠ approval: ask' : ''
      ].filter(Boolean)
    }
    case 'end':
      return Object.keys(n.inputs ?? {}).slice(0, 3)
  }
}

export default function SpecNodeCard({
  data,
  selected
}: {
  data: { node: TNode; running?: boolean; done?: boolean }
  selected?: boolean
}) {
  const n = data.node
  const { color, Icon } = STYLE[n.type]

  return (
    <div
      className="min-w-[170px] rounded-lg border bg-panel-2 px-3 py-2.5 transition-shadow"
      style={{
        borderColor: selected ? 'var(--color-accent)' : 'var(--color-border-strong)',
        boxShadow: selected
          ? '0 0 0 3px var(--color-accent-soft)'
          : data.running
            ? `0 0 0 3px ${color}33`
            : '0 1px 2px #0006',
        background: 'var(--color-panel-2)'
      }}
    >
      {n.type !== 'start' && <Handle type="target" position={Position.Left} />}

      <div className="flex items-center gap-2">
        <Icon size={13} style={{ color }} />
        <span className="font-medium text-fg">{n.id}</span>
        {data.running && (
          <span
            className="ml-auto h-1.5 w-1.5 animate-pulse rounded-full"
            style={{ background: color }}
          />
        )}
        {data.done && !data.running && (
          <span className="ml-auto h-1.5 w-1.5 rounded-full" style={{ background: 'var(--color-ok)' }} />
        )}
      </div>

      <div className="mt-1 space-y-0.5">
        {details(n).map((d) => (
          <div key={d} className="truncate text-[11px] text-muted">
            {d}
          </div>
        ))}
      </div>

      {n.type !== 'end' && <Handle type="source" position={Position.Right} />}
    </div>
  )
}
