export type Approval = 'auto' | 'ask'

export type TaskStatus = 'todo' | 'preparing' | 'working' | 'needs_you' | 'review' | 'failed'
export type WorkspaceState = 'preparing' | 'ready' | 'failed' | 'archived'

export interface Project {
  id: string
  name: string
  repo_path: string
  created_at: string
  updated_at: string
  archived_at: string | null
  verification_commands: VerificationCommand[]
  default_agent_profile_id: string | null
  promoted_comparison_id: string | null
  profile_promoted_at: string | null
}

export interface VerificationCommand {
  kind: 'acceptance' | 'test' | 'lint' | 'typecheck' | 'custom'
  command: string
}

export interface VerificationRun extends VerificationCommand {
  id: string
  task_id: string
  dispatch_id: string | null
  trigger: 'manual' | 'agent'
  agent_claim: 'passed' | 'failed' | 'unknown' | null
  status: 'queued' | 'running' | 'passed' | 'failed' | 'error' | 'cancelled'
  head_commit: string
  revision: string
  exit_code: number | null
  stdout: string
  stderr: string
  duration_ms: number | null
  error: string | null
  created_at: string
  started_at: string | null
  ended_at: string | null
}

export interface TaskWorkspace {
  id: string
  task_id: string
  repo_path: string
  root_path: string | null
  base_ref: string
  branch_name: string | null
  state: WorkspaceState
  progress: string
  error: string | null
  owned: 0 | 1
  /** 격리가 끊겨 있던 동안(0d53440~v1.0.27) 사용자의 체크아웃에서 직접 작업하던 Task */
  legacy_direct_checkout?: 0 | 1
  job_active?: boolean
}

export interface Task {
  id: string
  project_id: string
  title: string
  objective: string
  acceptance_command: string
  base_ref: string
  status: TaskStatus
  workflow_stage?: 'direct' | 'mockup' | 'implementation'
  mockup_feedback?: string | null
  attention_reason?: 'conversation_idle' | 'mockup_review' | 'input_required' | null
  created_at: string
  updated_at: string
  archived_at: string | null
  workspace?: TaskWorkspace | null
  dispatches?: Dispatch[]
}

export interface Dispatch {
  id: string
  task_id: string
  workspace_id: string
  agent_profile_id: string
  attempt: number
  status: 'queued' | 'running' | 'needs_you' | 'completed' | 'failed' | 'cancelled'
  error: string | null
  budget: ExecutionBudget
  usage: BudgetUsage
  budget_exhausted_reason: string | null
  adaptive_decision: AdaptiveDecision
}

export interface AdaptiveDecision {
  version?: number
  task_class?: 'single_file_bug' | 'multi_file_refactor' | 'multi_component_build' | 'investigation' | 'planning' | 'visual_prototype' | 'operations' | 'test_heavy' | 'general'
  task_signals?: string[]
  scheduler?: {
    closed: boolean
    model_generation: { cap: number; active: number; queued: number; free: number }
  }
  effective?: {
    worker_policy: 'none' | 'fixed_one' | 'autonomous'
    worker_roles: Array<'scout' | 'planner' | 'prototyper' | 'implementer' | 'verifier' | 'operator'>
    worker_role_sequence: Array<'scout' | 'planner' | 'prototyper' | 'implementer' | 'verifier' | 'operator'>
    allow_autonomous_workers: boolean
    budget: ExecutionBudget
  }
  retry?: {
    previous_dispatch_id: string | null
    failure_type: string | null
    evidence: string | null
    strategy: string
    allowed: boolean
  }
  reasons?: string[]
}

export interface ExecutionBudget {
  dispatch: { token_limit: number; time_limit_ms: number; step_limit: number }
  worker: { token_limit: number; time_limit_ms: number; step_limit: number }
  workers: { total_limit: number; concurrent_limit: number }
  queue: { timeout_ms: number; priority: number }
}

export interface BudgetUsage {
  prompt_tokens: number
  completion_tokens: number
  steps: number
  active_time_ms: number
  workers_started: number
  peak_concurrent_workers: number
}

export type AgentSessionStatus = 'created' | 'running' | 'idle' | 'stopped' | 'failed'

export interface SessionEvent {
  session_id: string
  seq: number
  kind: string
  payload: Record<string, unknown>
  task_id: string
  dispatch_id: string
  workspace_id: string | null
  created_at: string
}

