import { beforeEach, describe, expect, it, vi } from 'vitest'

/* 테마 전환의 세 가지 약속: 시스템 따름이 기본, 변화를 따라감, 명시 선택이 이김. */
describe('theme', () => {
  beforeEach(() => {
    vi.resetModules()
    localStorage.clear()
    delete document.documentElement.dataset.theme
  })

  it('follows the system by default and lets an explicit choice win', async () => {
    const listeners: Array<() => void> = []
    let systemLight = false
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      get matches() { return systemLight },
      addEventListener: (_event: string, listener: () => void) => listeners.push(listener),
      removeEventListener: vi.fn()
    })))
    const { initTheme, setThemePref, themePref } = await import('./theme')

    initTheme()
    expect(themePref()).toBe('system')
    expect(document.documentElement.dataset.theme).toBe('dark')

    systemLight = true
    listeners.forEach((listener) => listener())
    expect(document.documentElement.dataset.theme).toBe('light')

    setThemePref('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(localStorage.getItem('janus.theme')).toBe('dark')

    setThemePref('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    expect(themePref()).toBe('light')
  })
})
