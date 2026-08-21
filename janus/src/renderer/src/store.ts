import { create } from 'zustand'
import type {
  AgentEvent, AgentSummary, ApprovalRequest, NodeType, RunSummary, Span, Spec, SpecNode, ToolInfo
} from './types'

const BASE = 'http://localhost:8765'

interface State {
  serverUp: boolean | null
  mlxUp: boolean | null
  workspace: string | null
  agents: AgentSummary[]
  tools: ToolInfo[]
  models: { name: string; provider: string }[]

  agentId: string | null
  spec: Spec | null
  yaml: string
  errors: string[]
  dirty: boolean

  selectedNodeId: string | null
  selectedEdgeIdx: number | null
  view: 'graph' | 'yaml'

  runInputs: Record<string, string>
  setRunInput(key: string, value: string): void
  syncRunFields(fields: string[]): void

  running: boolean
  spans: Span[]
  /** 아직 끝나지 않은 agent 노드의 세션 — span_end 전까지 여기서 자란다 */
  liveEvents: Record<string, AgentEvent[]>
  approval: ApprovalRequest | null
  /** 실행 중인 WS. 승인 응답과 취소를 여기로 보낸다. */
  ws: WebSocket | null
  selectedSpanId: string | null
  runError: string | null
  cancelled: boolean
  /** 지난 실행 기록 (C). viewingRunId가 있으면 spans는 과거 실행을 보는 중이다. */
  pastRuns: RunSummary[]
  viewingRunId: string | null

  boot(): Promise<void>
  pollHealth(): Promise<void>
  pickWorkspace(): Promise<void>
  openAgent(id: string): Promise<void>
  createAgent(name: string): Promise<void>
  deleteAgent(id: string): Promise<void>

  patchNode(nodeId: string, patch: Record<string, unknown>): void
  setNodePosition(nodeId: string, pos: { x: number; y: number }): void
  addNode(type: NodeType): void
  deleteNode(nodeId: string): void
  renameNode(oldId: string, newId: string): string | null

  addEdge(from: string, to: string): void
  deleteEdge(idx: number): void
  patchEdge(idx: number, patch: { when?: string | null }): void

  save(): Promise<void>
  selectNode(id: string | null): void
  selectEdge(idx: number | null): void
  setView(v: 'graph' | 'yaml'): void
  run(inputs: Record<string, string>): void
  cancel(): void
  respondApproval(approved: boolean): void
  loadRuns(): Promise<void>
  loadRun(runId: string): Promise<void>
  selectSpan(id: string): void
}

/** 얕은 병합이지만 model 같은 중첩 객체는 한 겹 더 병합한다. */
function mergeNode(node: any, patch: Record<string, unknown>) {
  const next = { ...node }
  for (const [k, v] of Object.entries(patch)) {
    if (v && typeof v === 'object' && !Array.isArray(v) && next[k] && typeof next[k] === 'object') {
      next[k] = { ...next[k], ...(v as object) }
    } else {
      next[k] = v
    }
  }
  return next
}

/** 노드 id를 바꾸면 {{ old.field }} 참조가 전부 따라와야 한다. 안 그러면 그래프가 깨진다. */
function rewriteRefs(spec: Spec, oldId: string, newId: string): Spec {
  const re = new RegExp(`\\{\\{\\s*${oldId}\\.(\\w+)\\s*\\}\\}`, 'g')
  const fix = (s: string) => s.replace(re, `{{ ${newId}.$1 }}`)

  return {
    ...spec,
    nodes: spec.nodes.map((n) => {
      const next: SpecNode = { ...n, id: n.id === oldId ? newId : n.id }
      if (n.inputs) {
        next.inputs = Object.fromEntries(Object.entries(n.inputs).map(([k, v]) => [k, fix(v)]))
      }
      if (n.prompt) next.prompt = fix(n.prompt)
      return next
    }),
    edges: spec.edges.map((e) => ({
      ...e,
      from: e.from === oldId ? newId : e.from,
      to: e.to === oldId ? newId : e.to
    }))
  }
}

function uniqueId(spec: Spec, base: string): string {
  if (!spec.nodes.some((n) => n.id === base)) return base
  let i = 2
  while (spec.nodes.some((n) => n.id === `${base}_${i}`)) i++
  return `${base}_${i}`
}

