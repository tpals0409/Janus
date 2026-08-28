import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ModelSetup, { ModelBlockedNotice, eta, gb } from './ModelSetup'
import { useStore } from '../store'
import type { BackendStatus } from '../types'

function backend(model: { present: boolean; incomplete?: boolean }, phase = 'failed'): BackendStatus {
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
      phase, ownership: 'owned', pid: null, lastPid: 2,
      attempts: 3, retryInMs: 0, lastError: 'exit=78', logPath: '/logs/mlx.log',
      modelId: 'qwen3.8-27b',
      catalog: [{
        id: 'qwen3.8-27b', label: 'Qwen3.8 27B (4-bit MLX)',
        repo: 'mlx-community/Qwen3.8-27B-4bit', advisory: null
      }],
      snapshots: {
        hubRoot: '/hub',
        model: presence(model.present, model.incomplete),
        draft: { ...presence(true), id: 'qwen3.8-27b-mtp', label: 'MTP 드래프터' }
      }
    }
  } as unknown as BackendStatus
}

afterEach(() => {
  useStore.setState({ backendStatus: null, mlxUp: null, agentProfiles: [], modelProfiles: [] })
  vi.unstubAllGlobals()
})

describe('ModelSetup', () => {
  it('formats sizes and hides a meaningless ETA', () => {
    expect(gb(16 * 1024 ** 3)).toBe('16.0GB')
    expect(eta(null)).toBe('')
    expect(eta(0)).toBe('')
    expect(eta(30_000)).toBe('1분 미만 남음')
    expect(eta(5 * 60_000)).toBe('약 5분 남음')
    expect(eta(90 * 60_000)).toBe('약 1시간 30분 남음')
  })

  it('offers a download when the model is missing', async () => {
    useStore.setState({ backendStatus: backend({ present: false }) })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ job: null, total_bytes: 17 * 1024 ** 3, enough_space: true,
                           disk: { free_bytes: 500 * 1024 ** 3, total_bytes: 0, path: '/hub' },
                           model: {}, draft: {} })
    }))
    render(<ModelSetup />)
    expect(await screen.findByRole('button', { name: /모델 내려받기/ })).toBeEnabled()
    expect(screen.getByText('없음')).toBeVisible()
  })

  it('offers to resume rather than restart a partial download', async () => {
    useStore.setState({ backendStatus: backend({ present: false, incomplete: true }) })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ job: null, total_bytes: 1, enough_space: true,
                           disk: { free_bytes: 1, total_bytes: 0, path: '/hub' },
                           model: {}, draft: {} })
    }))
    render(<ModelSetup />)
    expect(await screen.findByRole('button', { name: /이어받기/ })).toBeVisible()
    expect(screen.getByText('일부만 받음')).toBeVisible()
  })

  it('blocks the download when the disk cannot hold it', async () => {
    useStore.setState({ backendStatus: backend({ present: false }) })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ job: null, total_bytes: 17 * 1024 ** 3, enough_space: false,
                           disk: { free_bytes: 2 * 1024 ** 3, total_bytes: 0, path: '/hub' },
                           model: {}, draft: {} })
    }))
    render(<ModelSetup />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /모델 내려받기/ })).toBeDisabled())
    expect(screen.getByText(/공간이 부족합니다/)).toBeVisible()
  })

  it('says nothing to do once the model is ready', async () => {
    useStore.setState({ backendStatus: backend({ present: true }, 'up') })
    render(<ModelSetup />)
    expect(await screen.findByText(/로컬 모델이 준비됐습니다/)).toBeVisible()
    expect(screen.queryByRole('button', { name: /내려받기/ })).toBeNull()
  })
})

describe('ModelBlockedNotice', () => {
  const localProfile = () => useStore.setState({
    agentProfiles: [{ id: 'agent_default', model_profile_id: 'model_local' }] as never,
    modelProfiles: [{ id: 'model_local', provider: 'local' }] as never,
    selectedAgentProfileId: 'agent_default'
  })

  it('names the reason instead of failing silently', () => {
    localProfile()
    useStore.setState({ backendStatus: backend({ present: false }), mlxUp: false })
    render(<ModelBlockedNotice />)
    expect(screen.getByText(/설정 → 로컬 모델에서 내려받으세요/)).toBeVisible()
  })

  it('distinguishes a disabled server from a missing model', () => {
    localProfile()
    useStore.setState({ backendStatus: backend({ present: false }, 'disabled'), mlxUp: false })
    render(<ModelBlockedNotice />)
    expect(screen.getByText(/로컬 모델 서버가 꺼져 있습니다/)).toBeVisible()
  })

  it('stays quiet for subscription models that need no local server', () => {
    useStore.setState({
      agentProfiles: [{ id: 'agent_cli', model_profile_id: 'model_cli' }] as never,
      modelProfiles: [{ id: 'model_cli', provider: 'claude_code' }] as never,
      selectedAgentProfileId: 'agent_cli',
      backendStatus: backend({ present: false }), mlxUp: false
    })
    const { container } = render(<ModelBlockedNotice />)
    expect(container).toBeEmptyDOMElement()
  })

  it('stays quiet once the model server is up', () => {
    localProfile()
    useStore.setState({ backendStatus: backend({ present: true }, 'up'), mlxUp: true })
    const { container } = render(<ModelBlockedNotice />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('the download button', () => {
  it('posts the selected model and shows progress from the job', async () => {
    useStore.setState({ backendStatus: backend({ present: false }) })
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/model/plan')) {
        return Promise.resolve({ ok: true, status: 200, json: async () => ({
          job: null, total_bytes: 1, enough_space: true,
          disk: { free_bytes: 1, total_bytes: 0, path: '/hub' }, model: {}, draft: {}
        }) })
      }
      if (url.includes('/model/download')) {
        return Promise.resolve({ ok: true, status: 202, json: async () => ({
          job: {
            model_id: 'qwen3.8-27b', repo: 'r', status: 'running', error: null,
            downloaded_bytes: 8 * 1024 ** 3, total_bytes: 16 * 1024 ** 3,
            elapsed_ms: 1000, eta_ms: 300_000
          }
        }) })
      }
      return Promise.resolve({ ok: true, status: 200, json: async () => ({ job: null }) })
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    render(<ModelSetup />)
    await user.click(await screen.findByRole('button', { name: /모델 내려받기/ }))

    expect(await screen.findByRole('progressbar')).toHaveAttribute('aria-valuenow', '50')
    expect(screen.getByText(/8\.0GB \/ 16\.0GB/)).toBeVisible()
    expect(screen.getByText(/약 5분 남음/)).toBeVisible()
    expect(screen.getByRole('button', { name: /취소/ })).toBeVisible()
    const posted = fetchMock.mock.calls.find(([url]) => String(url).includes('/model/download'))
    expect(JSON.parse(String(posted?.[1]?.body))).toEqual({ model_id: 'qwen3.8-27b' })
  })
})