export interface AgentSessionDetail {
  id: string
  task_id: string
  dispatch_id: string
  agent_profile_id: string
  status: AgentSessionStatus
  created_at: string
  updated_at: string
  stopped_at: string | null
  error: string | null
  dispatch: Dispatch
  workspace_id: string
  workspace_root: string
  skills?: AgentProfileSkill[]
  approval_scopes?: SessionApprovalScope[]
  context?: ContextSnapshot
  events: SessionEvent[]
}

export interface SessionApprovalScope {
  session_id: string
  workspace_id: string
  scope: string
  created_at: string
}

export interface ContextPolicy {
  max_chars: number
  recent_blocks: number
  summary_max_chars: number
  include_task_objective: boolean
  include_acceptance: boolean
  include_workspace_root: boolean
}

export interface ContextItem {
  id: string
  label: string
  source: string
  status: 'included' | 'excluded'
  content: string
  chars: number
  estimated_tokens: number
  detail: Record<string, unknown>
}

export interface ContextSnapshot {
  policy: ContextPolicy
  items: ContextItem[]
  estimated_static_tokens: number
  latest_window: Record<string, unknown> | null
}

export interface AgentProfile {
  id: string
  name: string
  description: string
  system_prompt: string
  base_system_prompt?: string
  coding_rules_prompt?: string
  effective_system_prompt?: string
  tools: string[]
  approval: Approval
  worker_policy: 'none' | 'fixed_one' | 'autonomous'
  max_steps: number
  model_profile_id: string
  budget: ExecutionBudget
  context_policy: ContextPolicy
}

export interface ModelProfile {
  id: string
  name: string
  provider: 'local' | 'claude_code' | 'codex'
  model_key: string
  quantization: string
  config: Record<string, unknown>
}

export type SkillActivationMode = 'off' | 'auto' | 'manual'
export type SkillCompatibility = 'native' | 'partial' | 'adapter_required' | 'blocked'

export interface SkillSummary {
  id: string
  latest_version_id: string
  namespace: string
  name: string
  description: string
  source_kind: 'janus' | 'codex' | 'claude' | 'github' | 'local' | 'project'
  source_locator: string
  source_subpath: string
  trust_state: 'untrusted' | 'trusted' | 'blocked'
  version: number
  content_hash: string
  source_revision: string | null
  compatibility: SkillCompatibility
  compiled: {
    format?: string
    name?: string
    description?: string
    activation?: { model_invocable?: boolean; user_invocable?: boolean; paths?: string[] }
    execution?: { context?: 'inline' | 'worker'; agent?: string | null }
    capabilities?: {
      required?: string[]
      approval_required?: string[]
      unmapped?: string[]
    }
  }
  report: {
    compatibility?: SkillCompatibility
    warnings?: string[]
    blocked_features?: string[]
    file_count?: number
    total_bytes?: number
    estimated_prompt_tokens?: number
    license?: string | null
    license_file?: string | null
  }
}

export interface AgentProfileSkill {
  agent_profile_id: string
  skill_id: string
  skill_version_id: string
  activation_mode: SkillActivationMode
  priority: number
  namespace: string
  name: string
  description: string
  source_kind: SkillSummary['source_kind']
  source_locator: string
  source_subpath: string
  trust_state: SkillSummary['trust_state']
  version: number
  content_hash: string
  compatibility: SkillCompatibility
  compiled: SkillSummary['compiled']
  report: SkillSummary['report']
  loaded_at?: string | null
  load_reason?: string | null
  prompt_tokens?: number
}

export interface SkillImportCandidate {
  namespace: string
  name: string
  description: string
  source_kind: 'github'
  source_locator: string
  source_subpath: string
  source_revision: string
  content_hash: string
  compatibility: SkillCompatibility
  compiled: SkillSummary['compiled']
  report: SkillSummary['report']
}

export interface SkillImportPreview {
  source: string
  url: string
  revision: string
  license: string | null
  skills: SkillImportCandidate[]
}

export interface WorkspaceInspection extends TaskWorkspace {
  git_status?: {
    dirty: boolean
    tracked_changes: string[]
    untracked: string[]
    unmerged: string[]
    porcelain: string[]
  }
}

export type ChangeLayer = 'committed' | 'staged' | 'unstaged' | 'untracked'

