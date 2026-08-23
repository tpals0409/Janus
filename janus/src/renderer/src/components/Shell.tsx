import {
  Boxes,
  ChartNoAxesColumn,
  FlaskConical,
  Home,
  ListTodo
} from 'lucide-react'
import { useStore } from '../store'
import { Status } from './ui'

/** Task가 제품의 첫 화면, Agent는 재사용할 실행 프로파일이다. */
const NAV: { id: string; label: string; Icon: typeof Home; wired?: boolean }[] = [
  { id: 'tasks', label: '작업', Icon: ListTodo, wired: true },
  { id: 'agents', label: '에이전트', Icon: Boxes, wired: true },
  { id: 'evals', label: '평가', Icon: FlaskConical, wired: true },
  { id: 'monitor', label: '모니터', Icon: ChartNoAxesColumn, wired: true }
]

export function NavRail({
  active,
  onSelect
}: {
  active: string
  onSelect: (id: string) => void
}) {
  return (
    <nav className="nav-rail" aria-label="기본 탐색">
      <div className="flex-1 space-y-1">
        {NAV.map(({ id, label, Icon, wired }) => (
          <button
            key={id}
            onClick={() => onSelect(id)}
            title={wired ? label : `${label} — 아직 구현되지 않음`}
            aria-label={label}
            aria-current={active === id ? 'page' : undefined}
            className="nav-rail__item"
          >
            <Icon size={17} strokeWidth={1.5} />
            <span className="sr-only">{label}</span>
          </button>
        ))}
      </div>
    </nav>
  )
}

export function AgentProfileList() {
  const profiles = useStore((state) => state.agentProfiles)
  const selectedId = useStore((state) => state.selectedAgentProfileId)
  const assignments = useStore((state) => state.agentProfileSkills)
  const selectProfile = useStore((state) => state.selectAgentProfile)

  return (
    <aside className="resource-sidebar">
      <div className="resource-sidebar__header">
        <div className="resource-sidebar__label">에이전트 프로필</div>
        <p className="resource-sidebar__description">프롬프트·스킬·컨텍스트를 실행 단위로 관리합니다.</p>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {profiles.map((profile) => (
          <button
            key={profile.id}
            onClick={() => selectProfile(profile.id)}
            className="resource-row"
            aria-selected={selectedId === profile.id}
          >
            <div className="truncate text-[12px] font-medium">{profile.name}</div>
            <div className="mt-0.5 line-clamp-1 text-[10px] text-faint">{profile.description || '일반 오케스트레이터'}</div>
            <div className="mt-1.5 flex items-center gap-2 font-mono text-[9px] text-muted">
              <Status tone="muted">{profile.worker_policy}</Status>
              {selectedId === profile.id && (
                <span>{assignments.filter((skill) => skill.activation_mode !== 'off').length}개 스킬</span>
              )}
            </div>
          </button>
        ))}
      </div>
      <div className="border-t border-border px-4 py-2 text-[9px] text-faint">
        변경은 새 Task 시도부터 적용
      </div>
    </aside>
  )
}

export function StatusBar({ mode }: { mode: string }) {
  const serverUp = useStore((s) => s.serverUp)
  const mlxUp = useStore((s) => s.mlxUp)
  const backendStatus = useStore((s) => s.backendStatus)
  const projects = useStore((s) => s.projects)
  const projectId = useStore((s) => s.projectId)
  const task = useStore((s) => s.task)
  const project = projects.find((item) => item.id === projectId)
  const serverExternal = backendStatus?.server.phase === 'external'
  const mlxPhase = backendStatus?.mlx.phase
  const mlxText = mlxUp
    ? `모델 :8080${mlxPhase === 'external' ? ' (외부)' : ''}`
    : mlxPhase === 'failed'
      ? `모델 재시작 실패 (${backendStatus?.mlx.attempts}회) · 재시도 예정`
      : mlxPhase === 'restarting'
        ? '모델 재시작 중…'
        : '모델 로딩 중…'

  return (
    <footer className="status-bar">
      <Status tone={serverUp ? 'success' : 'danger'}>
        {serverUp ? `janus-server :8765${serverExternal ? ' (외부)' : ''}` : '서버 연결 안 됨'}
      </Status>
      <Status
        tone={mlxPhase === 'failed' ? 'danger' : mlxUp ? 'success' : 'warning'}
        pulse={!mlxUp && mlxPhase !== 'failed'}
      >
        {mlxText}
      </Status>
      <button
        title={
          mode === 'agents'
            ? project?.repo_path ?? '작업 탭에서 프로젝트를 선택하세요'
            : '작업에 영속된 격리 워크트리'
        }
        className="max-w-[340px] truncate font-mono text-[10.5px] text-faint"
      >
        {mode === 'tasks' ? '작업 공간' : '프로젝트 루트'}:{' '}
        {mode === 'tasks' ? task?.workspace?.root_path ?? '준비 전' : project?.repo_path ?? '선택 안 됨'}
      </button>
      <span className="ml-auto text-faint">Janus v1.0.0</span>
    </footer>
  )
}
