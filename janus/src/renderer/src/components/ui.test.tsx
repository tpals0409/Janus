import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { Button, ConfirmDialog, EmptyState, Status, Tabs } from './ui'

describe('Janus UI primitives', () => {
  it('uses explicit button types so controls do not submit forms accidentally', () => {
    render(<Button>저장</Button>)
    expect(screen.getByRole('button', { name: '저장' })).toHaveAttribute('type', 'button')
  })

  it('exposes semantic status text in addition to its signal glyph', () => {
    render(<Status tone="warning">승인 필요</Status>)
    expect(screen.getByText('승인 필요')).toBeVisible()
    expect(screen.getByText('△')).toHaveAttribute('aria-hidden', 'true')
  })

  it('changes tabs through the common tab contract', () => {
    const change = vi.fn()
    render(<Tabs items={['프롬프트', '스킬'] as const} value="프롬프트" onChange={change} label="프로필" />)
    fireEvent.click(screen.getByRole('tab', { name: '스킬' }))
    expect(change).toHaveBeenCalledWith('스킬')
    expect(screen.getByRole('tab', { name: '프롬프트' })).toHaveAttribute('aria-selected', 'true')
  })

  it('moves tabs with arrow, home, and end keys', async () => {
    const user = userEvent.setup()
    function Harness() {
      const [value, setValue] = useState<'그래프' | '도구' | '컨텍스트'>('그래프')
      return <Tabs items={['그래프', '도구', '컨텍스트'] as const} value={value} onChange={setValue} label="프로필" />
    }
    render(<Harness />)
    const first = screen.getByRole('tab', { name: '그래프' })
    first.focus()
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('tab', { name: '도구' })).toHaveFocus()
    await user.keyboard('{End}')
    expect(screen.getByRole('tab', { name: '컨텍스트' })).toHaveFocus()
    await user.keyboard('{Home}')
    expect(first).toHaveFocus()
  })

  it('closes a dialog with escape and restores focus', async () => {
    const user = userEvent.setup()
    function Harness() {
      const [open, setOpen] = useState(false)
      return <><button onClick={() => setOpen(true)}>열기</button><ConfirmDialog open={open} title="삭제할까요?" onClose={() => setOpen(false)} onConfirm={() => setOpen(false)} /></>
    }
    render(<Harness />)
    const opener = screen.getByRole('button', { name: '열기' })
    await user.click(opener)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  })

  it('gives empty states an actionable title and description', () => {
    render(<EmptyState title="스킬 없음" description="GitHub URL을 입력하세요." />)
    expect(screen.getByText('스킬 없음')).toBeVisible()
    expect(screen.getByText('GitHub URL을 입력하세요.')).toBeVisible()
  })
})
