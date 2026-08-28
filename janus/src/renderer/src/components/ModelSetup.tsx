import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Check, Download, Loader2, X } from 'lucide-react'
import { errorMessage, janusApi } from '../api'
import { useDomainEvent } from '../domainEvents'
import { useStore } from '../store'
import { Button, Listbox } from './ui'
import type { ModelDownloadJob, ModelPlan, ModelPresence } from '../types'

export function gb(bytes: number): string {
  return `${(bytes / 1024 ** 3).toFixed(1)}GB`
}

/** 남은 시간을 사람이 읽는 단위로. 초 단위 카운트다운은 요동쳐서 오히려 안 읽힌다. */
export function eta(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms) || ms <= 0) return ''
  if (ms < 60000) return '1분 미만 남음'  // 반올림하면 30초가 "약 1분"이 된다
  const minutes = Math.round(ms / 60000)
  if (minutes < 60) return `약 ${minutes}분 남음`
  return `약 ${Math.floor(minutes / 60)}시간 ${minutes % 60}분 남음`
}

function PresenceRow({ item }: { item: ModelPresence }) {
  const tone = item.present ? 'ok' : item.incomplete ? 'warn' : 'faint'
  const label = item.present ? '준비됨' : item.incomplete ? '일부만 받음' : '없음'
  return (
    <div className="model-setup__row">
      <span className={`model-setup__dot model-setup__dot--${tone}`} aria-hidden="true" />
      <span className="model-setup__name">{item.label}</span>
      <span className="model-setup__state">{label}</span>
      <code title={item.path ?? item.repo}>{item.repo}</code>
    </div>
  )
}

/** 로컬 모델 진단과 다운로드. 전에는 모델이 없으면 앱이 조용히 재시작만 반복했다. */
export default function ModelSetup({ disabled = false }: { disabled?: boolean }) {
  const backendStatus = useStore((state) => state.backendStatus)
  const mlx = backendStatus?.mlx
  const snapshots = mlx?.snapshots
  const catalog = mlx?.catalog ?? []
  const modelId = mlx?.modelId ?? ''

  const [plan, setPlan] = useState<ModelPlan | null>(null)
  const [job, setJob] = useState<ModelDownloadJob | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const needsDownload = Boolean(snapshots && (!snapshots.model.present || !snapshots.draft.present))

  const loadStatus = useCallback(async () => {
    try {
      const status = await janusApi<{ job: ModelDownloadJob | null }>('/model/status')
      setJob(status.job)
    } catch { /* 백엔드가 아직 안 떴을 수 있다 */ }
  }, [])

  useEffect(() => { void loadStatus() }, [loadStatus])

  // 용량 조회는 네트워크를 쓰므로 실제로 받아야 할 때만.
  useEffect(() => {
    if (!needsDownload || !modelId || plan !== null) return
    let cancelled = false
    janusApi<ModelPlan>(`/model/plan?model_id=${encodeURIComponent(modelId)}`)
      .then((value) => { if (!cancelled) setPlan(value) })
      .catch((cause) => { if (!cancelled) setError(errorMessage(cause)) })
    return () => { cancelled = true }
  }, [needsDownload, modelId, plan])

  // DomainEvent는 평면이다 — payload 키가 그대로 이벤트에 실린다.
  useDomainEvent('model', (event) => {
    if (typeof event.status !== 'string') return
    setJob(event as unknown as ModelDownloadJob)
  })

  const running = job?.status === 'running'

  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      const response = await janusApi<{ job: ModelDownloadJob }>('/model/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: modelId })
      })
      setJob(response.job)
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    setBusy(true)
    try {
      await janusApi('/model/cancel', { method: 'POST' })
      await loadStatus()
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy(false)
    }
  }

  if (!snapshots) {
    return (
      <div className="settings-dialog__loading">
        <Loader2 size={14} className="animate-spin" /> 모델 상태 확인 중
      </div>
    )
  }

  const selected = catalog.find((entry) => entry.id === modelId)
  // total이 0인 경우가 있다 — 이미 캐시된 파일만 남았을 때 hf가 크기를 "-"로 준다.
  const percent = !job ? 0
    : job.total_bytes > 0
      ? Math.min(100, Math.round((job.downloaded_bytes / job.total_bytes) * 100))
      : 100

  return (
    <div className="model-setup">
      <PresenceRow item={snapshots.model} />
      <PresenceRow item={snapshots.draft} />

      {selected?.advisory && (
        <p className="settings-dialog__warning">
          <AlertTriangle size={11} /> {selected.advisory}
        </p>
      )}

      {running ? (
        <div className="model-setup__progress">
          <div className="model-setup__bar" role="progressbar" aria-valuenow={percent}
               aria-valuemin={0} aria-valuemax={100}>
            <span style={{ width: `${percent}%` }} />
          </div>
          <div className="model-setup__meta">
            <span>{percent}% · {gb(job.downloaded_bytes)} / {gb(job.total_bytes)}</span>
            <span>{eta(job.eta_ms)}</span>
            <Button variant="ghost" compact onClick={() => void cancel()} disabled={busy}>
              <X size={11} /> 취소
            </Button>
          </div>
        </div>
      ) : needsDownload ? (
        <>
          {plan && (
            <p className="model-setup__hint">
              내려받을 용량 {gb(plan.total_bytes)} · 디스크 여유 {gb(plan.disk.free_bytes)}
              {!plan.enough_space && ' — 공간이 부족합니다'}
            </p>
          )}
          <Button
            variant="primary"
            onClick={() => void start()}
            disabled={disabled || busy || !modelId || (plan !== null && !plan.enough_space)}
          >
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
            {snapshots.model.incomplete ? '이어받기' : '모델 내려받기'}
          </Button>
          <p className="model-setup__hint">
            중간에 취소해도 받은 만큼은 남고, 다시 시작하면 이어서 받습니다.
          </p>
        </>
      ) : (
        <p className="model-setup__hint model-setup__hint--ok">
          <Check size={11} /> 로컬 모델이 준비됐습니다.
        </p>
      )}

      {job?.status === 'failed' && job.error && (
        <p className="settings-dialog__warning">다운로드 실패 — {job.error}</p>
      )}
      {job?.status === 'cancelled' && (
        <p className="model-setup__hint">취소했습니다. 다시 시작하면 이어서 받습니다.</p>
      )}
      {error && <p className="settings-dialog__warning">{error}</p>}
    </div>
  )
}

