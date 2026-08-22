import { create } from 'zustand'
import type {
  AgentEvent, AgentSummary, ApprovalRequest, BackendStatus, RunDetail, RunSummary, Span,
  Spec, ToolInfo, TreeEntry
} from './types'

const BASE = 'http://localhost:8765'
const TOKEN = window.janus?.authToken ?? ''
let openAgentSequence = 0

function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('X-Janus-Token', TOKEN)
  return fetch(input, { ...init, headers })
}

/** 상태 코드를 실어 나른다 — 인증 실패(401/403)와 연결 실패를 UI가 구분해야 한다. */
class ApiError extends Error {
  constructor(readonly status: number) {
    super(`Janus API ${status}`)
  }
}

async function apiJson(input: RequestInfo | URL, init: RequestInit = {}) {
  const response = await apiFetch(input, init)
  if (!response.ok) throw new ApiError(response.status)
  return response.json()
}

async function readBackendStatus(): Promise<BackendStatus | null> {
  try {
    return (await window.janus?.backendStatus()) ?? null
  } catch {
    return null
  }
}

export const ORCH_ID = 'orchestrator'

interface State {
  serverUp: boolean | null
  /** true면 서버는 살아 있는데 토큰/Origin이 거부됐다 (연결 실패와 다르다) */
  authFailed: boolean
  mlxUp: boolean | null
  backendStatus: BackendStatus | null
  workspace: string | null
  agents: AgentSummary[]
  tools: ToolInfo[]
  models: { name: string; provider: string }[]

  agentId: string | null
  spec: Spec | null
  yaml: string
  errors: string[]
  dirty: boolean

  view: 'graph' | 'yaml' | 'file'

  /** IDE성 상태 — 워크스페이스 파일 트리와 열어본 파일 */
  sidebarTab: 'config' | 'files'
  tree: Record<string, TreeEntry[]>
  openedFile: { path: string; content: string } | null
  recentFolders: string[]

  /** 하단 패널 탭 — 캔버스 클릭이 Traces로 끌어와야 해서 스토어에 있다 */
  bottomTab: 'traces' | 'logs' | 'metrics'

  /** WS가 열려 있고 대화가 살아 있다 */
  connected: boolean
  /** 오케스트레이터 턴이 도는 중 (컴포저 잠금) */
  turnActive: boolean
  /** 이 대화의 첫 메시지 — 재실행과 RUN INPUTS 표시의 기준 */
  firstMessage: string | null
  spans: Span[]
  /** 아직 끝나지 않은 노드의 세션 — span_end 전까지 여기서 자란다 */
  liveEvents: Record<string, AgentEvent[]>
  approvals: ApprovalRequest[]
  /** 대화 WS. 메시지·승인 응답·취소를 여기로 보낸다. */
  ws: WebSocket | null
  selectedSpanId: string | null
  runError: string | null
  cancelled: boolean
  /** 지난 실행 기록. viewingRunId가 있으면 spans는 과거 실행을 보는 중이다. */
  pastRuns: RunSummary[]
  viewingRunId: string | null
  /** A를 덮어쓰지 않고 오른쪽에 나란히 보여줄 B 실행. */
  comparisonRun: RunDetail | null

  boot(): Promise<void>
  pollHealth(): Promise<void>
  pickWorkspace(): Promise<void>
  setWorkspaceTo(path: string): Promise<void>
  setSidebarTab(t: 'config' | 'files'): void
  setBottomTab(t: 'traces' | 'logs' | 'metrics'): void
  loadDir(rel: string): Promise<void>
  refreshTree(): void
  openFile(rel: string): Promise<void>
  closeFile(): void
  openAgent(id: string, options?: { discardDirty?: boolean }): Promise<void>
  createAgent(name: string): Promise<void>
  deleteAgent(id: string): Promise<void>

  patchSpec(patch: Partial<Spec>): void
  save(): Promise<void>
  setView(v: 'graph' | 'yaml' | 'file'): void