export interface ChangeSetFile {
  layer: ChangeLayer
  status: string
  path: string
  old_path: string | null
  binary: boolean
  large: boolean
  diff_bytes: number
  diff: string | null
  truncated: boolean
}

export interface ChangeSet {
  source: 'git'
  derived_at: string
  base_ref: string
  base_commit: string
  merge_base: string
  head_commit: string
  revision: string
  branch_name: string | null
  sections: Record<ChangeLayer, ChangeSetFile[]>
  counts: Record<ChangeLayer, number>
  dirty: boolean
  unmerged: string[]
}

export interface ReviewComment {
  id: string
  task_id: string
  revision: string
  layer: ChangeLayer
  file_path: string
  old_line: number | null
  new_line: number | null
  hunk_header: string | null
  body: string
  created_at: string
  resolved_at: string | null
}

export interface ReviewDecision {
  id: string
  task_id: string
  revision: string
  decision: 'accept' | 'request_changes' | 'discard'
  comment_ids: string[]
  message: string
  created_at: string
}

export interface ReviewSnapshot {
  task_status: TaskStatus
  revision: string
  unmerged: string[]
  comments: ReviewComment[]
  decisions: ReviewDecision[]
}

export interface TaskShipment {
  id: string
  task_id: string
  action: 'commit' | 'push'
  commit_sha: string
  branch_name: string
  remote: string | null
  status: 'completed' | 'failed'
  error: string | null
  created_at: string
}

export interface ShipHandoff {
  executed: boolean
  commit_sha: string
  branch_name: string
  local_apply_command: string | null
  push_command: string
  notice: string
}

export interface PullRequestCheck {
  name: string
  state: string
  bucket?: string
  workflow?: string
  link?: string
  description?: string
}

export interface PullRequestRun {
  databaseId: number
  name: string
  displayTitle?: string
  status: string
  conclusion: string | null
  url: string
}

export interface PullRequestFailedLog {
  run_id: number
  name: string
  conclusion: string
  url: string
  log: string
  truncated: boolean
}

export interface TaskPullRequest {
  id: string
  task_id: string
  number: number | null
  url: string | null
  state: 'creating' | 'open' | 'closed' | 'merged' | 'error'
  title: string
  head_branch: string
  base_branch: string
  draft: boolean
  merge_state: string | null
  review_decision: string | null
  checks: PullRequestCheck[]
  runs: PullRequestRun[]
  failed_logs: PullRequestFailedLog[]
  error: string | null
  created_at: string
  updated_at: string
  merged_at: string | null
  closed_at: string | null
}

export interface PullRequestSnapshot {
  pull_request: TaskPullRequest | null
  archive_recommended: boolean
  archive_reason: string | null
  branch_preserved: true
}

export interface ProjectLearning {
  id: string
  project_id: string
  kind: 'preference' | 'verification' | 'workflow' | 'avoidance'
  title: string
  content: string
  confidence: number
  evidence_count: number
  success_count: number
  failure_count: number
  status: 'active' | 'paused' | 'archived'
  evidence: string[]
  created_at: string
  updated_at: string
  last_applied_at: string | null
}

/** 에이전트가 도는 동안 흘러나오는 세션 이벤트 */
export interface AgentEvent {
  node_id: string
  task_id?: string
  workspace_id?: string
  dispatch_id?: string
  session_id?: string
  kind:
    | 'user'
    | 'assistant'
    | 'step'
    | 'text_delta'
    | 'reasoning_delta'
    | 'tool_start'
    | 'tool_result'
    | 'llm_call'
    | 'usage'
    | 'done'
  at_ms: number
  content?: string
  text?: string
  n?: number
  name?: string
  args?: Record<string, unknown>
  value?: Record<string, unknown>
  reason?: string
  messages?: { role: string; content: string }[]
  total_messages?: number
  prompt_tokens?: number
  completion_tokens?: number
  /** 서버 프롬프트 캐시(APC) 실측 적중 — 미지원 서버는 0 */
  cached_tokens?: number
  step?: number
  /** 병렬 동명 호출을 짝짓는 tool call id */
  call_id?: string
}

export interface TreeEntry {
  name: string
  type: 'dir' | 'file'
  size: number | null
}

export interface ApprovalRequest {
  id: string
  node_id: string
  tool: string
  args: Record<string, unknown>
  task_id: string
  workspace_id: string
  dispatch_id: string
  rememberable?: boolean
  approval_scope?: 'workspace_write' | 'workspace_shell' | null
  /** 서버가 무응답을 거부로 처리하는 시각. 재연결 재전송에도 원래 마감이 유지된다. */
  deadline_epoch_ms?: number
}

