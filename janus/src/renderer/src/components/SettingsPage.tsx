import { useEffect, useState } from 'react'
import { Loader2, Settings } from 'lucide-react'
import { useStore } from '../store'
import type { RuntimeSettingsSnapshot, RuntimeSettingsValues } from '../types'

const MTP_LABEL: Record<RuntimeSettingsValues['mtpPolicy'], string> = {
  required: '필수 — MTP 드래프터 없이는 기동하지 않음',
  preferred: '선호 — 실패 시 일반 디코딩으로 폴백',
  off: '끔 — 항상 일반 디코딩'
}

/** 설정 화면 — 모델 선택은 즉시 적용, 런타임 손잡이는 저장 시 해당 서비스만 재시작. */
export default function SettingsPage() {
  const agentProfiles = useStore((state) => state.agentProfiles)
  const selectedAgentProfileId = useStore((state) => state.selectedAgentProfileId)
  const selectAgentProfile = useStore((state) => state.selectAgentProfile)
  const [snapshot, setSnapshot] = useState<RuntimeSettingsSnapshot | null>(null)
  const [draft, setDraft] = useState<RuntimeSettingsValues | null>(null)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<string | null>(null)

  useEffect(() => {
    void window.janus?.runtimeSettingsGet?.().then((value) => {
      setSnapshot(value)
      setDraft(value.effective)
    })
  }, [])

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
    <main className="settings-page min-w-0 flex-1">
      <header className="settings-page__header">
        <Settings size={14} className="text-muted" />
        <div>
          <h2>설정</h2>
          <p>모델 선택은 즉시 적용되고, 런타임 손잡이는 저장 시 영향을 받는 서비스만 재시작됩니다.</p>
        </div>
      </header>

      <div className="settings-page__body">
        <section className="task-card settings-section">
          <h3>모델</h3>
          {agentProfiles.length > 0 ? (
            <label className="settings-field">
              <span className="settings-field__name">모델 (에이전트 프로필)</span>
              <select
                value={selectedAgentProfileId}
                onChange={(event) => selectAgentProfile(event.target.value)}
              >
                {agentProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>{profile.name}</option>
                ))}
              </select>
              <em>즉시 저장되며 새 시도·새 대화부터 적용됩니다. 진행 중인 세션은 시작 시점의 모델을 유지합니다.</em>
            </label>
          ) : (
            <p className="text-[11px] text-faint">프로필을 불러오는 중입니다.</p>
          )}
        </section>

        <section className="task-card settings-section">
          <h3>로컬 모델 런타임</h3>
          {!draft || !snapshot ? (
            <div className="settings-dialog__loading"><Loader2 size={14} className="animate-spin" /> 불러오는 중</div>
          ) : (
            <>
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

              <footer className="settings-section__actions">
                <button
                  type="button"
                  onClick={() => void save()}
                  disabled={!changed || saving}
                  className="task-primary-action"
                >
                  {saving ? '적용 중…' : '저장'}
                </button>
              </footer>
            </>
          )}
        </section>
      </div>
    </main>
  )
}
