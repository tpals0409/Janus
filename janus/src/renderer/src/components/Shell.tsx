import { useEffect, useRef, useState } from 'react'
import { useAgentProfileOptions, useStore } from '../store'
import { Settings, Bot } from 'lucide-react'
import { Listbox, Status } from './ui'

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
  const options = useAgentProfileOptions()
  const selectedId = useStore((state) => state.selectedAgentProfileId)
  const selectProfile = useStore((state) => state.selectAgentProfile)

  return <div className="agent-profile-picker">
    <Bot size={14} strokeWidth={1.5} aria-hidden="true" />
    <div className="agent-profile-picker__identity">
      <Listbox
        label="에이전트 프로필 선택"
        value={selectedId}
        options={options}
        onChange={selectProfile}
        className="agent-profile-picker__trigger"
      />
    </div>
    <span className="agent-profile-picker__ready" aria-label="모델 준비" />
  </div>
}

export function StatusBar({ mode, onOpenSettings }: { mode: string; onOpenSettings?: () => void }) {
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
  const snapshots = backendStatus?.mlx.snapshots
  // 모델이 없으면 로딩이 아니라 셋업 대기다. 이걸 loading으로 세면 재시작 루프 내내
  // 타이머가 돌고 "지난번 N초" 기대치에 실패 소요시간이 섞인다.
  const modelMissing = Boolean(snapshots && !snapshots.model.present)
  const mlxLoading = !mlxUp && mlxPhase !== 'failed' && mlxPhase !== 'disabled' && !modelMissing
  const { elapsed, lastSeconds } = useModelLoadSeconds(mlxLoading)
  const loadClock = elapsed === null
    ? ''
    : ` · ${elapsed}초${lastSeconds !== null ? ` (지난번 ${lastSeconds}초)` : ''}`
  const mlxText = mlxPhase === 'disabled'
    ? '로컬 모델 꺼짐 (설정)'
    : modelMissing
    ? snapshots?.model.incomplete
      ? '로컬 모델 일부만 받음 — 설정에서 이어받기'
      : '로컬 모델 없음 — 설정에서 내려받기'
    : mlxUp
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
      {onOpenSettings && (
        <button
          type="button"
          onClick={onOpenSettings}
          title="설정"
          aria-label="설정"
          className="status-bar__settings"
        >
          <Settings size={13} />
        </button>
      )}
      <Status tone={serverUp ? 'success' : 'danger'}>
        {serverUp ? `janus-server :8765${serverExternal ? ' (외부)' : ''}` : '서버 연결 안 됨'}
      </Status>
      {/* 모델이 없을 때는 상태 표시가 아니라 갈 곳이어야 한다 — 눌러서 설정으로 간다. */}
      <Status
        tone={mlxPhase === 'disabled' ? 'muted'
          : modelMissing ? 'warning'
          : mlxPhase === 'failed' ? 'danger' : mlxUp ? 'success' : 'warning'}
        pulse={mlxLoading}
        title={modelMissing
          ? `${snapshots?.model.repo ?? ''} — 설정에서 내려받으세요`
          : mlxPhase === 'failed'
            ? acceleration?.lastError ?? '모델 로그를 확인하세요'
            : acceleration?.draftModelPath ?? undefined}
        className={modelMissing && onOpenSettings ? 'status-bar__actionable' : undefined}
        onClick={modelMissing ? onOpenSettings : undefined}
      >
        {mlxText}
      </Status>
      <button
        title={
          mode === 'agents'
            ? project?.repo_path ?? '작업 탭에서 프로젝트를 선택하세요'
            : '작업이 일어나는 저장소 체크아웃'
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
