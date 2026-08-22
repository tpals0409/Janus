import { useState } from 'react'
import {
  Boxes,
  BookOpen,
  ChartNoAxesColumn,
  FlaskConical,
  Home,
  Layers,
  ListTodo,
  Rocket,
  Search,
  Settings,
  Wrench,
  Plus,
  Trash2
} from 'lucide-react'
import { useStore } from '../store'

/** Task가 제품의 첫 화면, Agent는 재사용할 실행 프로파일이다. */
const NAV: { id: string; label: string; Icon: typeof Home; wired?: boolean }[] = [
  { id: 'tasks', label: 'Tasks', Icon: ListTodo, wired: true },
  { id: 'agents', label: 'Agents', Icon: Boxes, wired: true },
  { id: 'tools', label: 'Tools', Icon: Wrench },
  { id: 'context', label: 'Context', Icon: BookOpen },
  { id: 'evals', label: 'Evals', Icon: FlaskConical, wired: true },
  { id: 'deployments', label: 'Deployments', Icon: Rocket },
  { id: 'monitor', label: 'Monitor', Icon: ChartNoAxesColumn, wired: true }
]

export function NavRail({
  active,
  onSelect
}: {
  active: string
  onSelect: (id: string) => void
}) {
  return (
    <nav className="flex w-[156px] shrink-0 flex-col border-r border-border bg-panel">
      <div className="flex-1 space-y-0.5 p-2">
        {NAV.map(({ id, label, Icon, wired }) => (
          <button
            key={id}
            onClick={() => onSelect(id)}
            title={wired ? label : `${label} — 아직 구현되지 않음`}
            className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left"
            style={{
              background: active === id ? 'var(--color-accent-soft)' : 'transparent',
              color: active === id ? 'var(--color-accent-fg)' : 'var(--color-muted)',
              opacity: wired ? 1 : 0.45
            }}
          >
            <Icon size={15} />
            <span className="text-[12.5px]">{label}</span>
          </button>
        ))}
      </div>
      <button className="flex items-center gap-2.5 border-t border-border px-4 py-3 text-muted opacity-45">
        <Settings size={15} />
        <span className="text-[12.5px]">Settings</span>
      </button>
    </nav>
  )
}

export function AgentList() {
  const agents = useStore((s) => s.agents)
  const agentId = useStore((s) => s.agentId)
  const openAgent = useStore((s) => s.openAgent)
  const createAgent = useStore((s) => s.createAgent)
  const deleteAgent = useStore((s) => s.deleteAgent)
  const spec = useStore((s) => s.spec)
  const tools = useStore((s) => s.tools)
  const models = useStore((s) => s.models)

  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [confirmId, setConfirmId] = useState<string | null>(null)

  const counts = [
    ['Tools', tools.length],
    ['Agent tools', spec?.tools?.length ?? 0],
    ['Models', models.length]
  ] as const

  const submit = async () => {
    const n = name.trim()
    if (!n) return setCreating(false)
    await createAgent(n)
    setName('')
    setCreating(false)
  }

  return (
    <aside className="flex w-[228px] shrink-0 flex-col border-r border-border bg-panel">
      <div className="p-2.5">
        <div className="mb-2 px-1 text-[10px] font-semibold tracking-wider text-faint">AGENTS</div>
        {creating ? (
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={submit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit()
              if (e.key === 'Escape') {
                setName('')
                setCreating(false)
              }
            }}
            placeholder="에이전트 이름…"
            className="mb-2 w-full rounded-md border border-accent bg-raised px-2 py-1.5 text-[12px] outline-none"
          />
        ) : (
          <button
            onClick={() => setCreating(true)}
            className="mb-2 flex w-full items-center justify-center gap-1.5 rounded-md border border-dashed border-border-strong py-1.5 text-[12px] text-muted hover:text-fg"
          >
            <Plus size={13} /> New Agent
          </button>
        )}
        <div className="relative mb-1 opacity-45">
          <Search size={12} className="absolute left-2 top-2 text-faint" />
          <input
            disabled
            placeholder="Search agents..."
            className="w-full rounded-md border border-border-strong bg-raised py-1.5 pl-7 pr-2 text-[12px] outline-none"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-2.5">
        {agents.map((a) => (
          <div
            key={a.id}
            className="group relative mb-1 rounded-md border"
            style={{
              borderColor: a.id === agentId ? 'var(--color-accent)' : 'transparent',
              background: a.id === agentId ? 'var(--color-accent-soft)' : 'transparent'
            }}
          >
            <button
              onClick={() => openAgent(a.id)}
              title={a.error}
              className="w-full px-2.5 py-2 text-left"
            >
              <div className="flex items-center gap-2">
                <Layers
                  size={13}
                  className={`shrink-0 ${a.error ? 'text-danger' : 'text-accent-fg'}`}
                />
                <span className="truncate text-[12.5px] font-medium">{a.name}</span>
              </div>
              <div
                className={`mt-0.5 truncate pl-[21px] text-[11px] ${a.error ? 'text-danger' : 'text-faint'}`}
              >
                {a.error ? '스펙 오류 · 클릭해 원문 확인' : a.description || a.model || ''}
              </div>
            </button>
            {confirmId === a.id ? (
              <div className="absolute right-1 top-1 flex gap-1">
                <button
                  onClick={() => {
                    deleteAgent(a.id)
                    setConfirmId(null)
                  }}
                  className="rounded bg-[#f8717126] px-1.5 py-0.5 text-[10px] text-danger"
                >
                  삭제
                </button>
                <button
                  onClick={() => setConfirmId(null)}
                  className="rounded px-1.5 py-0.5 text-[10px] text-muted"
                >
                  취소
                </button>
              </div>
            ) : (
              <button
                onClick={() => setConfirmId(a.id)}
                title="에이전트 삭제"
                className="absolute right-1 top-1 hidden rounded p-1 text-faint hover:text-danger group-hover:block"
              >
                <Trash2 size={12} />
              </button>
            )}
          </div>
        ))}
      </div>

      <div className="border-t border-border p-2.5">
        <div className="mb-1.5 px-1 text-[10px] font-semibold tracking-wider text-faint">
          COMPONENTS
        </div>
        {counts.map(([label, n]) => (
          <div key={label} className="flex items-center justify-between px-1 py-0.5 text-[12px]">
            <span className="text-muted">{label}</span>
            <span className="font-mono text-faint">{n}</span>
          </div>
        ))}
      </div>
    </aside>
  )
}

