import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useStore } from '../store'
import CommandPalette from './CommandPalette'

describe('CommandPalette', () => {
  beforeEach(() => {
    useStore.setState({
      agentProfiles: [],
      selectedAgentProfileId: 'agent_default'
    })
  })

  it('opens with the global shortcut and executes the keyboard-selected command', async () => {
    const navigate = vi.fn()
    const user = userEvent.setup()
    render(<CommandPalette onNavigate={navigate} />)

    await user.keyboard('{Meta>}k{/Meta}')
    expect(screen.getByRole('dialog', { name: '명령 팔레트' })).toBeVisible()
    await waitFor(() => expect(screen.getByRole('textbox', { name: '명령 검색' })).toHaveFocus())

    await user.keyboard('{ArrowDown}{Enter}')
    expect(navigate).toHaveBeenCalledWith('agents')
    expect(screen.queryByRole('dialog', { name: '명령 팔레트' })).not.toBeInTheDocument()
  })

  it('filters commands and closes with Escape', async () => {
    const user = userEvent.setup()
    render(<CommandPalette onNavigate={vi.fn()} />)

    await user.keyboard('{Control>}k{/Control}')
    await user.type(screen.getByRole('textbox', { name: '명령 검색' }), '모니터')
    expect(screen.getByRole('option', { name: /모니터 열기/ })).toBeVisible()
    expect(screen.queryByRole('option', { name: /작업 열기/ })).not.toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