/** 로컬 모델이 준비되지 않아 위임이 막히는 이유. 준비됐거나 구독형이면 null.
 *
 *  전에는 modelReady가 false면 useEffect가 조용히 return해서 위임 버튼이 무반응이었다.
 *  두 컴포저가 같은 사유를 보여줘야 해서 훅으로 둔다. */
export function useLocalModelBlock(): string | null {
  const mlx = useStore((state) => state.backendStatus?.mlx)
  const mlxUp = useStore((state) => state.mlxUp)
  const profiles = useStore((state) => state.agentProfiles)
  const models = useStore((state) => state.modelProfiles)
  const selected = useStore((state) => state.selectedAgentProfileId)
  const provider = models.find((model) =>
    model.id === profiles.find((profile) => profile.id === selected)?.model_profile_id
  )?.provider ?? 'local'
  if (provider !== 'local' || mlxUp) return null
  if (mlx?.phase === 'disabled') {
    return '로컬 모델 서버가 꺼져 있습니다. 설정에서 켜거나 구독형 모델을 고르세요.'
  }
  if (mlx?.snapshots && !mlx.snapshots.model.present) {
    return mlx.snapshots.model.incomplete
      ? '로컬 모델을 일부만 받았습니다. 설정 → 로컬 모델에서 이어받으세요.'
      : '로컬 모델이 아직 없습니다. 설정 → 로컬 모델에서 내려받으세요.'
  }
  if (mlx?.phase === 'failed') {
    return `모델 서버가 시작하지 못했습니다 — ${mlx.acceleration?.lastError ?? '로그를 확인하세요'}`
  }
  return '로컬 모델을 불러오는 중입니다.'
}

export function ModelBlockedNotice() {
  const reason = useLocalModelBlock()
  if (!reason) return null
  return (
    <p className="model-blocked">
      <AlertTriangle size={11} aria-hidden="true" /> {reason}
    </p>
  )
}

/** 모델 선택 — mlx 서버는 프로세스 하나가 모델 하나를 서빙하므로 교체는 재시작이다. */
export function ModelChoice({
  value, onChange, disabled
}: { value: string; onChange: (id: string) => void; disabled?: boolean }) {
  const catalog = useStore((state) => state.backendStatus?.mlx?.catalog) ?? []
  if (catalog.length === 0) return null
  return (
    <Listbox
      label="로컬 모델"
      value={value}
      options={catalog.map((entry, index) => ({
        value: entry.id,
        label: entry.label,
        hint: index === 0 ? '기본' : '고급'
      }))}
      onChange={onChange}
      disabled={disabled}
      compact
    />
  )
}