export type ApprovalResponseScope = 'once' | 'session_workspace'

export interface Span {
  id: string
  node_id: string
  task_id?: string
  workspace_id?: string
  dispatch_id?: string
  session_id?: string
  status: 'running' | 'success' | 'error'
  started_ms: number
  duration_ms?: number
  input?: unknown
  output?: unknown
  events?: AgentEvent[]
  usage?: { prompt_tokens: number; completion_tokens: number } | null
  /** 오케스트레이터는 null, 워커는 부모(오케스트레이터) 스팬 id */
  parent_id?: string | null
  /** 워커의 표시 이름 (create_worker의 name) */
  label?: string | null
}

export type ServicePhase = 'starting' | 'up' | 'restarting' | 'failed' | 'external' | 'blocked' | 'stopped'
  | 'disabled'

export interface ModelPresence {
  id: string
  repo: string
  label: string
  present: boolean
  path: string | null
  /** 있지만 샤드가 빠짐 — 재개 다운로드로 고칠 수 있다 */
  incomplete: boolean
}

export interface BackendServiceStatus {
  phase: ServicePhase
  ownership: 'owned' | 'external' | 'none'
  pid: number | null
  lastPid: number | null
  attempts: number
  retryInMs: number
  lastError: string | null
  logPath: string
  acceleration?: {
    policy: 'required' | 'preferred' | 'off'
    configured: boolean
    active: boolean
    kind: 'mtp' | null
    draftModelPath: string | null
    lastError: string | null
  }
  /** mlx만 — 모델이 실제로 디스크에 있는지 */
  snapshots?: { hubRoot: string; model: ModelPresence; draft: ModelPresence }
  catalog?: { id: string; label: string; repo: string; advisory: string | null }[]
  modelId?: string
}

export interface ModelDownloadJob {
  model_id: string
  repo: string
  status: 'running' | 'completed' | 'failed' | 'cancelled'
  error: string | null
  downloaded_bytes: number
  total_bytes: number
  elapsed_ms: number
  eta_ms: number | null
}

export interface ModelPlan {
  model: { repo: string; files: number; total_bytes: number }
  draft: { repo: string; files: number; total_bytes: number }
  total_bytes: number
  disk: { free_bytes: number; total_bytes: number; path: string }
  enough_space: boolean
}

export interface BackendStatus {
  server: BackendServiceStatus
  mlx: BackendServiceStatus
}

export interface TaskBrowserStatus {
  taskId: string
  partition: string
  url: string
  open: boolean
  console: Array<{ at: string; level: string; message: string; line?: number; source?: string }>
  network: Array<{ at: string; method: string; url: string; status?: number; error?: string }>
}

export interface TaskBrowserInspection {
  element: {
    tag: string; id: string | null; classes: string[]; html: string; text: string
    css: Record<string, string>
    rect: { x: number; y: number; width: number; height: number }
    sourceContext: string | null
    url: string
  }
  screenshotDataUrl: string
}

export interface RuntimeSettingsValues {
  localServer: boolean
  modelId: string
  mtpPolicy: 'required' | 'preferred' | 'off'
  modelSlots: number
  apc: boolean
}

export interface RuntimeSettingsSnapshot {
  settings: RuntimeSettingsValues
  effective: RuntimeSettingsValues
  locked: {
    localServer: boolean; modelId: boolean
    mtpPolicy: boolean; modelSlots: boolean; apc: boolean
  }
}

declare global {
  interface Window {
    janus?: {
      runtimeSettingsGet?: () => Promise<RuntimeSettingsSnapshot>
      runtimeSettingsSet?: (settings: RuntimeSettingsValues) => Promise<{
        settings: RuntimeSettingsValues; restarted: string[]
      }>
      pickFolder(): Promise<string | null>
      backendStatus(): Promise<BackendStatus>
      taskBrowserOpen(input: { taskId: string; url: string }): Promise<TaskBrowserStatus>
      taskBrowserStatus(taskId: string): Promise<TaskBrowserStatus>
      taskBrowserScreenshot(taskId: string): Promise<{ dataUrl: string; url: string }>
      taskBrowserInspect(taskId: string): Promise<TaskBrowserInspection>
      authToken: string
    }
  }
}
