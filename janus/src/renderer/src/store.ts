import { create } from 'zustand'
import type {
  AgentProfile, AgentProfileSkill, AgentSessionDetail, ApprovalRequest, ApprovalResponseScope, ChangeSet,
  BackendStatus, ModelProfile, Project, SessionEvent,
  ProjectLearning, PullRequestSnapshot, ReviewSnapshot, ShipHandoff,
  SkillActivationMode, SkillImportPreview, SkillSummary, Task, TaskShipment,
  TreeEntry, VerificationCommand, VerificationRun,
  WorkspaceInspection
} from './types'
import {
  ApiError, JANUS_BASE as BASE, apiFetch, apiJson, errorMessage,
  janusAuthToken, readBackendStatus, websocketUrl,
} from './api'

let openProjectSequence = 0
let openTaskSequence = 0

export const ORCH_ID = 'orchestrator'

function optimisticUserMessage(session: AgentSessionDetail, text: string, events: SessionEvent[]): SessionEvent {
  return {
    session_id: session.id,
    seq: (events.at(-1)?.seq ?? 0) + 1,
    kind: 'optimistic_transcript',
    payload: { kind: 'user', content: text },
    task_id: session.task_id,
    dispatch_id: session.dispatch_id,
    workspace_id: session.workspace_id,
    created_at: new Date().toISOString()
  }
}

interface State {
  serverUp: boolean | null
  /** true면 서버는 살아 있는데 토큰/Origin이 거부됐다 (연결 실패와 다르다) */
  authFailed: boolean
  mlxUp: boolean | null
  backendStatus: BackendStatus | null
  projects: Project[]
  tasks: Task[]
  projectId: string | null
  taskId: string | null
  task: Task | null
  agentProfiles: AgentProfile[]
  modelProfiles: ModelProfile[]
  skills: SkillSummary[]
  agentProfileSkills: AgentProfileSkill[]
  profileBusy: boolean
  profileError: string | null
  skillImportPreview: SkillImportPreview | null
  skillBusy: boolean
  skillError: string | null
  workspaceInspection: WorkspaceInspection | null
  changeSet: ChangeSet | null
  verificationRuns: VerificationRun[]
  verificationBusy: boolean
  review: ReviewSnapshot | null
  shipments: TaskShipment[]
  shipHandoff: ShipHandoff | null
  taskPullRequest: PullRequestSnapshot | null
  projectLearnings: ProjectLearning[]
  learningError: string | null
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
  pendingDelegation: { taskId: string; objective: string } | null

  /** IDE성 상태 — 워크스페이스 파일 트리와 열어본 파일 */
  sidebarTab: 'tasks' | 'files'
  tree: Record<string, TreeEntry[]>
  openedFile: { path: string; content: string } | null

  /** 하단 패널 탭 — 캔버스 클릭이 Traces로 끌어와야 해서 스토어에 있다 */
  bottomTab: 'traces' | 'logs' | 'metrics'

