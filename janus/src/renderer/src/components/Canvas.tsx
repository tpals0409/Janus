import { useMemo } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  type Edge,
  type Node
} from '@xyflow/react'
import { ORCH_ID, useStore } from '../store'
import TraceNodeCard from './nodes/TraceNode'

const nodeTypes = { trace: TraceNodeCard }

/**
 * 트레이스 뷰어 — 노드는 스펙이 아니라 **스팬**에서 나온다.
 * 오케스트레이터가 create_worker로 만든 워커가 실행 중에 여기 나타난다.
 * 그래프는 입력이 아니라 출력이다.
 */
export default function Canvas() {
  const spec = useStore((s) => s.spec)
  const errors = useStore((s) => s.errors)
  const spans = useStore((s) => s.spans)
  const selectedSpanId = useStore((s) => s.selectedSpanId)
  const selectNodeSpan = useStore((s) => s.selectNodeSpan)

  const nodes: Node[] = useMemo(() => {
    if (!spec) return []
    const orch = spans.find((s) => s.node_id === ORCH_ID) ?? null
    const workers = spans.filter((s) => s.node_id !== ORCH_ID)
    // ponytail: 고정 2열 레이아웃 — 오케스트레이터 좌측, 워커는 스폰 순 우측 열.
    // 워커가 ~8개를 넘어 겹치면 dagre 자동 배치로 교체.
    return [
      {
        id: ORCH_ID,
        type: 'trace',
        position: { x: 40, y: 40 + Math.max(0, workers.length - 1) * 55 },
        selected: orch ? orch.id === selectedSpanId : selectedSpanId === null,
        draggable: true,
        data: { span: orch, title: spec.name, isOrchestrator: true, pending: !orch }
      },
      ...workers.map((w, i) => ({
        id: w.node_id,
        type: 'trace' as const,
        position: { x: 360, y: 40 + i * 110 },
        selected: w.id === selectedSpanId,
        draggable: true,
        data: { span: w, title: w.label ?? w.node_id, isOrchestrator: false }
      }))
    ]
  }, [spec, spans, selectedSpanId])

  const edges: Edge[] = useMemo(
    () =>
      spans
        .filter((s) => s.node_id !== ORCH_ID)
        .map((w) => ({
          id: `e-${w.node_id}`,
          source: ORCH_ID,
          target: w.node_id,
          animated: w.status === 'running',
          style: {
            stroke:
              w.status === 'error'
                ? 'var(--color-danger)'
                : w.status === 'running'
                  ? 'var(--color-accent)'
                  : '#4a4a63',
            strokeWidth: 1.5
          }
        })),
    [spans]
  )

  if (!spec) {
    return (
      <div className="grid h-full place-items-center px-8 text-center text-faint">
        <div>
          <div>{errors.length ? '스펙을 열 수 없습니다' : '에이전트를 선택하세요'}</div>
          {errors.length > 0 && (
            <pre className="mt-2 max-w-[720px] whitespace-pre-wrap font-mono text-[11px] text-danger">
              {errors.join('\n')}
            </pre>
          )}
        </div>
      </div>
    )
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_, n) => selectNodeSpan(n.id)}
      nodesConnectable={false}
      deleteKeyCode={null}
      fitView
      fitViewOptions={{ padding: 0.25 }}
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#ffffff10" />
      <Controls showInteractive={false} />
    </ReactFlow>
  )
}
