import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { Bot, ChevronDown } from 'lucide-react'
import { Status } from './ui'

const MODEL_LOAD_KEY = 'janus.model-load-seconds'

/** 로딩 경과를 초 단위로 세고, 완료되면 다음 콜드 스타트의 기대치로 기억한다. */
function useModelLoadSeconds(loading: boolean): { elapsed: number | null; lastSeconds: number | null } {
  const elapsedRef = useRef(0)
  const [elapsed, setElapsed] = useState<number | null>(null)
  const [lastSeconds, setLastSeconds] = useState<number | null>(() => {
    const stored = Number(localStorage.getItem(MODEL_LOAD_KEY))
    return Number.isFinite(stored) && stored > 0 ? Math.round(stored) : null
  })
  useEffect(() => {
    if (!loading) {
      if (elapsedRef.current >= 3) {
        localStorage.setItem(MODEL_LOAD_KEY, String(elapsedRef.current))
        setLastSeconds(elapsedRef.current)
      }
      elapsedRef.current = 0
      setElapsed(null)
      return
    }
    const startedAt = Date.now()
    elapsedRef.current = 0
    setElapsed(0)
    const id = window.setInterval(() => {
      elapsedRef.current = Math.round((Date.now() - startedAt) / 1000)
      setElapsed(elapsedRef.current)
    }, 1000)
    return () => window.clearInterval(id)
  }, [loading])
  return { elapsed, lastSeconds }
}

export function AgentProfilePicker() {
  const profiles = useStore((state) => state.agentProfiles)
  const selectedId = useStore((state) => state.selectedAgentProfileId)
  const selectProfile = useStore((state) => state.selectAgentProfile)

  return <div className="agent-profile-picker">
    <Bot size={14} strokeWidth={1.5} aria-hidden="true" />
    <div className="agent-profile-picker__identity">
      <select value={selectedId} onChange={(event) => selectProfile(event.target.value)} aria-label="에이전트 프로필 선택">
        {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
      </select>
    </div>
    <span className="agent-profile-picker__ready" aria-label="모델 준비" />
    <ChevronDown size={12} strokeWidth={1.5} aria-hidden="true" />
  </div>
}

export function StatusBar({ mode }: { mode: string }) {
  const serverUp = useStore((s) => s.serverUp)
  const mlxUp = useStore((s) => s.mlxUp)
  const serverVersion = useStore((s) => s.serverVersion)
  const backendStatus = useStore((s) => s.backendStatus)
  const projects = useStore((s) => s.projects)
  const projectId = useStore((s) => s.projectId)
  const task = useStore((s) => s.task)
  const project = projects.find((item) => item.id === projectId)
  const serverExternal = backendStatus?.server.phase === 'external'
  const mlxPhase = backendStatus?.mlx.phase
  const acceleration = backendStatus?.mlx.acceleration
  const mlxLoading = !mlxUp && mlxPhase !== 'failed'
  const { elapsed, lastSeconds } = useModelLoadSeconds(mlxLoading)
  const loadClock = elapsed === null
    ? ''
    : ` · ${elapsed}초${lastSeconds !== null ? ` (지난번 ${lastSeconds}초)` : ''}`
  const mlxText = mlxUp
    ? mlxPhase === 'external'
      ? '모델 :8080 (외부) · MTP 확인 불가'
      : acceleration?.active
        ? '모델 :8080 · MTP 활성'
        : '모델 :8080 · MTP 비활성'
    : mlxPhase === 'failed'
      ? `모델 시작 실패 · ${backendStatus?.mlx.attempts ?? 0}회`
      : mlxPhase === 'restarting'
        ? `모델 재시작 중${loadClock}`
        : acceleration?.policy === 'required'
          ? `모델·MTP 로딩 중${loadClock}`
          : `모델 로딩 중${loadClock}`

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
      <span className="ml-auto text-faint">Janus{serverVersion ? ` v${serverVersion}` : ''}</span>
    </footer>
  )
}
