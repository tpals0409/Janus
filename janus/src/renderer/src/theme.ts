/* 테마 전환 — 계약 §4: 라이트는 v2 토큰의 명도 반전 파생.
   [data-theme='light']가 @theme 변수를 덮어쓰므로 여기서는 속성 하나만 관리한다. */

const KEY = 'janus.theme'

export type ThemePref = 'system' | 'dark' | 'light'

// jsdom엔 matchMedia가 없다 — 없으면 시스템 추적 없이 다크로 본다.
const media = typeof window.matchMedia === 'function'
  ? window.matchMedia('(prefers-color-scheme: light)')
  : null

export function themePref(): ThemePref {
  const stored = localStorage.getItem(KEY)
  return stored === 'dark' || stored === 'light' ? stored : 'system'
}

function apply(): void {
  const pref = themePref()
  const light = pref === 'light' || (pref === 'system' && Boolean(media?.matches))
  const next = light ? 'light' : 'dark'
  const root = document.documentElement
  if (root.dataset.theme === next) return
  // Chromium이 background/color 트랜지션 중이던 값을 변수 기반 테마 전환 뒤에도
  // 물고 늘어진다 — 전환하는 순간만 모든 트랜지션을 끈다.
  root.classList.add('theme-switching')
  root.dataset.theme = next
  void root.offsetWidth
  window.setTimeout(() => root.classList.remove('theme-switching'), 50)
}

export function setThemePref(pref: ThemePref): void {
  localStorage.setItem(KEY, pref)
  apply()
}

/** 렌더 전에 한 번 — 저장된 선호를 적용하고 시스템 테마 변화를 따라간다. */
export function initTheme(): void {
  apply()
  media?.addEventListener('change', apply)
}
