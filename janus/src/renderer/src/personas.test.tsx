/** 오픈소스 배포 전 페르소나 점검 — 각 사용자가 실제로 보게 되는 화면을 검증한다.
 *
 *  단위 테스트가 부품을 보는 동안 이 파일은 "이 사람이 앱을 켜면 무엇을 보는가"를 본다.
 *  결함이 나오면 여기가 먼저 빨개진다. */
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { useStore } from './store'
import type { BackendStatus } from './types'

function status(overrides: {
  mlxPhase?: string
  modelPresent?: boolean
  modelIncomplete?: boolean
}): BackendStatus {
  const presence = (present: boolean, incomplete = false) => ({
    id: 'qwen3.8-27b', repo: 'mlx-community/Qwen3.8-27B-4bit',
    label: 'Qwen3.8 27B (4-bit MLX)', present, path: present ? '/snap' : null, incomplete
  })
  return {
    server: {
      phase: 'up', ownership: 'owned', pid: 1, lastPid: 1,
      attempts: 0, retryInMs: 0, lastError: null, logPath: '/logs/server.log'
    },
    mlx: {
      phase: overrides.mlxPhase ?? 'failed', ownership: 'owned', pid: null, lastPid: 2,
      attempts: 3, retryInMs: 0, lastError: 'exit=78 signal=—', logPath: '/logs/mlx.log',
      modelId: 'qwen3.8-27b',
      catalog: [{
        id: 'qwen3.8-27b', label: 'Qwen3.8 27B (4-bit MLX)',
        repo: 'mlx-community/Qwen3.8-27B-4bit', advisory: null
      }],
      snapshots: {
        hubRoot: '/hub',
        model: presence(overrides.modelPresent ?? false, overrides.modelIncomplete),
        draft: { ...presence(true), id: 'qwen3.8-27b-mtp', label: 'MTP 드래프터' }
      }
    }
  } as unknown as BackendStatus
}

const LOCAL = {
  agentProfiles: [{ id: 'agent_default', name: 'Janus Local', model_profile_id: 'model_local' }],
  modelProfiles: [{ id: 'model_local', provider: 'local' }],
  selectedAgentProfileId: 'agent_default'
}
const SUBSCRIPTION = {
  agentProfiles: [{ id: 'agent_cli', name: 'Claude Code (구독)', model_profile_id: 'model_cli' }],
  modelProfiles: [{ id: 'model_cli', provider: 'claude_code' }],
  selectedAgentProfileId: 'agent_cli'
}

beforeEach(() => {
  // App은 이 플래그일 때만 boot()를 건너뛴다 — 안 그러면 우리가 세운 상태를 덮어쓴다.
  window.history.replaceState({}, '', '/?fixture=task-runtime')
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true, status: 200, json: async () => ({})
  }))
  ;(window as { janus?: unknown }).janus = { authToken: 't' }
})

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as { janus?: unknown }).janus
  useStore.setState({
    backendStatus: null, mlxUp: null, serverUp: null, projects: [], task: null,
    agentProfiles: [], modelProfiles: [], selectedAgentProfileId: 'agent_default'
  })
})

describe('페르소나 1 — 방금 clone한 신규 사용자 (모델 없음)', () => {
  it('앱이 왜 안 되는지 말하고 갈 곳을 준다', async () => {
    useStore.setState({
      serverUp: true, mlxUp: false, backendStatus: status({}), ...LOCAL
    } as never)
    render(<App />)
    // 타이틀바가 "모델 로딩"으로 영원히 도는 대신 사실을 말한다.
    expect(await screen.findByText('모델 없음')).toBeVisible()
    // 그리고 그게 누를 수 있는 것이어야 한다 — 상태 표시로 끝나면 갈 곳이 없다.
    // 타이틀바와 상태바 양쪽이 모두 설정으로 보낸다.
    const actionable = screen.getAllByRole('button', { name: /모델 없음/ })
    expect(actionable.length).toBe(2)
    for (const button of actionable) expect(button).toBeVisible()
  })

  it('상태바가 무한 재시작 대신 할 일을 말한다', async () => {
    useStore.setState({
      serverUp: true, mlxUp: false, backendStatus: status({}), ...LOCAL
    } as never)
    render(<App />)
    expect(await screen.findByText(/로컬 모델 없음 — 설정에서 내려받기/)).toBeVisible()
    expect(screen.queryByText(/모델 시작 실패/)).toBeNull()
  })

  it('일부만 받은 상태는 이어받기로 안내한다', async () => {
    useStore.setState({
      serverUp: true, mlxUp: false,
      backendStatus: status({ modelIncomplete: true }), ...LOCAL
    } as never)
    render(<App />)
    expect(await screen.findByText(/일부만 받음 — 설정에서 이어받기/)).toBeVisible()
  })
})

describe('페르소나 2 — 구독형만 쓰는 사용자 (로컬 모델 영영 없음)', () => {
  it('모델이 없어도 아무것도 막지 않는다', async () => {
    useStore.setState({
      serverUp: true, mlxUp: false,
      backendStatus: status({ mlxPhase: 'disabled' }), ...SUBSCRIPTION
    } as never)
    render(<App />)
    await waitFor(() => expect(screen.getByText('로컬 모델 꺼짐')).toBeVisible())
    // 구독형은 로컬 모델이 필요 없다 — 위임을 막는 경고가 뜨면 안 된다.
    expect(screen.queryByText(/설정 → 로컬 모델에서 내려받으세요/)).toBeNull()
    expect(screen.queryByText(/모델 없음/)).toBeNull()
  })
})

describe('페르소나 3 — 모델이 준비된 평소 사용자', () => {
  it('셋업 관련 잡음이 하나도 없다', async () => {
    useStore.setState({
      serverUp: true, mlxUp: true,
      backendStatus: status({ mlxPhase: 'up', modelPresent: true }), ...LOCAL
    } as never)
    render(<App />)
    expect(await screen.findByText('모델 준비')).toBeVisible()
    expect(screen.queryByText(/내려받기/)).toBeNull()
    expect(screen.queryByText(/이어받기/)).toBeNull()
  })
})

describe('페르소나 4 — 모델은 있는데 서버가 못 뜨는 사용자', () => {
  it('없음이 아니라 시작 실패라고 구분해서 말한다', async () => {
    useStore.setState({
      serverUp: true, mlxUp: false,
      backendStatus: status({ mlxPhase: 'failed', modelPresent: true }), ...LOCAL
    } as never)
    render(<App />)
    expect(await screen.findByText('모델 시작 실패')).toBeVisible()
    expect(screen.queryByText('모델 없음')).toBeNull()
  })
})
