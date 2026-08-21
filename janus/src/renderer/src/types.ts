export type NodeType = 'start' | 'llm' | 'tool' | 'end' | 'agent'
export type Approval = 'auto' | 'ask'

export interface SpecNode {
  id: string
  type: NodeType
  position?: { x: number; y: number }
  outputs?: string[]
  model?: { provider?: string; name?: string; temperature?: number; max_tokens?: number }
  system_prompt?: string
  prompt?: string
  tool?: string
  tools?: string[]
  approval?: Approval
  max_steps?: number
  inputs?: Record<string, string>
  output?: { name: string; type?: string }
}

/** agent 노드가 도는 동안 흘러나오는 세션 이벤트 */
export interface AgentEvent {
  node_id: string
  kind: 'user' | 'assistant' | 'step' | 'text_delta' | 'tool_start' | 'tool_result' | 'llm_call' | 'done'
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
}

export interface ApprovalRequest {
  id: string
  node_id: string
  tool: string
  args: Record<string, unknown>
}

export interface SpecEdge {
  from: string
  to: string
  when?: string
}

export interface Spec {
  name: string
  description?: string
  version?: number
  nodes: SpecNode[]
  edges: SpecEdge[]
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
}

export interface AgentSummary {
  id: string
  name: string
  description?: string
  node_count?: number
  error?: string
}

export interface ToolInfo {
  name: string
  description: string
  needs_approval: boolean
  params: string[]
}

declare global {
  interface Window {
    janus?: { pickFolder(): Promise<string | null> }
  }
}
