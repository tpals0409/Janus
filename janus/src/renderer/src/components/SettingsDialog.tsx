import { useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import type { RuntimeSettingsSnapshot, RuntimeSettingsValues } from '../types'
import { Dialog } from './ui'

const MTP_LABEL: Record<RuntimeSettingsValues['mtpPolicy'], string> = {
  required: '필수 — MTP 드래프터 없이는 기동하지 않음',
  preferred: '선호 — 실패 시 일반 디코딩으로 폴백',
  off: '끔 — 항상 일반 디코딩'
}

/** 모델 런타임 손잡이 설정. 저장하면 영향을 받는 서비스만 재시작해 적용한다. */
export default function SettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [snapshot, setSnapshot] = useState<RuntimeSettingsSnapshot | null>(null)
  const [draft, setDraft] = useState<RuntimeSettingsValues | null>(null)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setResult(null)
    void window.janus?.runtimeSettingsGet?.().then((value) => {
      setSnapshot(value)
      setDraft(value.effective)
    })
  }, [open])

  const changed = snapshot && draft && (
    draft.mtpPolicy !== snapshot.effective.mtpPolicy
    || draft.modelSlots !== snapshot.effective.modelSlots
    || draft.apc !== snapshot.effective.apc
  )
  const restartTargets = snapshot && draft
    ? [
        ...(draft.mtpPolicy !== snapshot.effective.mtpPolicy || draft.apc !== snapshot.effective.apc
          ? ['모델 서버'] : []),
        ...(draft.modelSlots !== snapshot.effective.modelSlots ? ['백엔드'] : [])
      ]
    : []

  const save = async () => {
    if (!draft || saving) return
    setSaving(true)
    setResult(null)
    try {
      const applied = await window.janus?.runtimeSettingsSet?.(draft)
      const restarted = applied?.restarted ?? []
      setResult(restarted.length > 0
        ? `저장했습니다 — ${restarted.map((label) => label === 'mlx' ? '모델 서버' : '백엔드').join('·')} 재시작 중`
        : '저장했습니다 — 재시작 없이 적용됨')
      const refreshed = await window.janus?.runtimeSettingsGet?.()
      if (refreshed) {
        setSnapshot(refreshed)
        setDraft(refreshed.effective)
      }
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} title="설정" onClose={onClose} className="settings-dialog">
      <header className="settings-dialog__header">
        <strong>설정</strong>
        <span>모델 런타임 — 저장 시 영향을 받는 서비스만 재시작됩니다.</span>
      </header>
      {!draft || !snapshot ? (
        <div className="settings-dialog__loading"><Loader2 size={14} className="animate-spin" /> 불러오는 중</div>
      ) : (
        <div className="settings-dialog__body">
          <label className="settings-field">
            <span className="settings-field__name">MTP (speculative decoding)</span>
            <select
              value={draft.mtpPolicy}
              disabled={snapshot.locked.mtpPolicy}
              onChange={(event) => setDraft({
                ...draft, mtpPolicy: event.target.value as RuntimeSettingsValues['mtpPolicy']
              })}
            >
              {(['required', 'preferred', 'off'] as const).map((policy) => (
                <option key={policy} value={policy}>{MTP_LABEL[policy]}</option>
              ))}
            </select>
            {snapshot.locked.mtpPolicy && <em>JANUS_MTP_POLICY 환경변수로 고정됨</em>}
          </label>

          <label className="settings-field">
            <span className="settings-field__name">모델 동시 생성 슬롯</span>
            <input
              type="number"
              min={1}
              max={8}
              value={draft.modelSlots}
              disabled={snapshot.locked.modelSlots}
              onChange={(event) => setDraft({ ...draft, modelSlots: Number(event.target.value) })}
            />
            <em>
              오케스트레이터·워커가 동시에 생성할 수 있는 수. 48GB 기준 권장 3 —
              운영 대시보드의 vram_sizing이 recommended일 때만 올리세요.
              {snapshot.locked.modelSlots && ' (JANUS_MODEL_SLOTS 환경변수로 고정됨)'}
            </em>
          </label>

          <label className="settings-field settings-field--row">
            <input
              type="checkbox"
              checked={draft.apc}
              disabled={snapshot.locked.apc}
              onChange={(event) => setDraft({ ...draft, apc: event.target.checked })}
            />
            <span className="settings-field__name">프롬프트 캐시 (APC)</span>
            <em>
              반복되는 컨텍스트 prefix를 재사용해 응답 시작을 앞당깁니다.
              {snapshot.locked.apc && ' (JANUS_APC 환경변수로 고정됨)'}
            </em>
          </label>

          {changed && restartTargets.length > 0 && (
            <p className="settings-dialog__warning">
              저장하면 {restartTargets.join('과 ')}가 재시작됩니다. 실행 중인 턴이 있으면 중단될 수 있습니다.
            </p>
          )}
          {result && <p className="settings-dialog__result">{result}</p>}

          <footer className="settings-dialog__actions">
            <button type="button" onClick={onClose} className="task-quiet-action">닫기</button>
            <button
              type="button"
              onClick={() => void save()}
              disabled={!changed || saving}
              className="task-primary-action"
            >
              {saving ? '적용 중…' : '저장'}
            </button>
          </footer>
        </div>
      )}
    </Dialog>
  )
}