  boot(): Promise<void>
  pollHealth(): Promise<void>
  loadProjects(): Promise<void>
  selectProject(id: string): Promise<void>
  selectTask(id: string): Promise<void>
  refreshSelectedTask(): Promise<void>
  addProjectFromPicker(): Promise<void>
  archiveProject(id: string): Promise<void>
  createTask(input: {
    title: string
    objective: string
    acceptance_command: string
    base_ref: string
    workflow_stage?: 'direct' | 'mockup'
  }): Promise<void>
  delegateTask(objective: string, workflowStage?: 'direct' | 'mockup'): Promise<void>
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
  loadTaskPullRequest(): Promise<void>
  loadProjectLearnings(): Promise<void>
  setProjectLearningStatus(id: string, status: ProjectLearning['status']): Promise<void>
  createTaskPullRequest(input: {
    title: string; body: string; base: string; draft: boolean
  }): Promise<void>
  refreshTaskPullRequest(): Promise<void>
  archiveWorkspace(force?: boolean): Promise<void>
  deleteWorkspaceBranch(): Promise<void>
  archiveTask(id?: string): Promise<void>
  clearTaskError(): void
  loadLatestTaskSession(): Promise<void>
  selectAgentProfile(id: string): void
  updateAgentProfile(id: string, changes: Partial<AgentProfile>): Promise<boolean>
  loadSkills(): Promise<void>
  loadAgentProfileSkills(profileId?: string): Promise<void>
  previewGithubSkills(url: string): Promise<void>
  confirmGithubSkills(selectedSubpaths: string[]): Promise<void>
  dismissSkillPreview(): void
  importLocalSkills(): Promise<void>
  setAgentProfileSkill(skillId: string, mode: SkillActivationMode): Promise<void>
  startTaskSession(options?: {
    priority?: number
    queue_timeout_ms?: number
    initialMessage?: string
  }): Promise<void>
  resumeTaskSession(initialMessage?: string): Promise<void>
  approveTaskMockup(): Promise<void>
  rejectTaskMockup(feedback: string): Promise<boolean>
  connectTaskSession(session: AgentSessionDetail, initialMessage?: string): void
  sendTaskMessage(text: string): void
  cancelTaskTurn(): void
  stopTaskSession(): Promise<void>
  respondTaskApproval(id: string, approved: boolean, scope?: ApprovalResponseScope): void
  revokeTaskApprovalScope(scope: string): Promise<void>
  setSidebarTab(t: 'tasks' | 'files'): void
  setBottomTab(t: 'traces' | 'logs' | 'metrics'): void
  loadDir(rel: string): Promise<void>
  refreshTree(): void
  openFile(rel: string): Promise<void>
  closeFile(): void
}