export function StatusBar({ mode }: { mode: string }) {
  const serverUp = useStore((s) => s.serverUp)
  const mlxUp = useStore((s) => s.mlxUp)
  const backendStatus = useStore((s) => s.backendStatus)
  const workspace = useStore((s) => s.workspace)
  const pickWorkspace = useStore((s) => s.pickWorkspace)
  const dirty = useStore((s) => s.dirty)
  const errors = useStore((s) => s.errors)
  const task = useStore((s) => s.task)
  const serverExternal = backendStatus?.server.phase === 'external'
  const mlxPhase = backendStatus?.mlx.phase
  const mlxText = mlxUp
    ? `model :8080${mlxPhase === 'external' ? ' (external)' : ''}`
    : mlxPhase === 'failed'
      ? `모델 재시작 실패 (${backendStatus?.mlx.attempts}회) · 재시도 예정`
      : mlxPhase === 'restarting'
        ? '모델 재시작 중…'
        : '모델 로딩 중…'

  return (
    <footer className="flex h-[26px] shrink-0 items-center gap-4 border-t border-border bg-panel px-3 text-[11px] text-muted">
      <span className="flex items-center gap-1.5">
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: serverUp ? 'var(--color-ok)' : 'var(--color-danger)' }}
        />
        {serverUp ? `janus-server :8765${serverExternal ? ' (external)' : ''}` : '서버 연결 안 됨'}
      </span>
      <span className="flex items-center gap-1.5" title="LLM 노드가 쓰는 모델 서버 (:8080)">
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{
            background:
              mlxPhase === 'failed'
                ? 'var(--color-danger)'
                : mlxUp
                  ? 'var(--color-ok)'
                  : 'var(--color-warn)'
          }}
        />
        {mlxText}
      </span>
      <button
        onClick={mode === 'agents' ? pickWorkspace : undefined}
        title={
          mode === 'agents'
            ? '기존 Agent 파일 도구가 갇히는 폴더. 클릭해서 변경'
            : 'Task에 영속된 격리 worktree'
        }
        className="max-w-[340px] truncate font-mono text-[10.5px] text-faint hover:text-fg"
      >
        {mode === 'tasks' ? 'task ws' : 'legacy ws'}:{' '}
        {mode === 'tasks' ? task?.workspace?.root_path ?? '준비 전' : workspace ?? '—'}
      </button>
      {mode === 'agents' && errors.length > 0 && (
        <span className="text-danger">스펙 오류 {errors.length}건</span>
      )}
      {mode === 'agents' && dirty && <span className="text-warn">저장되지 않은 변경</span>}
      <span className="ml-auto text-faint">Janus v0.1.0</span>
    </footer>
  )
}
