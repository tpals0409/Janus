import { useCallback, useMemo } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeChange
} from '@xyflow/react'
import { Bot, Sparkles, Wrench, Send } from 'lucide-react'
import { useStore } from '../store'
import type { NodeType } from '../types'
import SpecNodeCard from './nodes/SpecNode'

const nodeTypes = { spec: SpecNodeCard }

/** position이 없는 노드는 순서대로 흩어 놓는다 (스펙에 좌표가 없어도 그려져야 한다) */
function fallbackPos(i: number) {
  return { x: 40 + i * 240, y: 160 }
}

/** 엣지 id에 인덱스를 심어두고 다시 꺼낸다 — 스펙의 엣지는 id가 없다 */
const edgeId = (i: number, from: string, to: string) => `e${i}::${from}::${to}`
const edgeIdx = (id: string) => Number(id.slice(1).split('::')[0])

const PALETTE: { type: NodeType; label: string; Icon: typeof Sparkles }[] = [
  { type: 'agent', label: 'Agent', Icon: Bot },
  { type: 'llm', label: 'LLM', Icon: Sparkles },
  { type: 'tool', label: 'Tool', Icon: Wrench },
  { type: 'end', label: 'End', Icon: Send }
]

export default function Canvas() {
  const spec = useStore((s) => s.spec)
  const selectedNodeId = useStore((s) => s.selectedNodeId)
  const selectedEdgeIdx = useStore((s) => s.selectedEdgeIdx)
  const spans = useStore((s) => s.spans)
  const selectNode = useStore((s) => s.selectNode)
  const selectEdge = useStore((s) => s.selectEdge)
  const setNodePosition = useStore((s) => s.setNodePosition)
  const addEdge = useStore((s) => s.addEdge)
  const addNode = useStore((s) => s.addNode)
  const deleteNode = useStore((s) => s.deleteNode)
  const deleteEdge = useStore((s) => s.deleteEdge)

  const nodes: Node[] = useMemo(() => {
    if (!spec) return []
    return spec.nodes.map((n, i) => {
      const span = spans.find((s) => s.node_id === n.id)
      return {
        id: n.id,
        type: 'spec',
        position: n.position ?? fallbackPos(i),
        selected: n.id === selectedNodeId,
        // start는 지우면 그래프가 성립하지 않는다
        deletable: n.type !== 'start',
        data: { node: n, running: span?.status === 'running', done: span?.status === 'success' }
      }
    })
  }, [spec, selectedNodeId, spans])

  const edges: Edge[] = useMemo(() => {
    if (!spec) return []
    return spec.edges.map((e, i) => {
      const on = i === selectedEdgeIdx
      return {
        id: edgeId(i, e.from, e.to),
        source: e.from,
        target: e.to,
        label: e.when,
        animated: Boolean(e.when),
        selected: on,
        labelStyle: { fill: on ? 'var(--color-accent-fg)' : 'var(--color-muted)', fontSize: 10 },
        labelBgStyle: { fill: 'var(--color-panel)' },
        style: {
          stroke: on ? 'var(--color-accent-fg)' : e.when ? 'var(--color-accent)' : '#4a4a63',
          strokeWidth: on ? 2.5 : 1.5
        }
      }
    })
  }, [spec, selectedEdgeIdx])

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      for (const c of changes) {
        // 드래그가 끝났을 때만 저장한다 — 매 프레임 스펙을 갈아엎지 않는다
        if (c.type === 'position' && !c.dragging && c.position) setNodePosition(c.id, c.position)
      }
    },
    [setNodePosition]
  )

  const onConnect = useCallback(
    (c: Connection) => {
      if (c.source && c.target) addEdge(c.source, c.target)
    },
    [addEdge]
  )

  // 여러 엣지를 한 번에 지우면 인덱스가 밀린다. 큰 인덱스부터 지운다.
  const onEdgesDelete = useCallback(
    (removed: Edge[]) => {
      removed
        .map((e) => edgeIdx(e.id))
        .sort((a, b) => b - a)
        .forEach(deleteEdge)
    },
    [deleteEdge]
  )

  if (!spec)
    return <div className="grid h-full place-items-center text-faint">에이전트를 선택하세요</div>

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onConnect={onConnect}
      onNodeClick={(_, n) => selectNode(n.id)}
      onEdgeClick={(_, e) => selectEdge(edgeIdx(e.id))}
      onNodesDelete={(ns) => ns.forEach((n) => deleteNode(n.id))}
      onEdgesDelete={onEdgesDelete}
      onPaneClick={() => {
        selectNode(null)
        selectEdge(null)
      }}
      deleteKeyCode={['Backspace', 'Delete']}
      fitView
      fitViewOptions={{ padding: 0.25 }}
      proOptions={{ hideAttribution: true }}
    >
      <Panel position="top-left" className="flex gap-1">
        {PALETTE.map(({ type, label, Icon }) => (
          <button
            key={type}
            onClick={() => addNode(type)}
            title={`${label} 노드 추가`}
            className="flex items-center gap-1.5 rounded-md border border-border-strong bg-panel-2 px-2 py-1 text-[11px] text-muted hover:text-fg"
          >
            <Icon size={12} /> + {label}
          </button>
        ))}
        <span className="self-center pl-1 text-[10px] text-faint">
          노드·엣지 선택 후 Delete로 삭제
        </span>
      </Panel>

      <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#ffffff10" />
      <Controls showInteractive={false} />
      <MiniMap
        pannable
        zoomable
        maskColor="#0b0b10cc"
        nodeColor={(n) => {
          const t = (n.data as any)?.node?.type
          return t === 'agent'
            ? 'var(--color-node-agent)'
            : t === 'llm'
              ? 'var(--color-node-llm)'
              : t === 'tool'
                ? 'var(--color-node-tool)'
                : 'var(--color-node-start)'
        }}
        style={{ background: 'var(--color-panel)', border: '1px solid var(--color-border-strong)' }}
      />
    </ReactFlow>
  )
}