export const useStore = create<State>((set, get) => ({
  serverUp: null,
  authFailed: false,
  mlxUp: null,
  backendStatus: null,
  projects: [],
  tasks: [],
  projectId: null,
  taskId: null,
  task: null,
  agentProfiles: [],
  modelProfiles: [],
  skills: [],
  agentProfileSkills: [],
  profileBusy: false,
  profileError: null,
  skillImportPreview: null,
  skillBusy: false,
  skillError: null,
  workspaceInspection: null,
  changeSet: null,
  verificationRuns: [],
  verificationBusy: false,
  review: null,
  shipments: [],
  shipHandoff: null,
  taskPullRequest: null,
  projectLearnings: [],
  learningError: null,
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
  pendingDelegation: null,
  sidebarTab: 'tasks',
  tree: {},
  openedFile: null,
  bottomTab: 'traces',

  async boot() {
    try {
      const [health, backendStatus, projects, agentProfiles, modelProfiles, skills] = await Promise.all([
        apiJson(`${BASE}/health`),
        readBackendStatus(),
        apiJson(`${BASE}/projects`),
        apiJson(`${BASE}/profiles/agents`),
        apiJson(`${BASE}/profiles/models`),
        apiJson(`${BASE}/skills`)
      ])
      set({
        serverUp: true,
        authFailed: false,
        mlxUp: Boolean(health.mlx),
        backendStatus,
        projects,
        agentProfiles,
        modelProfiles,
        skills,
        selectedAgentProfileId:
          agentProfiles.some((profile: AgentProfile) => profile.id === get().selectedAgentProfileId)
            ? get().selectedAgentProfileId
            : agentProfiles[0]?.id ?? 'agent_default'
      })
      const selectedProject =
        projects.find((project: Project) => project.id === get().projectId) ?? projects[0]
      if (selectedProject) await get().selectProject(selectedProject.id)
      await get().loadAgentProfileSkills(get().selectedAgentProfileId)
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
    const projectDefault = get().projects.find((project) => project.id === id)
      ?.default_agent_profile_id
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
      tree: {},
      openedFile: null,
      workspaceInspection: null,
      changeSet: null,
      verificationRuns: [],
      review: null,
      shipments: [],
      shipHandoff: null,
      taskPullRequest: null,
      projectLearnings: [],
      learningError: null,
      taskActionError: null,
      ...(projectDefault ? { selectedAgentProfileId: projectDefault } : {})
    })
    if (projectDefault) localStorage.setItem('janus.agentProfile', projectDefault)
    void get().loadDir('')
    try {
      const tasks = (await apiJson(`${BASE}/projects/${id}/tasks`)) as Task[]
      if (sequence !== openProjectSequence) return
      set({ tasks })
      const selected = tasks[0]
      if (selected) await get().selectTask(selected.id)
      await get().loadProjectLearnings()
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
      taskPullRequest: null,
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
      if (task.workspace?.state === 'ready') await get().loadTaskPullRequest()
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

  async archiveProject(id) {
    set({ taskBusy: true, taskActionError: null })
    try {
      await apiJson(`${BASE}/projects/${id}`, { method: 'DELETE' })
      const projects = (await apiJson(`${BASE}/projects`)) as Project[]
      if (get().projectId !== id) {
        set({ projects })
        return
      }
      const next = projects[0]
      if (next) {
        set({ projects })
        await get().selectProject(next.id)
        return
      }
      openProjectSequence++
      openTaskSequence++
      get().taskWs?.close()
      set({
        projects: [], projectId: null, tasks: [], taskId: null, task: null,
        taskSession: null, taskSessionEvents: [], taskWs: null, taskConnected: false,
        taskTurnActive: false, taskRuntimeError: null, taskApprovals: [],
        workspaceInspection: null, changeSet: null, verificationRuns: [], review: null,
        shipments: [], shipHandoff: null, taskPullRequest: null
      })
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

  async delegateTask(objective, workflowStage = 'direct') {
    const projectId = get().projectId
    const trimmed = objective.trim()
    if (!projectId || !trimmed) return
    set({ taskBusy: true, taskActionError: null })
    try {
      const task = (await apiJson(`${BASE}/projects/${projectId}/delegations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ objective: trimmed, workflow_stage: workflowStage })
      })) as Task
      const tasks = (await apiJson(`${BASE}/projects/${projectId}/tasks`)) as Task[]
      set({ tasks, pendingDelegation: { taskId: task.id, objective: trimmed } })
      await get().selectTask(task.id)
      await apiJson(`${BASE}/tasks/${task.id}/workspace/prepare`, { method: 'POST' })
      await get().refreshSelectedTask()
    } catch (error) {
      set({ taskActionError: errorMessage(error), pendingDelegation: null })
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

  async loadTaskPullRequest() {
    const taskId = get().taskId
    if (!taskId) return
    try {
      const taskPullRequest = await apiJson(
        `${BASE}/tasks/${taskId}/pull-request`
      ) as PullRequestSnapshot
      if (get().taskId === taskId) set({ taskPullRequest })
    } catch (error) {
      if (get().taskId === taskId) set({ taskActionError: errorMessage(error) })
    }
  },

  async createTaskPullRequest(input) {
    const taskId = get().taskId
    if (!taskId) return
    set({ taskBusy: true, taskActionError: null })
    try {
      const taskPullRequest = await apiJson(`${BASE}/tasks/${taskId}/pull-request`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input)
      }) as PullRequestSnapshot
      if (get().taskId === taskId) set({ taskPullRequest })
    } catch (error) {
      if (get().taskId === taskId) {
        set({ taskActionError: errorMessage(error) })
        await get().loadTaskPullRequest()
      }
    } finally {
      set({ taskBusy: false })
    }
  },

  async refreshTaskPullRequest() {
    const taskId = get().taskId
    if (!taskId) return
    set({ taskBusy: true, taskActionError: null })
    try {
      const taskPullRequest = await apiJson(
        `${BASE}/tasks/${taskId}/pull-request/refresh`, { method: 'POST' }
      ) as PullRequestSnapshot
      if (get().taskId === taskId) set({ taskPullRequest })
    } catch (error) {
      if (get().taskId === taskId) {
        set({ taskActionError: errorMessage(error) })
        await get().loadTaskPullRequest()
      }
    } finally {
      set({ taskBusy: false })
    }
  },

  async loadProjectLearnings() {
    const projectId = get().projectId
    if (!projectId) return
    try {
      const projectLearnings = await apiJson(
        `${BASE}/projects/${projectId}/learnings`
      ) as ProjectLearning[]
      if (get().projectId === projectId) set({ projectLearnings, learningError: null })
    } catch (error) {
      if (get().projectId === projectId) set({ learningError: errorMessage(error) })
    }
  },

  async setProjectLearningStatus(id, status) {
    const projectId = get().projectId
    if (!projectId) return
    try {
      await apiJson(`${BASE}/projects/${projectId}/learnings/${id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status })
      })
      await get().loadProjectLearnings()
    } catch (error) {
      set({ learningError: errorMessage(error) })
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
      set({ workspaceInspection: null, changeSet: null, verificationRuns: [], review: null, shipments: [], shipHandoff: null, taskPullRequest: null })
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

  async archiveTask(id) {
    const { projectId, taskId } = get()
    const target = id ?? taskId
    if (!target || !projectId) return
    set({ taskBusy: true, taskActionError: null })
    try {
      await apiJson(`${BASE}/tasks/${target}`, { method: 'DELETE' })
      const tasks = (await apiJson(`${BASE}/projects/${projectId}/tasks`)) as Task[]
      // 열려 있지 않은 작업을 지웠다면 보고 있던 화면을 흔들지 않는다.
      if (target !== taskId) {
        set({ tasks })
        return
      }
      set({ tasks, taskId: null, task: null, workspaceInspection: null, changeSet: null, verificationRuns: [], review: null, shipments: [], shipHandoff: null, taskPullRequest: null })
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
    set({ selectedAgentProfileId: id, agentProfileSkills: [], skillError: null })
    void get().loadAgentProfileSkills(id)
  },

  async updateAgentProfile(id, changes) {
    set({ profileBusy: true, profileError: null })
    try {
      const profile = (await apiJson(`${BASE}/profiles/agents/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changes)
      })) as AgentProfile
      set((state) => ({
        agentProfiles: state.agentProfiles.map((item) => item.id === id ? profile : item),
        profileError: null
      }))
      return true
    } catch (error) {
      set({ profileError: errorMessage(error) })
      return false
    } finally {
      set({ profileBusy: false })
    }
  },

  async loadSkills() {
    try {
      const skills = (await apiJson(`${BASE}/skills`)) as SkillSummary[]
      set({ skills, skillError: null })
    } catch (error) {
      set({ skillError: errorMessage(error) })
    }
  },

  async loadAgentProfileSkills(profileId = get().selectedAgentProfileId) {
    if (!profileId) return
    try {
      const assignments = (await apiJson(
        `${BASE}/profiles/agents/${profileId}/skills`
      )) as AgentProfileSkill[]
      if (get().selectedAgentProfileId === profileId) {
        set({ agentProfileSkills: assignments, skillError: null })
      }
    } catch (error) {
      if (get().selectedAgentProfileId === profileId) {
        set({ skillError: errorMessage(error) })
      }
    }
  },

  async previewGithubSkills(url) {
    const trimmed = url.trim()
    if (!trimmed) return
    set({ skillBusy: true, skillError: null, skillImportPreview: null })
    try {
      const preview = (await apiJson(`${BASE}/skills/preview/github`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: trimmed })
      })) as SkillImportPreview
      set({ skillImportPreview: preview })
    } catch (error) {
      set({ skillError: errorMessage(error) })
    } finally {
      set({ skillBusy: false })
    }
  },

  async confirmGithubSkills(selectedSubpaths) {
    const preview = get().skillImportPreview
    if (!preview || selectedSubpaths.length === 0) return
    set({ skillBusy: true, skillError: null })
    try {
      await apiJson(`${BASE}/skills/import/github`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: preview.url,
          expected_revision: preview.revision,
          selected_subpaths: selectedSubpaths
        })
      })
      set({ skillImportPreview: null })
      await get().loadSkills()
      await get().loadAgentProfileSkills()
    } catch (error) {
      set({ skillError: errorMessage(error) })
    } finally {
      set({ skillBusy: false })
    }
  },

  dismissSkillPreview() {
    set({ skillImportPreview: null })
  },

  async importLocalSkills() {
    const picked = await window.janus?.pickFolder()
    if (!picked) return
    set({ skillBusy: true, skillError: null })
    try {
      await apiJson(`${BASE}/skills/import/local`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: picked })
      })
      await get().loadSkills()
      await get().loadAgentProfileSkills()
    } catch (error) {
      set({ skillError: errorMessage(error) })
    } finally {
      set({ skillBusy: false })
    }
  },

  async setAgentProfileSkill(skillId, mode) {
    const profileId = get().selectedAgentProfileId
    if (!profileId) return
    set({ skillBusy: true, skillError: null })
    try {
      await apiJson(`${BASE}/profiles/agents/${profileId}/skills/${skillId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ activation_mode: mode })
      })
      await get().loadAgentProfileSkills(profileId)
    } catch (error) {
      set({ skillError: errorMessage(error) })
    } finally {
      set({ skillBusy: false })
    }
  },

  async startTaskSession(options) {
    const { taskId, selectedAgentProfileId } = get()
    if (!taskId) return
    set({ taskBusy: true, taskRuntimeError: null })
    try {
      const { initialMessage, ...requestOptions } = options ?? {}
      const session = (await apiJson(`${BASE}/tasks/${taskId}/sessions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_profile_id: selectedAgentProfileId, ...requestOptions })
      })) as AgentSessionDetail
      set({ taskSession: session, taskSessionEvents: session.events })
      get().connectTaskSession(session, initialMessage)
      await get().refreshSelectedTask()
    } catch (error) {
      set({ taskRuntimeError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async resumeTaskSession(initialMessage) {
    const session = get().taskSession
    if (!session) return
    set({ taskBusy: true, taskRuntimeError: null })
    try {
      const resumed = (await apiJson(`${BASE}/sessions/${session.id}/resume`, {
        method: 'POST'
      })) as AgentSessionDetail
      set({ taskSession: resumed, taskSessionEvents: resumed.events })
      get().connectTaskSession(resumed, initialMessage)
    } catch (error) {
      set({ taskRuntimeError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async approveTaskMockup() {
    const { task, taskTurnActive } = get()
    if (!task || task.workflow_stage !== 'mockup' || taskTurnActive) return
    set({ taskBusy: true, taskRuntimeError: null })
    try {
      await apiJson(`${BASE}/tasks/${task.id}/mockup/approve`, { method: 'POST' })
      await get().refreshSelectedTask()
      await get().resumeTaskSession(
        '목업을 승인합니다. 승인된 화면과 상호작용에 필요한 최소 계약만 정의하고 실제 구현과 검증을 진행하세요.'
      )
    } catch (error) {
      set({ taskRuntimeError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async rejectTaskMockup(feedback) {
    const task = get().task
    const trimmed = feedback.trim()
    if (!task || task.workflow_stage !== 'mockup' || !trimmed) return false
    set({ taskBusy: true, taskRuntimeError: null })
    try {
      const updated = (await apiJson(`${BASE}/tasks/${task.id}/mockup/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ feedback: trimmed })
      })) as Task
      set({
        task: { ...task, ...updated },
        tasks: get().tasks.map((item) => item.id === task.id ? { ...item, ...updated } : item)
      })
      return true
    } catch (error) {
      set({ taskRuntimeError: errorMessage(error) })
      return false
    } finally {
      set({ taskBusy: false })
    }
  },

  connectTaskSession(session, initialMessage) {
    get().taskWs?.close()
    const socket = new WebSocket(
      websocketUrl(`/tasks/${session.task_id}/sessions/${session.id}`),
      ['janus', janusAuthToken()]
    )
    set({
      taskWs: socket,
      taskConnected: false,
      taskTurnActive: false,
      taskRuntimeError: null,
      taskApprovals: []
    })

    socket.onopen = () => {
      if (get().taskWs !== socket) return
      set({ taskConnected: true })
      const trimmed = initialMessage?.trim()
      if (trimmed) {
        const events = get().taskSessionEvents
        set({
          taskSessionEvents: [...events, optimisticUserMessage(session, trimmed, events)],
          pendingDelegation: null
        })
        socket.send(JSON.stringify({ type: 'message', text: trimmed }))
        set({ taskTurnActive: true, taskRuntimeError: null })
      }
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
        set({ taskRuntimeError: String(payload.error ?? '작업 실행이 실패했습니다') })
      } else if (payload.type === 'approval_request') {
        const request = payload as unknown as ApprovalRequest
        if (!get().taskApprovals.some((item) => item.id === request.id)) {
          set({ taskApprovals: [...get().taskApprovals, request] })
        }
      } else if (payload.type === 'approval_scope_granted') {
        const activeSession = get().taskSession
        if (activeSession && !activeSession.approval_scopes?.some((item) => item.scope === payload.scope)) {
          set({ taskSession: {
            ...activeSession,
            approval_scopes: [...(activeSession.approval_scopes ?? []), {
              session_id: activeSession.id,
              workspace_id: activeSession.workspace_id,
              scope: String(payload.scope),
              created_at: new Date().toISOString()
            }]
          } })
        }
      } else if (payload.type === 'approval_scope_revoked') {
        const activeSession = get().taskSession
        if (activeSession) {
          set({ taskSession: {
            ...activeSession,
            approval_scopes: (activeSession.approval_scopes ?? []).filter(
              (item) => item.scope !== payload.scope
            )
          } })
        }
      } else if (payload.type === 'skill_loaded') {
        const activeSession = get().taskSession
        if (activeSession) {
          set({ taskSession: {
            ...activeSession,
            skills: (activeSession.skills ?? []).map((skill) =>
              skill.skill_version_id === payload.skill_version_id
                ? {
                    ...skill,
                    loaded_at: new Date().toISOString(),
                    load_reason: String(payload.reason ?? ''),
                    prompt_tokens: Number(payload.prompt_tokens ?? 0)
                  }
                : skill
            )
          } })
        }
      } else if (payload.type === 'skill_load_failed') {
        set({
          taskRuntimeError: `스킬 ${String(payload.requested ?? '')} 로드 실패: ${String(payload.reason ?? '알 수 없는 오류')}`
        })
      } else if (payload.type === 'stale_dispatch') {
        set({
          taskRuntimeError: String(payload.error ?? '이 디스패치는 이미 만료됐습니다'),
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
    socket.onclose = (event) => {
      if (get().taskWs === socket) {
        set({
          taskWs: null,
          taskConnected: false,
          taskTurnActive: false,
          ...(event.code === 1000 ? {} : {
            taskRuntimeError: `세션 연결이 종료됐습니다 (code ${event.code})`
          })
        })
      }
    }
  },

  sendTaskMessage(text) {
    const socket = get().taskWs
    const trimmed = text.trim()
    if (!trimmed || !socket || socket.readyState !== WebSocket.OPEN || get().taskTurnActive) return
    const session = get().taskSession
    if (!session) return
    const events = get().taskSessionEvents
    set({ taskSessionEvents: [...events, optimisticUserMessage(session, trimmed, events)] })
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

  respondTaskApproval(id, approved, scope = 'once') {
    const socket = get().taskWs
    if (!socket || socket.readyState !== WebSocket.OPEN) return
    socket.send(JSON.stringify({ type: 'approval_response', id, approved, scope }))
    set({ taskApprovals: get().taskApprovals.filter((item) => item.id !== id) })
  },

  async revokeTaskApprovalScope(scope) {
    const session = get().taskSession
    if (!session) return
    set({ taskBusy: true, taskRuntimeError: null })
    try {
      const socket = get().taskWs
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'approval_scope_revoke', scope }))
      } else {
        await apiJson(
          `${BASE}/sessions/${session.id}/approvals/${encodeURIComponent(scope)}?workspace_id=${encodeURIComponent(session.workspace_id)}`,
          { method: 'DELETE' }
        )
        set({ taskSession: {
          ...session,
          approval_scopes: (session.approval_scopes ?? []).filter((item) => item.scope !== scope)
        } })
      }
    } catch (error) {
      set({ taskRuntimeError: errorMessage(error) })
    } finally {
      set({ taskBusy: false })
    }
  },

  async stopTaskSession() {
    const session = get().taskSession
    if (!session) return
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

  setSidebarTab(t) {
    set({ sidebarTab: t })
  },

  setBottomTab(t) {
    set({ bottomTab: t })
  },

  async loadDir(rel) {
    const projectId = get().projectId
    if (!projectId) return
    try {
      const r = await apiFetch(
        `${BASE}/projects/${projectId}/tree?path=${encodeURIComponent(rel)}`
      )
      if (!r.ok) return
      const d = await r.json()
      if (get().projectId !== projectId) return
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
    const projectId = get().projectId
    if (!projectId) return
    const r = await apiFetch(
      `${BASE}/projects/${projectId}/file?path=${encodeURIComponent(rel)}`
    )
    if (!r.ok) return
    const d = await r.json()
    if (get().projectId !== projectId) return
    if (d.error) {
      set({ openedFile: { path: rel, content: `(${d.error})` } })
      return
    }
    set({ openedFile: { path: rel, content: d.content } })
  },

  closeFile() {
    set({ openedFile: null })
  },
}))

