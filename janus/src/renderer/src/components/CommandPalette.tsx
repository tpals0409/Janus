import { useEffect, useMemo, useRef, useState } from 'react'
import { Boxes, ChartNoAxesColumn, FlaskConical, ListTodo, Search, TerminalSquare } from 'lucide-react'
import { useStore } from '../store'

type Command = {
  id: string
  label: string
  detail: string
  icon: typeof Search
  run: () => void
}

export default function CommandPalette({ onNavigate }: { onNavigate: (id: string) => void }) {
  const profiles = useStore((state) => state.agentProfiles)
  const selectProfile = useStore((state) => state.selectAgentProfile)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [cursor, setCursor] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const panel = useRef<HTMLElement>(null)
  const previousFocus = useRef<HTMLElement | null>(null)

  const commands = useMemo<Command[]>(() => [
    { id: 'nav:tasks', label: '작업 열기', detail: '프로젝트와 Task 실행', icon: ListTodo, run: () => onNavigate('tasks') },
    { id: 'nav:agents', label: '에이전트 열기', detail: 'AgentProfile 구성', icon: Boxes, run: () => onNavigate('agents') },
    { id: 'nav:evals', label: '평가 열기', detail: '로컬 TaskSuite 비교', icon: FlaskConical, run: () => onNavigate('evals') },
    { id: 'nav:monitor', label: '모니터 열기', detail: '실행 상태와 자원', icon: ChartNoAxesColumn, run: () => onNavigate('monitor') },
    ...profiles.map((profile): Command => ({
      id: `profile:${profile.id}`,
      label: `${profile.name} 프로필로 전환`,
      detail: `${profile.worker_policy} · 실행 프로필`,
      icon: TerminalSquare,
      run: () => {
        selectProfile(profile.id)
        onNavigate('agents')
      },
    })),
  ], [onNavigate, profiles, selectProfile])

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return commands
    return commands.filter((command) => `${command.label} ${command.detail}`.toLowerCase().includes(needle))
  }, [commands, query])

  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setOpen((value) => !value)
      } else if (event.key === 'Escape') {
        setOpen(false)
      } else if (event.key === 'Tab' && open && panel.current) {
        const focusable = Array.from(panel.current.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])'
        ))
        if (focusable.length === 0) return
        const first = focusable[0]
        const last = focusable[focusable.length - 1]
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault(); last.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus()
        }
      }
    }
    window.addEventListener('keydown', handle)
    return () => window.removeEventListener('keydown', handle)
  }, [open])

  useEffect(() => {
    if (!open) return
    previousFocus.current = document.activeElement as HTMLElement | null
    setQuery('')
    setCursor(0)
    requestAnimationFrame(() => input.current?.focus())
    return () => previousFocus.current?.focus()
  }, [open])

  useEffect(() => setCursor((value) => Math.min(value, Math.max(0, filtered.length - 1))), [filtered.length])

  const execute = (command: Command | undefined) => {
    if (!command) return
    command.run()
    setOpen(false)
  }

  if (!open) return null

  return (
    <div className="ui-dialog-backdrop command-palette-backdrop" onMouseDown={() => setOpen(false)}>
      <section
        ref={panel}
        className="command-palette"
        role="dialog"
        aria-modal="true"
        aria-label="명령 팔레트"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="command-palette__search">
          <Search size={16} strokeWidth={1.5} aria-hidden="true" />
          <input
            ref={input}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value)
              setCursor(0)
            }}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown') {
                event.preventDefault()
                setCursor((value) => Math.min(filtered.length - 1, value + 1))
              } else if (event.key === 'ArrowUp') {
                event.preventDefault()
                setCursor((value) => Math.max(0, value - 1))
              } else if (event.key === 'Enter') {
                event.preventDefault()
                execute(filtered[cursor])
              }
            }}
            placeholder="명령 검색…"
            aria-label="명령 검색"
          />
          <kbd>esc</kbd>
        </div>
        <div className="command-palette__list" role="listbox">
          {filtered.map((command, index) => {
            const Icon = command.icon
            return (
              <button
                key={command.id}
                role="option"
                aria-selected={cursor === index}
                onMouseEnter={() => setCursor(index)}
                onClick={() => execute(command)}
                className="command-palette__row"
              >
                <Icon size={15} strokeWidth={1.5} aria-hidden="true" />
                <span><strong>{command.label}</strong><small>{command.detail}</small></span>
                <kbd>↵</kbd>
              </button>
            )
          })}
          {filtered.length === 0 && <div className="command-palette__empty">일치하는 명령이 없습니다.</div>}
        </div>
        <footer className="command-palette__footer"><span>↑↓ 이동</span><span>↵ 실행</span><span>⌘K 열기</span></footer>
      </section>
    </div>
  )
}
