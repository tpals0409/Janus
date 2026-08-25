import { useMemo } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  Panel,
  ReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react'
import type { Span } from '../types'
import { ORCH_ID, useStore } from '../store'
import TraceNodeCard from './nodes/TraceNode'
import { Status } from './ui'

const nodeTypes = { trace: TraceNodeCard }

/** AgentProfile은 고정 루트, worker는 실제 Task 실행에서 생긴 span만 표시한다. */
export default function Canvas() {
  const profiles = useStore((state) => state.agentProfiles)
  const selectedProfileId = useStore((state) => state.selectedAgentProfileId)
  const session = useStore((state) => state.taskSession)
  const events = useStore((state) => state.taskSessionEvents)
  const profile = profiles.find((item) => item.id === selectedProfileId) ?? null
  const sessionMatches = Boolean(session && session.agent_profile_id === selectedProfileId)

  const spans = useMemo(() => {
    if (!sessionMatches) return []
    const byId = new Map<string, Span>()
    for (const event of events) {
      if (event.kind !== 'span_start' && event.kind !== 'span_end') continue
      const value = event.payload.span
      if (!value || typeof value !== 'object') continue
      const span = value as Span
      if (span.id && span.node_id) byId.set(span.id, span)
    }
    return [...byId.values()]
  }, [events, sessionMatches])

  const nodes: Node[] = useMemo(() => {
    if (!profile) return []
    const orchestrator = spans.find((span) => span.node_id === ORCH_ID) ?? null
    const workers = spans.filter((span) => span.node_id !== ORCH_ID)
    return [
      {
        id: ORCH_ID,
        type: 'trace',
        position: { x: 52, y: 64 + Math.max(0, workers.length - 1) * 55 },
        draggable: false,
        data: {
          span: orchestrator,
          title: profile.name,
          isOrchestrator: true,
          pending: !orchestrator,
          pendingLabel: '작업 실행 시 활성화',
        },
      },
      ...workers.map((worker, index) => ({
        id: worker.node_id,
        type: 'trace' as const,
        position: { x: 390, y: 64 + index * 110 },
        draggable: false,
        data: {
          span: worker,
          title: worker.label ?? worker.node_id,
          isOrchestrator: false,
        },
      })),
    ]
  }, [profile, spans])

  const edges: Edge[] = useMemo(() => nodes.slice(1).map((node) => {
    const span = node.data.span as Span
    return {
      id: `e-${node.id}`,
      source: ORCH_ID,
      target: node.id,
      animated: span.status === 'running',
      style: {
        stroke: span.status === 'error'
          ? 'var(--color-danger)'
          : span.status === 'running'
            ? 'var(--color-accent)'
            : 'var(--border-strong)',
        strokeWidth: 1.5,
      },
    }
  }), [nodes])

  if (!profile) {
    return <div className="grid h-full place-items-center text-[12px] text-faint">실행 프로필을 선택하세요.</div>
  }

  const workerCount = nodes.length - 1
  return (
    <section className="workspace-surface">
      <div className="min-h-0 flex-1">
      <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      nodesConnectable={false}
      nodesDraggable={false}
      elementsSelectable={false}
      deleteKeyCode={null}
      fitView
      fitViewOptions={{ padding: 0.28 }}
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--border-subtle)" />
      <Controls showInteractive={false} />
      <Panel position="top-left" className="graph-overlay m-4">
        <div className="flex items-center gap-2"><span className="font-mono text-[10px] tracking-wider text-faint">RUNTIME OWNERSHIP</span><Status tone={sessionMatches ? 'success' : 'muted'}>{sessionMatches ? '최근 실행' : '실행 전'}</Status></div>
        <div className="mt-1 text-[10px] text-muted">프로필 루트 1 · 실행 워커 {workerCount}</div>
      </Panel>
      <Panel position="top-right" className="graph-overlay m-4 max-w-[270px] text-[10px] leading-relaxed text-faint">
        워커는 영속 설정이 아닙니다. Task 실행 중 오케스트레이터가 생성·종료한 실제 span만 표시합니다.
        {session && !sessionMatches && <div className="mt-1.5 text-warn">최근 세션은 다른 프로필의 실행입니다.</div>}
      </Panel>
      </ReactFlow>
      </div>
    </section>
  )
}
