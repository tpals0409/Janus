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
  task_class?: 'single_file_bug' | 'multi_file_refactor' | 'investigation' | 'test_heavy' | 'general'
  task_signals?: string[]
  scheduler?: {
    closed: boolean
    model_generation: { cap: number; active: number; queued: number; free: number }
  }
  effective?: {
    worker_policy: 'none' | 'fixed_one' | 'autonomous'
    worker_roles: Array<'implementer' | 'researcher' | 'verifier'>
    worker_role_sequence: Array<'implementer' | 'researcher' | 'verifier'>
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
  events: SessionEvent[]
}

export interface AgentProfile {
  id: string
  name: string
  description: string
  system_prompt: string
  tools: string[]
  approval: Approval
  worker_policy: 'none' | 'fixed_one' | 'autonomous'
  max_steps: number
  model_profile_id: string
  budget: ExecutionBudget
}

export interface ModelProfile {
  id: string
  name: string
  provider: 'local'
  model_key: string
  quantization: string
  config: Record<string, unknown>
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
  executed: false
  commit_sha: string
  branch_name: string
  local_apply_command: string
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

export interface EvaluationExperiment {
  id: string
  role: 'baseline' | 'candidate'
  label: string
  source: 'import' | 'runner'
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  agent_profile_id: string | null
  profile_snapshot: Record<string, unknown>
  config: Record<string, unknown>
  conditions: Record<string, unknown>
  report: Record<string, unknown> | null
  result_path: string | null
  error: string | null
  created_at: string
  started_at: string | null
  ended_at: string | null
}

export interface EvaluationMetrics {
  runs: number
  successes: number
  success_rate: number
  wall_mean_ms: number
  wall_stdev_ms: number
  wall_p95_ms: number
  tokens_mean: number
  tokens_stdev: number
  interventions_mean: number
  interventions_stdev: number
  worker_count_mean: number
  memory_peak_bytes_mean: number
}

export interface EvaluationComparisonRow {
  task_id: string
  baseline: EvaluationMetrics
  candidate: EvaluationMetrics
  success_rate_delta_pp: number
  wall_delta_pct: number | null
  wall_p95_delta_pct: number | null
  token_delta_pct: number | null
  intervention_delta: number
}

export interface EvaluationComparison {
  id: string
  baseline_experiment_id: string
  candidate_experiment_id: string
  thresholds: Record<string, number>
  result: {
    verdict: 'incomparable_conditions' | 'regression' | 'improved' | 'equivalent'
    condition_mismatches: Array<{ field: string; baseline: unknown; candidate: unknown }>
    regressions: Array<{ scope: string; metric: string; delta: number }>
    improvements: Array<{ scope: string; metric: string; delta: number }>
    overall: Omit<EvaluationComparisonRow, 'task_id'>
    rows: EvaluationComparisonRow[]
    conditions: { baseline: Record<string, unknown>; candidate: Record<string, unknown> }
  }
  created_at: string
}

export type OperationsLane = 'queue' | 'working' | 'needs_you' | 'review' | 'failed'

export interface OperationsTimelineItem {
  category: 'generation' | 'tool' | 'verification' | 'queue' | 'worker'
  kind: string
  at: string
  status: string | null
  label: string | null
}

export interface OperationsTask {
  id: string
  project_id: string
  project_name: string
  title: string
  status: TaskStatus
  lane: OperationsLane
  updated_at: string
  dispatch: Dispatch | null
  session: { id: string; status: AgentSessionStatus; updated_at: string } | null
  budget_progress: {
    tokens: number; steps: number; time: number; workers: number; peak: number
  }
  timeline: OperationsTimelineItem[]
  attention: boolean
}

export interface OperationsSnapshot {
  generated_at: string
  summary: {
    total: number
    attention: number
    lanes: Record<OperationsLane, number>
  }
  scheduler: {
    closed: boolean
    resources: Record<string, {
      cap: number; active: number; queued: number; next_priority: number | null
    }>
    active_leases: number
  }
  memory: { janus_process_peak_rss_bytes: number }
  tasks: OperationsTask[]
}

/** 에이전트 = 오케스트레이터 1개의 평평한 설정. 워커는 런타임에 만들어져 트레이스에만 존재한다. */
export interface Spec {
  name: string
  description?: string
  model: string
  system_prompt?: string
  tools?: string[]
  approval?: Approval
  max_steps?: number
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
  step?: number
  /** 병렬 동명 호출을 짝짓는 tool call id */
  call_id?: string
}

export interface TreeEntry {
  name: string
  type: 'dir' | 'file'
  size: number | null
}

export interface RunSummary {
  id: string
  at: string
  cancelled: boolean
  duration_ms: number
  node_count: number
  summary: string
  inputs: Record<string, string>
}

export interface RunDetail extends RunSummary {
  agent_id?: string
  spans: Span[]
}

export interface ApprovalRequest {
  id: string
  node_id: string
  tool: string
  args: Record<string, unknown>
  task_id: string
  workspace_id: string
  dispatch_id: string
}

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

export interface AgentSummary {
  id: string
  name: string
  description?: string
  model?: string
  error?: string
}

export interface ToolInfo {
  name: string
  description: string
  needs_approval: boolean
  requires_workspace: boolean
  params: string[]
}

export type ServicePhase = 'starting' | 'up' | 'restarting' | 'failed' | 'external' | 'blocked' | 'stopped'

export interface BackendServiceStatus {
  phase: ServicePhase
  attempts: number
  retryInMs: number
  lastError: string | null
  logPath: string
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

declare global {
  interface Window {
    janus?: {
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
