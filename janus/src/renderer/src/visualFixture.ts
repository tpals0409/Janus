import { useStore } from './store'
import type { AgentProfile, AgentSessionDetail, Project, SessionEvent, Task, TaskWorkspace } from './types'

/** Deterministic, local-only state for visual regression and screenshot review. */
export function seedTaskRuntimeVisualFixture(): void {
  const now = '2026-08-22T07:00:00.000Z'
  const taskId = 'task_ced9a7b25cc546bb88844f7bce212a8c'
  const workspace: TaskWorkspace = {
    id: 'workspace_ac82d78caea64b7b987474f45f2a8b8a',
    task_id: taskId,
    repo_path: '/Users/local/Janus',
    root_path: '/Users/local/.janus/workspaces/restart-safe',
    base_ref: 'main',
    branch_name: 'janus/task-restart-safe',
    state: 'ready',
    progress: 'ready',
    error: null,
    owned: 1
  }
  const dispatch = {
    id: 'dispatch_d2d9ebd757e5f418e802ccb6d4075',
    task_id: taskId,
    workspace_id: workspace.id,
    agent_profile_id: 'agent_default',
    attempt: 2,
    status: 'needs_you' as const,
    error: null,
    budget: {
      dispatch: { token_limit: 32768, time_limit_ms: 900000, step_limit: 15 },
      worker: { token_limit: 8192, time_limit_ms: 300000, step_limit: 8 },
      workers: { total_limit: 4, concurrent_limit: 2 },
      queue: { timeout_ms: 300000, priority: 0 }
    },
    usage: {
      prompt_tokens: 1240,
      completion_tokens: 386,
      steps: 4,
      active_time_ms: 18432,
      workers_started: 1,
      peak_concurrent_workers: 1
    },
    budget_exhausted_reason: null,
    adaptive_decision: {}
  }
  const session: AgentSessionDetail = {
    id: 'session_ced9a7b25cc546bb88844f7bce212',
    task_id: taskId,
    dispatch_id: dispatch.id,
    agent_profile_id: 'agent_default',
    status: 'idle',
    created_at: now,
    updated_at: now,
    stopped_at: null,
    error: null,
    dispatch,
    workspace_id: workspace.id,
    workspace_root: workspace.root_path!,
    events: []
  }
  const event = (seq: number, kind: string, payload: Record<string, unknown>): SessionEvent => ({
    session_id: session.id,
    seq,
    kind,
    payload,
    task_id: taskId,
    dispatch_id: dispatch.id,
    workspace_id: workspace.id,
    created_at: now
  })
  const events = [
    event(1, 'transcript', {
      kind: 'user',
      content: 'Make restart recovery deterministic and prove it with integration tests.'
    }),
    event(2, 'transcript', {
      kind: 'assistant',
      content: 'Implemented the persistent recovery gate. All Task runtime tests pass.'
    }),
    event(3, 'span_start', { type: 'span_start' }),
    event(4, 'agent_event', { type: 'agent_event', kind: 'tool_result' }),
    event(5, 'turn_end', { type: 'turn_end' })
  ]
  session.events = events
  const task: Task = {
    id: taskId,
    project_id: 'project_demo',
    title: 'Make Task runtime restart-safe',
    objective: 'Persist every local agent turn behind a Task-owned Dispatch and resume it after the ADE restarts.',
    acceptance_command: 'python -m pytest tests/test_task_runtime.py',
    base_ref: 'main',
    status: 'needs_you',
    created_at: now,
    updated_at: now,
    archived_at: null,
    workspace,
    dispatches: [{ ...dispatch, attempt: 1, status: 'cancelled' }, dispatch]
  }
  const project: Project = {
    id: 'project_demo',
    name: 'Janus P1 Demo',
    repo_path: '/Users/local/Janus',
    created_at: now,
    updated_at: now,
    archived_at: null,
    verification_commands: [],
    default_agent_profile_id: null,
    promoted_comparison_id: null,
    profile_promoted_at: null
  }
  const profile: AgentProfile = {
    id: 'agent_default',
    name: 'Janus Local',
    description: 'Default local coding agent',
    system_prompt: '',
    tools: ['read_file', 'edit_file'],
    approval: 'ask',
    worker_policy: 'autonomous',
    max_steps: 15,
    model_profile_id: 'model_qwen38_27b_4bit',
    budget: {
      dispatch: { token_limit: 32768, time_limit_ms: 900000, step_limit: 15 },
      worker: { token_limit: 8192, time_limit_ms: 300000, step_limit: 8 },
      workers: { total_limit: 4, concurrent_limit: 2 },
      queue: { timeout_ms: 300000, priority: 0 }
    }
  }
  useStore.setState({
    serverUp: true,
    authFailed: false,
    mlxUp: true,
    projects: [project],
    projectId: project.id,
    tasks: [task],
    taskId,
    task,
    agentProfiles: [profile],
    selectedAgentProfileId: profile.id,
    workspaceInspection: {
      ...workspace,
      git_status: {
        dirty: false,
        tracked_changes: [],
        untracked: [],
        unmerged: [],
        porcelain: []
      }
    },
    taskSession: session,
    taskSessionEvents: events,
    taskConnected: false,
    taskTurnActive: false,
    taskRuntimeError: null
  })
}