/** 새 노드는 검증을 통과하는 최소 형태로 만든다 — 만들자마자 빨간 줄이 뜨면 안 된다. */
function blankNode(spec: Spec, type: NodeType, models: { name: string }[], tools: ToolInfo[]) {
  const id = uniqueId(spec, type)
  const xs = spec.nodes.map((n) => n.position?.x ?? 0)
  const position = { x: Math.max(0, ...xs) + 240, y: 300 }

  if (type === 'llm') {
    return {
      id,
      type,
      position,
      model: { provider: 'local', name: models[0]?.name ?? '', temperature: 0, max_tokens: 200 },
      system_prompt: 'You are a helpful assistant.',
      inputs: {},
      output: { name: `${id}_out`, type: 'string' }
    } as SpecNode
  }
  if (type === 'agent') {
    return {
      id,
      type,
      position,
      model: { provider: 'local', name: models[0]?.name ?? '', temperature: 0.2, max_tokens: 400 },
      system_prompt: 'You are a helpful agent. Finish the task, then reply with your result.',
      tools: [],
      approval: 'auto',
      max_steps: 10,
      inputs: {},
      output: { name: `${id}_out`, type: 'string' }
    } as SpecNode
  }
  if (type === 'tool') {
    return {
      id,
      type,
      position,
      tool: tools[0]?.name ?? 'echo',
      inputs: {},
      output: { name: `${id}_out`, type: 'string' }
    } as SpecNode
  }
  return { id, type, position, inputs: {} } as SpecNode
}

