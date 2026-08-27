import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { AgentSessionDetail, SessionEvent } from '../../types'
import ContextInspector from './ContextInspector'

function session(latestWindow: Record<string, unknown> | null): AgentSessionDetail {
  return {
    context: {
      policy: {
        max_chars: 24_000, recent_blocks: 8, summary_max_chars: 4_000,
        include_task_objective: true, include_acceptance: true, include_workspace_root: true
      },
      items: [],
      estimated_static_tokens: 1_000,
      latest_window: latestWindow
    }
  } as unknown as AgentSessionDetail
}

function usageEvent(seq: number, prompt: number, cached: number): SessionEvent {
  return {
    session_id: 's', seq, kind: 'agent_event',
    payload: { kind: 'usage', prompt_tokens: prompt, completion_tokens: 10, cached_tokens: cached },
    task_id: 't', dispatch_id: 'd', workspace_id: null, created_at: ''
  }
}

describe('ContextInspector measured signals', () => {
  it('uses the engine-calibrated token target and shows calibration + cache hit rate', () => {
    render(
      <ContextInspector
        session={session({
          sent_token_estimate: 3_000, saved_token_estimate: 500,
          context_token_target: 6_000, chars_per_token: 2.1, token_calibration_samples: 12
        })}
        events={[usageEvent(1, 800, 200), usageEvent(2, 200, 50)]}
      />
    )
    expect(screen.getByText('/ 6,000 token')).toBeVisible()
    expect(screen.getByText('2.1자/tok · 실측 12회')).toBeVisible()
    expect(screen.getByText('25%')).toBeVisible()
  })

  it('falls back to the chars/4 heuristic and marks cache as unmeasured without reports', () => {
    render(<ContextInspector session={session(null)} events={[usageEvent(1, 500, 0)]} />)
    expect(screen.getByText('/ 6,000 token')).toBeVisible()
    expect(screen.getByText('—')).toBeVisible()
    expect(screen.getByText('미측정')).toBeVisible()
  })
})