  sendMessage(text: string): void
  stopTurn(): void
  stopWorker(nodeId: string): void
  endSession(): void
  respondApproval(id: string, approved: boolean): void

  loadRuns(): Promise<void>
  loadRun(runId: string): Promise<void>
  loadComparison(runId: string): Promise<void>
  clearComparison(): void
  rerun(): void
  rerunRun(runId: string): Promise<void>
  selectSpan(id: string | null): void
  /** 캔버스 노드 클릭 — 해당 노드의 스팬을 선택하고 Traces 패널을 연다 */
  selectNodeSpan(nodeId: string): void
}

function confirmDiscardChanges(nextName: string): boolean {
  return window.confirm(
    `저장되지 않은 변경이 있습니다. 변경을 버리고 ${nextName}(으)로 전환할까요?`
  )
}

export const useStore = create<State>((set, get) => ({
  serverUp: null,
  authFailed: false,
  mlxUp: null,
  backendStatus: null,
  workspace: null,
  agents: [],
  tools: [],
  models: [],
  agentId: null,
  spec: null,
  yaml: '',
  errors: [],
  dirty: false,
  view: 'graph',
  sidebarTab: 'config',
  tree: {},
  openedFile: null,
  recentFolders: JSON.parse(localStorage.getItem('janus.recentFolders') ?? '[]'),
  bottomTab: 'traces',
  connected: false,
  turnActive: false,
  firstMessage: null,
  spans: [],
  liveEvents: {},
  approvals: [],
  ws: null,
  selectedSpanId: null,
  runError: null,
  cancelled: false,
  pastRuns: [],
  viewingRunId: null,
  comparisonRun: null,

  async boot() {
    const currentAgentId = get().agentId
    const previousWorkspace = get().workspace
    try {
      const [health, agents, tools, models, ws, backendStatus] = await Promise.all([
        apiJson(`${BASE}/health`),
        apiJson(`${BASE}/agents`),
        apiJson(`${BASE}/tools`),
        apiJson(`${BASE}/models`),
        apiJson(`${BASE}/workspace`),
        readBackendStatus()
      ])
      const workspaceChanged = previousWorkspace !== null && previousWorkspace !== ws.path
      set({
        serverUp: true,
        authFailed: false,
        mlxUp: Boolean(health.mlx),
        backendStatus,
        agents,
        tools,
        models,
        workspace: ws.path,
        ...(workspaceChanged
          ? {
              tree: {},
              openedFile: null,
              view: get().view === 'file' ? ('graph' as const) : get().view
            }
          : {})
      })
      get().loadDir('')
      const currentStillExists = currentAgentId && agents.some((a: AgentSummary) => a.id === currentAgentId)
      if (currentStillExists) {
        // 백엔드 재시작이 로컬 미저장 편집을 날리면 복구가 또 다른 손실이 된다.
        if (!get().dirty) await get().openAgent(currentAgentId)
        else get().loadRuns()
      } else if (agents[0] && !get().dirty) {
        await get().openAgent(agents[0].id)
      }
    } catch (e) {
      // 401/403은 서버가 살아 있다는 뜻이다 — "백엔드 시작 중"으로 숨기면 거짓말이 된다.
      const authFailed = e instanceof ApiError && (e.status === 401 || e.status === 403)
      set({
        serverUp: false,
        authFailed,
        mlxUp: null,
        backendStatus: await readBackendStatus()
      })
    }
  },

  /** 가벼운 상태 갱신 — 모델 서버가 늦게 뜨는 걸 상태바가 따라잡는다. */
  async pollHealth() {
    const status = readBackendStatus()
    try {
      const h = await apiJson(`${BASE}/health`)
      set({ serverUp: true, authFailed: false, mlxUp: Boolean(h.mlx), backendStatus: await status })
    } catch (e) {
      const authFailed = e instanceof ApiError && (e.status === 401 || e.status === 403)
      set({ serverUp: false, authFailed, mlxUp: null, backendStatus: await status })
    }
  },

  async pickWorkspace() {
    // Electron 밖(브라우저)에서 열렸으면 다이얼로그가 없다 — 조용히 무시
    const picked = await window.janus?.pickFolder()
    if (picked) await get().setWorkspaceTo(picked)
  },

  async setWorkspaceTo(path) {
    const r = await apiFetch(`${BASE}/workspace`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path })
    })
    if (!r.ok) return
    const real = (await r.json()).path
    const recents = [real, ...get().recentFolders.filter((f) => f !== real)].slice(0, 6)
    localStorage.setItem('janus.recentFolders', JSON.stringify(recents))
    // 워크스페이스가 바뀌면 이전 트리·열린 파일은 전부 무효다
    set({ workspace: real, recentFolders: recents, tree: {}, openedFile: null })
    if (get().view === 'file') set({ view: 'graph' })
    get().loadDir('')
  },

  setSidebarTab(t) {
    set({ sidebarTab: t })
  },

  setBottomTab(t) {
    set({ bottomTab: t })
  },

  async loadDir(rel) {
    try {
      const r = await apiFetch(`${BASE}/workspace/tree?path=${encodeURIComponent(rel)}`)
      if (!r.ok) return
      const d = await r.json()
      set({ tree: { ...get().tree, [rel]: d.entries } })
    } catch {
      /* 트리는 보조 기능 — 실패해도 조용히 */
    }
  },

  /** 로드했던 디렉토리를 전부 다시 읽는다 — 에이전트가 파일을 만든 뒤 호출. */
  refreshTree() {
    const keys = Object.keys(get().tree)
    for (const k of keys.length ? keys : ['']) get().loadDir(k)
  },

  async openFile(rel) {
    const r = await apiFetch(`${BASE}/workspace/file?path=${encodeURIComponent(rel)}`)
    if (!r.ok) return
    const d = await r.json()
    if (d.error) {
      set({ openedFile: { path: rel, content: `(${d.error})` }, view: 'file' })
      return
    }
    set({ openedFile: { path: rel, content: d.content }, view: 'file' })
  },

  closeFile() {
    set({ openedFile: null, view: 'graph' })
  },

  async openAgent(id, options) {
    const current = get().agentId
    if (id === current) return
    if (get().dirty && !options?.discardDirty) {
      const name = get().agents.find((agent) => agent.id === id)?.name ?? id
      if (!confirmDiscardChanges(name)) return
    }
    const sequence = ++openAgentSequence
    // 대화가 살아 있으면 끊는다 — 서버 finally가 워커까지 정리하고 저장한다
    get().ws?.close()
    set({ ws: null, connected: false, turnActive: false, approvals: [] })
    const r = await apiJson(`${BASE}/agents/${id}`)
    if (sequence !== openAgentSequence) return
    set({
      agentId: id,
      spec: r.spec,
      yaml: r.yaml,
      errors: r.errors ?? [],
      dirty: false,
      view: r.spec ? get().view : 'yaml',
      firstMessage: null,
      spans: [],
      liveEvents: {},
      approvals: [],
      selectedSpanId: null,
      runError: null,
      cancelled: false,
      pastRuns: [],
      viewingRunId: null,
      comparisonRun: null
    })
    get().loadRuns()
  },

  async createAgent(name) {
    if (get().dirty && !confirmDiscardChanges(`새 에이전트 “${name}”`)) return
    const r = await apiFetch(`${BASE}/agents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    }).then((x) => x.json())
    const agents = await apiFetch(`${BASE}/agents`).then((x) => x.json())
    set({ agents })
    await get().openAgent(r.id, { discardDirty: true })
  },

  async deleteAgent(id) {
    await apiFetch(`${BASE}/agents/${id}`, { method: 'DELETE' })
    const agents = await apiFetch(`${BASE}/agents`).then((x) => x.json())
    set({ agents })
    if (get().agentId === id) {
      if (agents[0]) await get().openAgent(agents[0].id, { discardDirty: true })
      else set({ agentId: null, spec: null, yaml: '', errors: [], dirty: false })
    }
  },

  patchSpec(patch) {
    const spec = get().spec
    if (!spec) return
    set({ spec: { ...spec, ...patch }, dirty: true })
  },

  async save() {
    const { agentId, spec } = get()
    if (!agentId || !spec) return
    const r = await apiFetch(`${BASE}/agents/${agentId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ spec })
    }).then((x) => x.json())
    // 저장 실패해도 편집 내용은 유지한다 — 고쳐야 하니까
    set({ errors: r.errors ?? [], dirty: !r.saved, yaml: r.yaml ?? get().yaml })
    if (r.saved) {
      set({ agents: await apiFetch(`${BASE}/agents`).then((x) => x.json()) })
    }
  },

  setView(v) {
    set({ view: v })
  },

  sendMessage(text) {
    const { agentId, errors, turnActive, ws } = get()
    const trimmed = text.trim()
    if (!agentId || errors.length > 0 || turnActive || !trimmed) return

    if (ws && ws.readyState === WebSocket.OPEN) {
      // 이어지는 턴 — 같은 세션
      ws.send(JSON.stringify({ type: 'message', text: trimmed }))
      set({ turnActive: true, cancelled: false, runError: null })
      return
    }

    // 새 대화 — 이전 스팬을 비우고 소켓을 연다. 첫 메시지가 곧 실행 시작이다.
    set({
      spans: [], liveEvents: {}, approvals: [], selectedSpanId: null,
      runError: null, cancelled: false, viewingRunId: null,
      turnActive: true, firstMessage: trimmed
    })
    const sock = new WebSocket(`ws://localhost:8765/run/${agentId}`, ['janus', TOKEN])
    set({ ws: sock, connected: false })

    sock.onopen = () => {
      if (get().ws !== sock) return
      set({ connected: true })
      sock.send(JSON.stringify({ type: 'message', text: trimmed }))
    }

    sock.onmessage = (m) => {
      if (get().ws !== sock) return
      const ev = JSON.parse(m.data)
      if (ev.type === 'span_start') {
        set({
          spans: [...get().spans, ev.span],
          // 오케스트레이터 스팬이 기본 선택 — 클릭 없이도 대화가 보인다
          selectedSpanId: get().selectedSpanId ?? ev.span.id
        })
      } else if (ev.type === 'span_end') {
        set({
          spans: get().spans.map((s) => (s.id === ev.span.id ? ev.span : s)),
          // 끝난 노드의 세션은 스팬이 들고 있으므로 live에서 뺀다
          liveEvents: Object.fromEntries(
            Object.entries(get().liveEvents).filter(([k]) => k !== ev.span.node_id)
          )
        })
      } else if (ev.type === 'agent_event') {
        const cur = get().liveEvents[ev.node_id] ?? []
        set({ liveEvents: { ...get().liveEvents, [ev.node_id]: [...cur, ev] } })
      } else if (ev.type === 'approval_request') {
        if (!get().approvals.some((request) => request.id === ev.id)) {
          set({ approvals: [...get().approvals, ev] })
        }
      } else if (ev.type === 'run_error') {
        set({ runError: ev.error, turnActive: false, approvals: [] })
      } else if (ev.type === 'turn_end') {
        set({ turnActive: false, approvals: [] })
        get().loadRuns()    // 서버가 저장을 마친 뒤 turn_end를 보낸다
        get().refreshTree() // 에이전트가 파일을 만들었을 수 있다
      }
    }
    sock.onerror = () => {
      if (get().ws === sock) set({ runError: '서버에 연결할 수 없습니다', turnActive: false })
    }
    sock.onclose = () => {
      if (get().ws !== sock) return
      set({
        connected: false,
        turnActive: false,
        approvals: [],
        ws: null,
        // 대화가 끝났다 — 아직 running인 스팬을 정리해 영원한 스피너를 막는다
        spans: get().spans.map((s) =>
          s.status === 'running' ? { ...s, status: get().cancelled ? 'error' : 'success' } : s
        )
      })
    }
  },

  stopTurn() {
    get().ws?.send(JSON.stringify({ type: 'cancel' }))
    set({ approvals: [], cancelled: true })
  },

  stopWorker(nodeId) {
    const ws = get().ws
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'stop_worker', node_id: nodeId }))
    }
  },

  endSession() {
    get().ws?.close()
  },

  respondApproval(id, approved) {
    const { approvals, ws } = get()
    if (!approvals.some((request) => request.id === id) || !ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify({ type: 'approval_response', id, approved }))
    set({ approvals: approvals.filter((request) => request.id !== id) })
  },

  async loadRuns() {
    const { agentId } = get()
    if (!agentId) return
    try {
      const runs = await apiFetch(`${BASE}/runs/${agentId}`).then((r) => r.json())
      if (get().agentId === agentId) set({ pastRuns: runs })
    } catch {
      /* 히스토리는 있으면 좋은 것 — 실패해도 조용히 */
    }
  },

  async loadRun(runId) {
    const { agentId, turnActive } = get()
    if (!agentId || turnActive) return
    get().ws?.close() // 과거 실행을 보는 건 현재 대화를 닫는다는 뜻이다
    const r = (await apiJson(`${BASE}/runs/${agentId}/${runId}`)) as RunDetail
    set({
      spans: r.spans, liveEvents: {}, approvals: [],
      cancelled: Boolean(r.cancelled), viewingRunId: runId,
      selectedSpanId: r.spans[0]?.id ?? null, runError: null,
      firstMessage: r.inputs?.task ?? null
    })
  },

  async loadComparison(runId) {
    const { agentId, comparisonRun } = get()
    if (!agentId) return
    if (comparisonRun?.id === runId) {
      set({ comparisonRun: null })
      return
    }
    const run = (await apiJson(`${BASE}/runs/${agentId}/${runId}`)) as RunDetail
    set({ comparisonRun: run })
  },

  clearComparison() {
    set({ comparisonRun: null })
  },

  /** 현재 스팬을 B에 고정하고, 같은 첫 메시지로 새 대화를 시작한다. */
  rerun() {
    const state = get()
    if (state.turnActive || !state.agentId || !state.firstMessage) return
    const source = state.pastRuns.find((r) => r.id === state.viewingRunId)
    if (state.spans.length) {
      const duration = state.spans.reduce(
        (max, span) => Math.max(max, span.started_ms + (span.duration_ms ?? 0)),
        0
      )
      set({
        comparisonRun: {
          id: state.viewingRunId ?? 'previous',
          at: source?.at ?? '방금 전',
          cancelled: state.cancelled,
          duration_ms: duration,
          node_count: state.spans.length,
          summary: source?.summary ?? '',
          inputs: { task: state.firstMessage },
          spans: state.spans
        }
      })
    }
    state.ws?.close()
    set({ ws: null, connected: false })
    get().sendMessage(state.firstMessage)
  },

  async rerunRun(runId) {
    const { agentId, turnActive } = get()
    if (!agentId || turnActive) return
    const run = (await apiJson(`${BASE}/runs/${agentId}/${runId}`)) as RunDetail
    const first = run.inputs?.task
    if (!first) return
    set({ comparisonRun: run })
    get().ws?.close()
    set({ ws: null, connected: false })
    get().sendMessage(first)
  },

  selectSpan(id) {
    set({ selectedSpanId: id })
  },

  selectNodeSpan(nodeId) {
    const span = get().spans.find((s) => s.node_id === nodeId)
    set({ selectedSpanId: span?.id ?? null, bottomTab: 'traces' })
  }
}))