export const useStore = create<State>((set, get) => ({
  serverUp: null,
  mlxUp: null,
  workspace: null,
  agents: [],
  tools: [],
  models: [],
  agentId: null,
  spec: null,
  yaml: '',
  errors: [],
  dirty: false,
  selectedNodeId: null,
  selectedEdgeIdx: null,
  view: 'graph',
  runInputs: {},
  running: false,
  spans: [],
  liveEvents: {},
  approval: null,
  ws: null,
  selectedSpanId: null,
  runError: null,
  cancelled: false,
  pastRuns: [],
  viewingRunId: null,

  async boot() {
    try {
      const [health, agents, tools, models, ws] = await Promise.all([
        fetch(`${BASE}/health`).then((r) => r.json()),
        fetch(`${BASE}/agents`).then((r) => r.json()),
        fetch(`${BASE}/tools`).then((r) => r.json()),
        fetch(`${BASE}/models`).then((r) => r.json()),
        fetch(`${BASE}/workspace`).then((r) => r.json())
      ])
      set({ serverUp: true, mlxUp: Boolean(health.mlx), agents, tools, models, workspace: ws.path })
      if (agents[0]) await get().openAgent(agents[0].id)
    } catch {
      set({ serverUp: false })
    }
  },

  /** 가벼운 상태 갱신 — 모델 서버가 늦게 뜨는 걸 상태바가 따라잡는다. */
  async pollHealth() {
    try {
      const h = await fetch(`${BASE}/health`).then((r) => r.json())
      set({ serverUp: true, mlxUp: Boolean(h.mlx) })
    } catch {
      set({ serverUp: false, mlxUp: null })
    }
  },

  async pickWorkspace() {
    // Electron 밖(브라우저)에서 열렸으면 다이얼로그가 없다 — 조용히 무시
    const picked = await window.janus?.pickFolder()
    if (!picked) return
    const r = await fetch(`${BASE}/workspace`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: picked })
    })
    if (r.ok) set({ workspace: (await r.json()).path })
  },

  async openAgent(id) {
    const r = await fetch(`${BASE}/agents/${id}`).then((x) => x.json())
    set({
      agentId: id,
      spec: r.spec,
      yaml: r.yaml,
      errors: r.errors ?? [],
      dirty: false,
      selectedNodeId: null,
      selectedEdgeIdx: null,
      runInputs: {},
      spans: [],
      liveEvents: {},
      approval: null,
      selectedSpanId: null,
      runError: null,
      pastRuns: [],
      viewingRunId: null
    })
    get().loadRuns()
  },

  async createAgent(name) {
    const r = await fetch(`${BASE}/agents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    }).then((x) => x.json())
    const agents = await fetch(`${BASE}/agents`).then((x) => x.json())
    set({ agents })
    await get().openAgent(r.id)
  },

  async deleteAgent(id) {
    await fetch(`${BASE}/agents/${id}`, { method: 'DELETE' })
    const agents = await fetch(`${BASE}/agents`).then((x) => x.json())
    set({ agents })
    if (get().agentId === id) {
      if (agents[0]) await get().openAgent(agents[0].id)
      else set({ agentId: null, spec: null, yaml: '', errors: [] })
    }
  },

  patchNode(nodeId, patch) {
    const spec = get().spec
    if (!spec) return
    set({
      spec: { ...spec, nodes: spec.nodes.map((n) => (n.id === nodeId ? mergeNode(n, patch) : n)) },
      dirty: true
    })
  },

  setNodePosition(nodeId, pos) {
    const spec = get().spec
    if (!spec) return
    set({
      spec: {
        ...spec,
        nodes: spec.nodes.map((n) => (n.id === nodeId ? { ...n, position: pos } : n))
      },
      dirty: true
    })
  },

  addNode(type) {
    const { spec, models, tools } = get()
    if (!spec) return
    const node = blankNode(spec, type, models, tools)
    set({ spec: { ...spec, nodes: [...spec.nodes, node] }, dirty: true, selectedNodeId: node.id })
  },

  deleteNode(nodeId) {
    const spec = get().spec
    if (!spec) return
    set({
      spec: {
        ...spec,
        nodes: spec.nodes.filter((n) => n.id !== nodeId),
        // 매달린 엣지를 남기면 저장이 거부된다 — 같이 지운다
        edges: spec.edges.filter((e) => e.from !== nodeId && e.to !== nodeId)
      },
      dirty: true,
      selectedNodeId: null
    })
  },

  renameNode(oldId, newId) {
    const spec = get().spec
    if (!spec || oldId === newId) return null
    if (!/^[A-Za-z_][\w-]*$/.test(newId)) return '영문자/밑줄로 시작하는 이름만 됩니다'
    if (spec.nodes.some((n) => n.id === newId)) return `이미 있는 이름입니다: ${newId}`
    set({ spec: rewriteRefs(spec, oldId, newId), dirty: true, selectedNodeId: newId })
    return null
  },

  addEdge(from, to) {
    const spec = get().spec
    if (!spec) return
    if (spec.edges.some((e) => e.from === from && e.to === to)) return
    set({ spec: { ...spec, edges: [...spec.edges, { from, to }] }, dirty: true })
  },

  deleteEdge(idx) {
    const spec = get().spec
    if (!spec) return
    set({
      spec: { ...spec, edges: spec.edges.filter((_, i) => i !== idx) },
      dirty: true,
      selectedEdgeIdx: null
    })
  },

  patchEdge(idx, patch) {
    const spec = get().spec
    if (!spec) return
    set({
      spec: {
        ...spec,
        edges: spec.edges.map((e, i) => {
          if (i !== idx) return e
          // when: null/'' 은 "조건 제거" 를 뜻한다 — 키 자체를 빼야 무조건 엣지가 된다
          if (patch.when === null || patch.when === '') {
            const { when: _drop, ...rest } = e
            return rest
          }
          return { ...e, when: patch.when }
        })
      },
      dirty: true
    })
  },

  async save() {
    const { agentId, spec } = get()
    if (!agentId || !spec) return
    const r = await fetch(`${BASE}/agents/${agentId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec })
    }).then((x) => x.json())
    // 저장 실패해도 편집 내용은 유지한다 — 고쳐야 하니까
    set({ errors: r.errors ?? [], dirty: !r.saved, yaml: r.yaml ?? get().yaml })
    if (r.saved) {
      set({ agents: await fetch(`${BASE}/agents`).then((x) => x.json()) })
    }
  },

  setRunInput(key, value) {
    set({ runInputs: { ...get().runInputs, [key]: value } })
  },

  /** start 노드의 outputs가 바뀌면 없어진 필드는 버리고 새 필드는 빈 값으로 만든다. */
  syncRunFields(fields) {
    const cur = get().runInputs
    const next = Object.fromEntries(fields.map((f) => [f, cur[f] ?? '']))
    if (JSON.stringify(next) !== JSON.stringify(cur)) set({ runInputs: next })
  },

  selectNode(id) {
    set({ selectedNodeId: id, selectedEdgeIdx: id ? null : get().selectedEdgeIdx })
  },

  selectEdge(idx) {
    set({ selectedEdgeIdx: idx, selectedNodeId: idx === null ? get().selectedNodeId : null })
  },

  setView(v) {
    set({ view: v })
  },

  run(inputs) {
    const { agentId, running } = get()
    if (!agentId || running) return
    set({ running: true, spans: [], liveEvents: {}, approval: null,
          selectedSpanId: null, runError: null, cancelled: false, viewingRunId: null })

    const ws = new WebSocket(`ws://localhost:8765/run/${agentId}`)
    set({ ws })
    ws.onopen = () => ws.send(JSON.stringify({ type: 'run', inputs }))

    ws.onmessage = (m) => {
      const ev = JSON.parse(m.data)
      if (ev.type === 'span_start') {
        set({ spans: [...get().spans, ev.span] })
      } else if (ev.type === 'span_end') {
        set({
          spans: get().spans.map((s) => (s.id === ev.span.id ? ev.span : s)),
          // 끝난 노드의 세션은 스팬이 들고 있으므로 live에서 뺀다
          liveEvents: Object.fromEntries(
            Object.entries(get().liveEvents).filter(([k]) => k !== ev.span.node_id)
          ),
          selectedSpanId: get().selectedSpanId ?? ev.span.id
        })
      } else if (ev.type === 'agent_event') {
        const cur = get().liveEvents[ev.node_id] ?? []
        set({ liveEvents: { ...get().liveEvents, [ev.node_id]: [...cur, ev] } })
      } else if (ev.type === 'token' && ev.node_id) {
        // llm 노드의 토큰 — agent 노드처럼 세션에 흘려 실시간 표시되게 한다
        const cur = get().liveEvents[ev.node_id] ?? []
        set({
          liveEvents: {
            ...get().liveEvents,
            [ev.node_id]: [...cur, { node_id: ev.node_id, kind: 'text_delta', text: ev.text, at_ms: 0 }]
          }
        })
      } else if (ev.type === 'approval_request') {
        set({ approval: ev })
      } else if (ev.type === 'run_error') {
        set({ runError: ev.error, running: false, approval: null })
        ws.close()
      } else if (ev.type === 'run_end') {
        set({ running: false, approval: null, cancelled: Boolean(ev.cancelled) })
        get().loadRuns()   // 방금 실행이 기록됐다 — 히스토리 갱신
        ws.close()
      }
    }
    ws.onerror = () => set({ runError: '서버에 연결할 수 없습니다', running: false })
    ws.onclose = () => set({ running: false, approval: null, ws: null })
  },

  cancel() {
    get().ws?.send(JSON.stringify({ type: 'cancel' }))
  },

  respondApproval(approved) {
    const { approval, ws } = get()
    if (!approval || !ws) return
    ws.send(JSON.stringify({ type: 'approval_response', id: approval.id, approved }))
    set({ approval: null })
  },

  async loadRuns() {
    const { agentId } = get()
    if (!agentId) return
    try {
      const runs = await fetch(`${BASE}/runs/${agentId}`).then((r) => r.json())
      set({ pastRuns: runs })
    } catch {
      /* 히스토리는 있으면 좋은 것 — 실패해도 조용히 */
    }
  },

  async loadRun(runId) {
    const { agentId } = get()
    if (!agentId) return
    const r = await fetch(`${BASE}/runs/${agentId}/${runId}`).then((x) => x.json())
    // 과거 실행을 스팬 뷰에 로드한다. running/live와 섞이지 않게 플래그를 세운다.
    set({
      spans: r.spans, liveEvents: {}, running: false, approval: null,
      cancelled: Boolean(r.cancelled), viewingRunId: runId,
      selectedSpanId: r.spans[0]?.id ?? null, runError: null
    })
  },

  selectSpan(id) {
    set({ selectedSpanId: id })
  }
}))
