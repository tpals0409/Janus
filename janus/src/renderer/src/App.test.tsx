import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import App from './App'
import { seedTaskRuntimeVisualFixture } from './visualFixture'

describe('Janus renderer fixture', () => {
  it('renders the Task-first shell and navigates to AgentProfile configuration', async () => {
    window.history.replaceState({}, '', '/?fixture=task-runtime')
    seedTaskRuntimeVisualFixture()
    const user = userEvent.setup()

    render(<App />)

    expect(screen.getByRole('heading', { name: 'Make Task runtime restart-safe' })).toBeVisible()
    expect(screen.getByRole('navigation', { name: '기본 탐색' })).toBeVisible()
    expect(screen.getByText('janus-server :8765')).toBeVisible()

    await user.click(screen.getByRole('button', { name: '에이전트' }))
    expect(screen.getByRole('tablist', { name: '에이전트 프로필' })).toBeVisible()
    expect(screen.getByRole('heading', { name: '시스템 프롬프트' })).toBeVisible()
  })
})
