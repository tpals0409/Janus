import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Button, EmptyState, Status, Tabs } from './ui'

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

  it('gives empty states an actionable title and description', () => {
    render(<EmptyState title="스킬 없음" description="GitHub URL을 입력하세요." />)
    expect(screen.getByText('스킬 없음')).toBeVisible()
    expect(screen.getByText('GitHub URL을 입력하세요.')).toBeVisible()
  })
})
