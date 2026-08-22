import { create } from 'zustand'
import type {
  AgentEvent, AgentProfile, AgentSessionDetail, AgentSummary, ApprovalRequest, ChangeSet,
  BackendStatus, ModelProfile, Project, RunDetail, RunSummary, SessionEvent, Span,
  ReviewSnapshot, ShipHandoff, Spec, Task, TaskShipment, ToolInfo, TreeEntry,
  VerificationCommand, VerificationRun,
  WorkspaceInspection
} from './types'

const BASE = import.meta.env.VITE_JANUS_BASE ?? 'http://localhost:8765'
const TOKEN = window.janus?.authToken ?? import.meta.env.VITE_JANUS_TOKEN ?? ''

function websocketUrl(path: string): string {
  const url = new URL(path, BASE)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}
let openAgentSequence = 0
let openProjectSequence = 0
let openTaskSequence = 0

function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('X-Janus-Token', TOKEN)
  return fetch(input, { ...init, headers })
}

/** 상태 코드를 실어 나른다 — 인증 실패(401/403)와 연결 실패를 UI가 구분해야 한다. */
class ApiError extends Error {
  constructor(readonly status: number, detail?: string) {
    super(detail || `Janus API ${status}`)
  }
}

async function apiJson(input: RequestInfo | URL, init: RequestInit = {}) {
  const response = await apiFetch(input, init)
  if (!response.ok) {
    let detail = ''
    try {
      detail = String((await response.clone().json()).detail ?? '')
    } catch {
      detail = await response.text()
    }
    throw new ApiError(response.status, detail)
  }
  return response.json()
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
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
  projects: Project[]
  tasks: Task[]
  projectId: string | null
  taskId: string | null
  task: Task | null
  agentProfiles: AgentProfile[]
  modelProfiles: ModelProfile[]
  workspaceInspection: WorkspaceInspection | null
  changeSet: ChangeSet | null
  verificationRuns: VerificationRun[]
  verificationBusy: boolean
  review: ReviewSnapshot | null
  shipments: TaskShipment[]
  shipHandoff: ShipHandoff | null
  taskBusy: boolean
  taskActionError: string | null
  taskSession: AgentSessionDetail | null
  taskSessionEvents: SessionEvent[]
  selectedAgentProfileId: string
  taskWs: WebSocket | null
  taskConnected: boolean
  taskTurnActive: boolean
  taskRuntimeError: string | null
  taskApprovals: ApprovalRequest[]

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
  loadProjects(): Promise<void>
  selectProject(id: string): Promise<void>
  selectTask(id: string): Promise<void>
  refreshSelectedTask(): Promise<void>
  addProjectFromPicker(): Promise<void>
  createTask(input: {
    title: string
    objective: string
    acceptance_command: string
    base_ref: string
  }): Promise<void>
  updateTask(patch: Partial<Pick<Task, 'title' | 'objective' | 'acceptance_command' | 'base_ref'>>): Promise<void>
  prepareWorkspace(): Promise<void>
  retryWorkspace(): Promise<void>
  inspectWorkspace(): Promise<void>
  loadVerifications(): Promise<void>
  setProjectVerificationCommands(commands: VerificationCommand[]): Promise<void>
  runVerifications(): Promise<void>
  rerunVerification(id: string): Promise<void>
  loadReview(): Promise<void>
  addReviewComment(input: {
    revision: string; layer: string; file_path: string; old_line: number | null
    new_line: number | null; hunk_header: string | null; body: string
  }): Promise<void>
  resolveReviewComment(id: string, resolved: boolean): Promise<void>
  decideReview(input: {
    decision: 'accept' | 'request_changes' | 'discard'; message?: string
    confirm_workspace_id?: string; confirm_discard?: string
  }): Promise<void>
  loadShipments(): Promise<void>
  commitTask(message: string): Promise<void>
  pushTask(remote?: string): Promise<void>
  loadShipHandoff(): Promise<void>
  archiveWorkspace(force?: boolean): Promise<void>
  deleteWorkspaceBranch(): Promise<void>
  archiveSelectedTask(): Promise<void>
  clearTaskError(): void
  loadLatestTaskSession(): Promise<void>
  selectAgentProfile(id: string): void
  startTaskSession(options?: { priority?: number; queue_timeout_ms?: number }): Promise<void>
  resumeTaskSession(): Promise<void>
  connectTaskSession(session: AgentSessionDetail): void
  sendTaskMessage(text: string): void
  cancelTaskTurn(): void
  stopTaskSession(): Promise<void>
  respondTaskApproval(id: string, approved: boolean): void
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
  projects: [],
  tasks: [],
  projectId: null,
  taskId: null,
  task: null,
  agentProfiles: [],
  modelProfiles: [],
  workspaceInspection: null,
  changeSet: null,
  verificationRuns: [],
  verificationBusy: false,
  review: null,
  shipments: [],
  shipHandoff: null,
  taskBusy: false,
  taskActionError: null,
  taskSession: null,
  taskSessionEvents: [],
  selectedAgentProfileId: localStorage.getItem('janus.agentProfile') ?? 'agent_default',
  taskWs: null,
  taskConnected: false,
  taskTurnActive: false,
  taskRuntimeError: null,
  taskApprovals: [],
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
      const [health, agents, tools, models, ws, backendStatus, projects, agentProfiles, modelProfiles] = await Promise.all([
        apiJson(`${BASE}/health`),
        apiJson(`${BASE}/agents`),
        apiJson(`${BASE}/tools`),
        apiJson(`${BASE}/models`),
        apiJson(`${BASE}/workspace`),
        readBackendStatus(),
        apiJson(`${BASE}/projects`),
        apiJson(`${BASE}/profiles/agents`),
        apiJson(`${BASE}/profiles/models`)
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
        projects,
        agentProfiles,
        modelProfiles,
        selectedAgentProfileId:
          agentProfiles.some((profile: AgentProfile) => profile.id === get().selectedAgentProfileId)
            ? get().selectedAgentProfileId
            : agentProfiles[0]?.id ?? 'agent_default',
        workspace: ws.path,
        ...(workspaceChanged
          ? {
              tree: {},
              openedFile: null,
              view: get().view === 'file' ? ('graph' as const) : get().view
            }
          : {})
      })
      const selectedProject =
        projects.find((project: Project) => project.id === get().projectId) ?? projects[0]
      if (selectedProject) await get().selectProject(selectedProject.id)
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

  async loadProjects() {
    const projects = (await apiJson(`${BASE}/projects`)) as Project[]
    set({ projects })
    const selected = projects.find((project) => project.id === get().projectId) ?? projects[0]
    if (selected) await get().selectProject(selected.id)
    else set({ projectId: null, tasks: [], taskId: null, task: null })
  },

  async selectProject(id) {
    const sequence = ++openProjectSequence
    get().taskWs?.close()
    set({
      projectId: id,
      tasks: [],
      taskId: null,
      task: null,
      taskSession: null,
      taskSessionEvents: [],
      taskWs: null,
      taskConnected: false,
      taskTurnActive: false,
      taskRuntimeError: null,
      taskApprovals: [],
      workspaceInspection: null,
      changeSet: null,
      verificationRuns: [],
      review: null,
      shipments: [],
      shipHandoff: null,
      taskActionError: null
    })
    try {
      const tasks = (await apiJson(`${BASE}/projects/${id}/tasks`)) as Task[]
      if (sequence !== openProjectSequence) return
      set({ tasks })
      const selected = tasks[0]
      if (selected) await get().selectTask(selected.id)
    } catch (error) {
      if (sequence === openProjectSequence) set({ taskActionError: errorMessage(error) })
    }
  },

  async selectTask(id) {
    const sequence = ++openTaskSequence
    get().taskWs?.close()
    set({
      taskId: id,
      task: null,
      workspaceInspection: null,
      changeSet: null,
      verificationRuns: [],
      review: null,
      shipments: [],
      shipHandoff: null,
      taskActionError: null,
      taskSession: null,
      taskSessionEvents: [],
      taskWs: null,
      taskConnected: false,
      taskTurnActive: false,
      taskRuntimeError: null,
      taskApprovals: []
    })
    try {
      const task = (await apiJson(`${BASE}/tasks/${id}`)) as Task
      if (sequence !== openTaskSequence) return
      set({ task })
      await get().loadLatestTaskSession()
      await get().loadVerifications()
      if (task.workspace?.state === 'ready') await get().loadReview()
      if (task.workspace?.state === 'ready') await get().loadShipments()
      if (task.workspace?.state === 'ready') await get().inspectWorkspace()
    } catch (error) {
      if (sequence === openTaskSequence) set({ taskActionError: errorMessage(error) })
    }
  },

  async refreshSelectedTask() {
    const { taskId, projectId } = get()
    if (!taskId || !projectId) return
    try {
      const [task, tasks] = await Promise.all([
        apiJson(`${BASE}/tasks/${taskId}`) as Promise<Task>,
        apiJson(`${BASE}/projects/${projectId}/tasks`) as Promise<Task[]>
      ])
      if (get().taskId !== taskId) return
      set({ task, tasks })
      if (task.workspace?.state === 'ready') await get().inspectWorkspace()
    } catch (error) {
      if (get().taskId === taskId) set({ taskActionError: errorMessage(error) })
    }
  },

  async addProjectFromPicker() {
    const picked = await window.janus?.pickFolder()
    if (!picked) return
    set({ taskBusy: true, taskActionError: null })
    try {
      const name = picked.split(/[\\/]/).filter(Boolean).pop() ?? 'Project'
      const project = (await apiJson(`${BASE}/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, repo_path: picked })
      })) as Project
      const projects = (await apiJson(`${BASE}/projects`)) as Project[]
      set({ projects })
      await get().selectProject(project.id)
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async createTask(input) {
    const projectId = get().projectId
    if (!projectId) return
    set({ taskBusy: true, taskActionError: null })
    try {
      const task = (await apiJson(`${BASE}/projects/${projectId}/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input)
      })) as Task
      const tasks = (await apiJson(`${BASE}/projects/${projectId}/tasks`)) as Task[]
      set({ tasks })
      await get().selectTask(task.id)
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async updateTask(patch) {
    const taskId = get().taskId
    if (!taskId) return
    set({ taskBusy: true, taskActionError: null })
    try {
      await apiJson(`${BASE}/tasks/${taskId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch)
      })
      await get().refreshSelectedTask()
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async prepareWorkspace() {
    const taskId = get().taskId
    if (!taskId) return
    set({ taskBusy: true, taskActionError: null })
    try {
      await apiJson(`${BASE}/tasks/${taskId}/workspace/prepare`, { method: 'POST' })
      await get().refreshSelectedTask()
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async retryWorkspace() {
    const taskId = get().taskId
    if (!taskId) return
    set({ taskBusy: true, taskActionError: null })
    try {
      await apiJson(`${BASE}/tasks/${taskId}/workspace/retry`, { method: 'POST' })
      await get().refreshSelectedTask()
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async inspectWorkspace() {
    const taskId = get().taskId
    if (!taskId) return
    try {
      const [inspection, changeSet] = await Promise.all([
        apiJson(`${BASE}/tasks/${taskId}/workspace/status`) as Promise<WorkspaceInspection>,
        apiJson(`${BASE}/tasks/${taskId}/changeset`) as Promise<ChangeSet>
      ])
      if (get().taskId === taskId) set({ workspaceInspection: inspection, changeSet })
      if (get().taskId === taskId) await get().loadReview()
    } catch (error) {
      if (get().taskId === taskId) set({ taskActionError: errorMessage(error) })
    }
  },

  async loadVerifications() {
    const taskId = get().taskId
    if (!taskId) return
    try {
      const runs = await apiJson(`${BASE}/tasks/${taskId}/verifications`) as VerificationRun[]
      if (get().taskId === taskId) set({ verificationRuns: runs })
    } catch (error) {
      if (get().taskId === taskId) set({ taskActionError: errorMessage(error) })
    }
  },

  async setProjectVerificationCommands(commands) {
    const projectId = get().projectId
    if (!projectId) return
    set({ verificationBusy: true, taskActionError: null })
    try {
      const project = await apiJson(`${BASE}/projects/${projectId}/verification-commands`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ commands })
      }) as Project
      set({ projects: get().projects.map((item) => item.id === project.id ? project : item) })
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    } finally {
      set({ verificationBusy: false })
    }
  },

  async runVerifications() {
    const taskId = get().taskId
    if (!taskId) return
    set({ verificationBusy: true, taskActionError: null })
    try {
      await apiJson(`${BASE}/tasks/${taskId}/verifications`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}'
      })
      await get().loadVerifications()
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    } finally {
      set({ verificationBusy: false })
    }
  },

  async rerunVerification(id) {
    set({ verificationBusy: true, taskActionError: null })
    try {
      await apiJson(`${BASE}/verifications/${id}/rerun`, { method: 'POST' })
      await get().loadVerifications()
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    } finally {
      set({ verificationBusy: false })
    }
  },

  async loadReview() {
    const taskId = get().taskId
    if (!taskId) return
    try {
      const review = await apiJson(`${BASE}/tasks/${taskId}/review`) as ReviewSnapshot
      if (get().taskId === taskId) set({ review })
    } catch (error) {
      if (get().taskId === taskId) set({ taskActionError: errorMessage(error) })
    }
  },

  async addReviewComment(input) {
    const taskId = get().taskId
    if (!taskId) return
    set({ taskActionError: null })
    try {
      await apiJson(`${BASE}/tasks/${taskId}/review/comments`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input)
      })
      await get().loadReview()
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    }
  },

  async resolveReviewComment(id, resolved) {
    try {
      await apiJson(`${BASE}/review/comments/${id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resolved })
      })
      await get().loadReview()
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    }
  },

  async decideReview(input) {
    const taskId = get().taskId
    const revision = get().changeSet?.revision
    if (!taskId || !revision) return
    set({ taskBusy: true, taskActionError: null })
    try {
      const unresolved = get().review?.comments
        .filter((item) => !item.resolved_at).map((item) => item.id) ?? []
      await apiJson(`${BASE}/tasks/${taskId}/review/decision`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...input, revision, comment_ids: unresolved })
      })
      await get().refreshSelectedTask()
      await get().loadReview()
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async loadShipments() {
    const taskId = get().taskId
    if (!taskId) return
    try {
      const shipments = await apiJson(`${BASE}/tasks/${taskId}/shipments`) as TaskShipment[]
      if (get().taskId === taskId) set({ shipments })
    } catch (error) {
      if (get().taskId === taskId) set({ taskActionError: errorMessage(error) })
    }
  },

  async commitTask(message) {
    const taskId = get().taskId
    const revision = get().changeSet?.revision
    if (!taskId || !revision) return
    set({ taskBusy: true, taskActionError: null })
    try {
      await apiJson(`${BASE}/tasks/${taskId}/ship/commit`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ revision, message })
      })
      await get().inspectWorkspace()
      await get().loadShipments()
      await get().loadShipHandoff()
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async pushTask(remote = 'origin') {
    const taskId = get().taskId
    const commit = [...get().shipments].reverse().find((item) => item.action === 'commit')
    if (!taskId || !commit) return
    set({ taskBusy: true, taskActionError: null })
    try {
      await apiJson(`${BASE}/tasks/${taskId}/ship/push`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ remote, confirm_commit_sha: commit.commit_sha })
      })
      await get().loadShipments()
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async loadShipHandoff() {
    const taskId = get().taskId
    if (!taskId) return
    try {
      const shipHandoff = await apiJson(`${BASE}/tasks/${taskId}/ship/handoff`) as ShipHandoff
      if (get().taskId === taskId) set({ shipHandoff })
    } catch (error) {
      if (get().taskId === taskId) set({ taskActionError: errorMessage(error) })
    }
  },

  async archiveWorkspace(force = false) {
    const task = get().task
    const workspace = task?.workspace
    if (!task || !workspace) return
    set({ taskBusy: true, taskActionError: null })
    try {
      await apiJson(
        `${BASE}/tasks/${task.id}/workspace/${force ? 'force' : 'archive'}`,
        {
          method: force ? 'DELETE' : 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm_workspace_id: workspace.id })
        }
      )
      set({ workspaceInspection: null, changeSet: null, verificationRuns: [], review: null, shipments: [], shipHandoff: null })
      await get().refreshSelectedTask()
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
      await get().inspectWorkspace()
    } finally {
      set({ taskBusy: false })
    }
  },

  async deleteWorkspaceBranch() {
    const task = get().task
    const workspace = task?.workspace
    if (!task || !workspace) return
    set({ taskBusy: true, taskActionError: null })
    try {
      await apiJson(`${BASE}/tasks/${task.id}/workspace/branch`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ confirm_workspace_id: workspace.id })
      })
      await get().refreshSelectedTask()
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async archiveSelectedTask() {
    const { taskId, projectId } = get()
    if (!taskId || !projectId) return
    set({ taskBusy: true, taskActionError: null })
    try {
      await apiJson(`${BASE}/tasks/${taskId}`, { method: 'DELETE' })
      const tasks = (await apiJson(`${BASE}/projects/${projectId}/tasks`)) as Task[]
      set({ tasks, taskId: null, task: null, workspaceInspection: null, changeSet: null, verificationRuns: [], review: null, shipments: [], shipHandoff: null })
      if (tasks[0]) await get().selectTask(tasks[0].id)
    } catch (error) {
      set({ taskActionError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  clearTaskError() {
    set({ taskActionError: null })
  },

  async loadLatestTaskSession() {
    const taskId = get().taskId
    if (!taskId) return
    try {
      const response = await apiFetch(`${BASE}/tasks/${taskId}/sessions/latest`)
      if (get().taskId !== taskId) return
      if (response.status === 404) {
        set({ taskSession: null, taskSessionEvents: [] })
        return
      }
      if (!response.ok) throw new ApiError(response.status, await response.text())
      const session = (await response.json()) as AgentSessionDetail
      set({ taskSession: session, taskSessionEvents: session.events })
    } catch (error) {
      if (get().taskId === taskId) set({ taskRuntimeError: errorMessage(error) })
    }
  },

  selectAgentProfile(id) {
    localStorage.setItem('janus.agentProfile', id)
    set({ selectedAgentProfileId: id })
  },

  async startTaskSession(options) {
    const { taskId, selectedAgentProfileId } = get()
    if (!taskId) return
    set({ taskBusy: true, taskRuntimeError: null })
    try {
      const session = (await apiJson(`${BASE}/tasks/${taskId}/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_profile_id: selectedAgentProfileId, ...options })
      })) as AgentSessionDetail
      set({ taskSession: session, taskSessionEvents: session.events })
      get().connectTaskSession(session)
      await get().refreshSelectedTask()
    } catch (error) {
      set({ taskRuntimeError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async resumeTaskSession() {
    const session = get().taskSession
    if (!session) return
    set({ taskBusy: true, taskRuntimeError: null })
    try {
      const resumed = (await apiJson(`${BASE}/sessions/${session.id}/resume`, {
        method: 'POST'
      })) as AgentSessionDetail
      set({ taskSession: resumed, taskSessionEvents: resumed.events })
      get().connectTaskSession(resumed)
    } catch (error) {
      set({ taskRuntimeError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  connectTaskSession(session) {
    get().taskWs?.close()
    const socket = new WebSocket(
      websocketUrl(`/tasks/${session.task_id}/sessions/${session.id}`),
      ['janus', TOKEN]
    )
    set({
      taskWs: socket,
      taskConnected: false,
      taskTurnActive: false,
      taskRuntimeError: null,
      taskApprovals: []
    })

    socket.onopen = () => {
      if (get().taskWs === socket) set({ taskConnected: true })
    }
    socket.onmessage = (message) => {
      if (get().taskWs !== socket) return
      const payload = JSON.parse(message.data) as Record<string, unknown>
      const current = get().taskSessionEvents
      const liveEvent: SessionEvent = {
        session_id: session.id,
        seq: (current.at(-1)?.seq ?? 0) + 1,
        kind: String(payload.type ?? 'runtime'),
        payload,
        task_id: session.task_id,
        dispatch_id: session.dispatch_id,
        workspace_id: session.workspace_id,
        created_at: new Date().toISOString()
      }
      set({ taskSessionEvents: [...current, liveEvent] })

      if (payload.type === 'run_error') {
        set({ taskRuntimeError: String(payload.error ?? 'Task runtime failed') })
      } else if (payload.type === 'approval_request') {
        const request = payload as unknown as ApprovalRequest
        if (!get().taskApprovals.some((item) => item.id === request.id)) {
          set({ taskApprovals: [...get().taskApprovals, request] })
        }
      } else if (payload.type === 'stale_dispatch') {
        set({
          taskRuntimeError: String(payload.error ?? 'This Dispatch is stale'),
          taskTurnActive: false
        })
      } else if (payload.type === 'turn_end') {
        set({ taskTurnActive: false, taskApprovals: [] })
        void get().refreshSelectedTask()
        void get().loadLatestTaskSession()
      } else if (payload.type === 'session_stopped') {
        set({ taskTurnActive: false, taskApprovals: [] })
        void get().refreshSelectedTask()
        void get().loadLatestTaskSession()
      }
    }
    socket.onerror = () => {
      if (get().taskWs === socket) {
        set({ taskRuntimeError: 'Task runtime에 연결할 수 없습니다', taskTurnActive: false })
      }
    }
    socket.onclose = () => {
      if (get().taskWs === socket) {
        set({ taskWs: null, taskConnected: false, taskTurnActive: false })
      }
    }
  },

  sendTaskMessage(text) {
    const socket = get().taskWs
    const trimmed = text.trim()
    if (!trimmed || !socket || socket.readyState !== WebSocket.OPEN || get().taskTurnActive) return
    socket.send(JSON.stringify({ type: 'message', text: trimmed }))
    set({ taskTurnActive: true, taskRuntimeError: null })
  },

  cancelTaskTurn() {
    const socket = get().taskWs
    if (socket?.readyState === WebSocket.OPEN && get().taskTurnActive) {
      socket.send(JSON.stringify({ type: 'cancel' }))
      set({ taskApprovals: [] })
    }
  },

  respondTaskApproval(id, approved) {
    const socket = get().taskWs
    if (!socket || socket.readyState !== WebSocket.OPEN) return
    socket.send(JSON.stringify({ type: 'approval_response', id, approved }))
    set({ taskApprovals: get().taskApprovals.filter((item) => item.id !== id) })
  },

  async stopTaskSession() {
    const session = get().taskSession
    if (!session) return
    const socket = get().taskWs
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: 'stop' }))
      return
    }
    set({ taskBusy: true, taskRuntimeError: null })
    try {
      const stopped = (await apiJson(`${BASE}/sessions/${session.id}/stop`, {
        method: 'POST'
      })) as AgentSessionDetail
      set({ taskSession: stopped, taskSessionEvents: stopped.events })
      await get().refreshSelectedTask()
    } catch (error) {
      set({ taskRuntimeError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
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
