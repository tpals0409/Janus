import { useStore } from '../store'
import { Status } from './ui'

export function AgentProfilePicker() {
  const profiles = useStore((state) => state.agentProfiles)
  const selectedId = useStore((state) => state.selectedAgentProfileId)
  const assignments = useStore((state) => state.agentProfileSkills)
  const selectProfile = useStore((state) => state.selectAgentProfile)

  const selected = profiles.find((profile) => profile.id === selectedId)
  const activeSkills = assignments.filter((skill) => skill.activation_mode !== 'off').length
  return <div className="agent-profile-picker">
    <div className="agent-profile-picker__identity">
      <span>에이전트</span>
      <select value={selectedId} onChange={(event) => selectProfile(event.target.value)} aria-label="에이전트 프로필 선택">
        {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
      </select>
    </div>
    <div className="agent-profile-picker__meta">
      <Status tone="success">모델 준비</Status>
      <span>{selected?.worker_policy === 'autonomous' ? '워커 자동 배치' : '제한된 워커'}</span>
      <span>{activeSkills}개 능력</span>
      <em>저장하면 다음 작업부터 적용</em>
    </div>
  </div>
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
  const acceleration = backendStatus?.mlx.acceleration
  const mlxText = mlxUp
    ? mlxPhase === 'external'
      ? '모델 :8080 (외부) · MTP 확인 불가'
      : acceleration?.active
        ? '모델 :8080 · MTP 활성'
        : '모델 :8080 · MTP 비활성'
    : mlxPhase === 'failed'
      ? `모델 시작 실패 · ${backendStatus?.mlx.attempts ?? 0}회`
      : mlxPhase === 'restarting'
        ? '모델 재시작 중…'
        : acceleration?.policy === 'required'
          ? '모델·MTP 로딩 중…'
          : '모델 로딩 중…'

  return (
    <footer className="status-bar">
      <Status tone={serverUp ? 'success' : 'danger'}>
        {serverUp ? `janus-server :8765${serverExternal ? ' (외부)' : ''}` : '서버 연결 안 됨'}
      </Status>
      <Status
        tone={mlxPhase === 'failed' ? 'danger' : mlxUp ? 'success' : 'warning'}
        pulse={!mlxUp && mlxPhase !== 'failed'}
        title={mlxPhase === 'failed'
          ? acceleration?.lastError ?? '모델 로그를 확인하세요'
          : acceleration?.draftModelPath ?? undefined}
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
