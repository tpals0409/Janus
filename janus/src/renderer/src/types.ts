export type Approval = 'auto' | 'ask'

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
}

export interface Span {
  id: string
  node_id: string
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
  params: string[]
}

export type ServicePhase = 'starting' | 'up' | 'restarting' | 'failed' | 'external' | 'stopped'

export interface BackendStatus {
  server: { phase: ServicePhase; attempts: number; retryInMs: number; lastError: string | null }
  mlx: { phase: ServicePhase; attempts: number; retryInMs: number; lastError: string | null }
}

declare global {
  interface Window {
    janus?: {
      pickFolder(): Promise<string | null>
      backendStatus(): Promise<BackendStatus>
      authToken: string
    }
  }
}
