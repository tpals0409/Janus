import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SettingsDialog from './SettingsDialog'

const snapshot = {
  settings: { mtpPolicy: 'required', modelSlots: 3, apc: true },
  effective: { mtpPolicy: 'required', modelSlots: 3, apc: true },
  locked: { mtpPolicy: false, modelSlots: false, apc: false }
} as never

describe('SettingsDialog', () => {
  afterEach(() => {
    delete (window as { janus?: unknown }).janus
  })

  it('loads runtime knobs and applies changes with a restart warning', async () => {
    const runtimeSettingsSet = vi.fn().mockResolvedValue({
      settings: { mtpPolicy: 'required', modelSlots: 4, apc: true }, restarted: ['server']
    })
    ;(window as { janus?: unknown }).janus = {
      runtimeSettingsGet: vi.fn().mockResolvedValue(snapshot),
      runtimeSettingsSet
    }
    const user = userEvent.setup()
    render(<SettingsDialog open onClose={() => {}} />)

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

  it('locks env-pinned fields', async () => {
    ;(window as { janus?: unknown }).janus = {
      runtimeSettingsGet: vi.fn().mockResolvedValue({
        ...snapshot, locked: { mtpPolicy: true, modelSlots: false, apc: false }
      })
    }
    render(<SettingsDialog open onClose={() => {}} />)
    const select = await screen.findByLabelText(/MTP/)
    expect(select).toBeDisabled()
    expect(screen.getByText(/JANUS_MTP_POLICY 환경변수로 고정됨/)).toBeVisible()
  })
})
