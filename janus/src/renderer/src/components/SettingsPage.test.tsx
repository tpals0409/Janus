import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SettingsPage from './SettingsPage'
import { useStore } from '../store'
import type { RuntimeSettingsSnapshot } from '../types'

const snapshot: RuntimeSettingsSnapshot = {
  settings: { localServer: true, modelId: 'qwen3.8-27b', mtpPolicy: 'required', modelSlots: 3, apc: true },
  effective: { localServer: true, modelId: 'qwen3.8-27b', mtpPolicy: 'required', modelSlots: 3, apc: true },
  locked: { localServer: false, modelId: false, mtpPolicy: false, modelSlots: false, apc: false }
}

describe('SettingsPage', () => {
  afterEach(() => {
    delete (window as { janus?: unknown }).janus
  })

  it('loads runtime knobs and applies changes with a restart warning', async () => {
    const runtimeSettingsSet = vi.fn().mockResolvedValue({
      settings: { localServer: true, modelId: 'qwen3.8-27b', mtpPolicy: 'required', modelSlots: 4, apc: true }, restarted: ['server']
    })
    ;(window as { janus?: unknown }).janus = {
      runtimeSettingsGet: vi.fn().mockResolvedValue(snapshot),
      runtimeSettingsSet
    }
    const user = userEvent.setup()
    render(<SettingsPage />)

    const slots = await screen.findByLabelText(/모델 동시 생성 슬롯/)
    await user.clear(slots)
    await user.type(slots, '4')
    expect(screen.getByText(/백엔드가 재시작됩니다/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: '저장' }))
    await waitFor(() => expect(runtimeSettingsSet).toHaveBeenCalledWith(
      expect.objectContaining({ modelSlots: 4 })
    ))
    expect(await screen.findByText(/백엔드 재시작 중/)).toBeVisible()
  })

  it('switches the default model profile immediately from settings', async () => {
    ;(window as { janus?: unknown }).janus = {
      runtimeSettingsGet: vi.fn().mockResolvedValue(snapshot)
    }
    useStore.setState({
      agentProfiles: [
        { id: 'agent_default', name: 'Janus Local' },
        { id: 'agent_claude_code', name: 'Claude Code (구독)' }
      ] as never,
      selectedAgentProfileId: 'agent_default'
    })
    const user = userEvent.setup()
    render(<SettingsPage />)
    const trigger = await screen.findByRole('combobox', { name: /모델 \(에이전트 프로필\)/ })
    await user.click(trigger)
    await user.click(await screen.findByRole('option', { name: /Claude Code/ }))
    expect(useStore.getState().selectedAgentProfileId).toBe('agent_claude_code')
    // 선택하면 목록이 닫히고 포커스가 트리거로 돌아온다.
    expect(screen.queryByRole('listbox')).toBeNull()
    expect(trigger).toHaveFocus()
    useStore.setState({ agentProfiles: [], selectedAgentProfileId: 'agent_default' })
  })

  it('warns subscription users and lets them turn the local server off', async () => {
    ;(window as { janus?: unknown }).janus = {
      runtimeSettingsGet: vi.fn().mockResolvedValue(snapshot)
    }
    useStore.setState({
      agentProfiles: [
        { id: 'agent_claude_code', name: 'Claude Code (구독)', model_profile_id: 'model_claude_code' }
      ] as never,
      modelProfiles: [
        { id: 'model_claude_code', provider: 'claude_code' }
      ] as never,
      selectedAgentProfileId: 'agent_claude_code'
    })
    const user = userEvent.setup()
    render(<SettingsPage />)
    expect(await screen.findByText(/구독형이라 이 섹션의 영향을 받지 않습니다/)).toBeVisible()
    await user.click(screen.getByLabelText(/로컬 모델 서버/))
    expect(screen.getByText(/모델 서버가 재시작됩니다/)).toBeVisible()
    // 로컬 전용 손잡이는 서버가 꺼지면 비활성화된다.
    expect(screen.getByLabelText(/MTP/)).toBeDisabled()
    useStore.setState({
      agentProfiles: [], modelProfiles: [], selectedAgentProfileId: 'agent_default'
    })
  })

  it('locks env-pinned fields', async () => {
    ;(window as { janus?: unknown }).janus = {
      runtimeSettingsGet: vi.fn().mockResolvedValue({
        ...snapshot, locked: { localServer: false, mtpPolicy: true, modelSlots: false, apc: false }
      })
    }
    render(<SettingsPage />)
    const select = await screen.findByLabelText(/MTP/)
    expect(select).toBeDisabled()
    expect(screen.getByText(/JANUS_MTP_POLICY 환경변수로 고정됨/)).toBeVisible()
  })
})
