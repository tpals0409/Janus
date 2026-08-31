import { useEffect, useState } from 'react'
import { Loader2, Settings } from 'lucide-react'
import { useAgentProfileOptions, useStore } from '../store'
import ModelSetup, { ModelChoice } from './ModelSetup'
import { Listbox } from './ui'
import { setThemePref, themePref, type ThemePref } from '../theme'
import type { ModelProfile, RuntimeSettingsSnapshot, RuntimeSettingsValues } from '../types'

const THEME_LABEL: Record<ThemePref, string> = {
  system: '시스템 따름',
  dark: '다크',
  light: '라이트'
}

const MTP_LABEL: Record<RuntimeSettingsValues['mtpPolicy'], string> = {
  required: '필수 — MTP 드래프터 없이는 기동하지 않음',
  preferred: '선호 — 실패 시 일반 디코딩으로 폴백',
  off: '끔 — 항상 일반 디코딩'
}

// 구독형 실행기의 손잡이. effort 어휘가 CLI마다 달라 서버도 provider별로만 받는다
// (cli_runner.CLI_EFFORTS) — 여기 목록이 그 계약의 화면 쪽 절반이다.
const SUBSCRIPTION_CHOICES = {
  claude_code: {
    models: [
      { value: '', label: '기본값 — CLI 설정 따름' },
      { value: 'fable', label: 'Fable' },
      { value: 'opus', label: 'Opus' },
      { value: 'sonnet', label: 'Sonnet' },
      { value: 'haiku', label: 'Haiku' }
    ],
    efforts: ['low', 'medium', 'high', 'xhigh', 'max']
  },
  codex: {
    models: [
      { value: '', label: '기본값 — CLI 설정 따름' },
      { value: 'gpt-5.6-sol', label: 'GPT-5.6 Sol' },
      { value: 'gpt-5.6-codex', label: 'GPT-5.6 Codex' },
      { value: 'gpt-5.6', label: 'GPT-5.6' }
    ],
    efforts: ['minimal', 'low', 'medium', 'high']
  }
} as const

/** 구독형 CLI의 모델·사고 강도. 저장 즉시 다음 턴부터 적용된다. */
function SubscriptionModelFields({ profile }: { profile: ModelProfile }) {
  const update = useStore((state) => state.updateModelProfileConfig)
  const busy = useStore((state) => state.profileBusy)
  const error = useStore((state) => state.profileError)
  const choices = SUBSCRIPTION_CHOICES[profile.provider as 'claude_code' | 'codex']
  if (!choices) return null
  // config는 서버가 항상 채우지만, 설정 화면이 낡은 스냅샷 하나로 죽으면 안 된다.
  const config = profile.config ?? {}
  const model = String(config.model ?? '')
  const effort = String(config.effort ?? '')
  return (
    <>
      <div className="settings-field">
        <span className="settings-field__name">모델 ({profile.name})</span>
        <Listbox
          label={`모델 (${profile.name})`}
          value={model}
          options={choices.models.map((item) => ({ value: item.value, label: item.label }))}
          onChange={(value) => void update(profile.id, { model: value, effort })}
          disabled={busy}
          compact
        />
        <em>구독 플랜이 주는 모델만 고를 수 있습니다. 새 턴부터 적용됩니다.</em>
      </div>
      <div className="settings-field">
        <span className="settings-field__name">사고 강도</span>
        <Listbox
          label="사고 강도"
          value={effort}
          options={[
            { value: '', label: '기본값 — CLI 설정 따름' },
            ...choices.efforts.map((level) => ({ value: level, label: level }))
          ]}
          onChange={(value) => void update(profile.id, { model, effort: value })}
          disabled={busy}
          compact
        />
        <em>높일수록 더 오래 생각하고 더 많은 사용량을 씁니다.</em>
      </div>
      {error && <p className="settings-dialog__warning">{error}</p>}
    </>
  )
}

/** 설정 화면 — 모델 선택은 즉시 적용, 런타임 손잡이는 저장 시 해당 서비스만 재시작. */
export default function SettingsPage() {
  const agentProfiles = useStore((state) => state.agentProfiles)
  const modelProfiles = useStore((state) => state.modelProfiles)
  const selectedAgentProfileId = useStore((state) => state.selectedAgentProfileId)
  const selectAgentProfile = useStore((state) => state.selectAgentProfile)
  const profileOptions = useAgentProfileOptions()
  const selectedModelProfile = modelProfiles.find((model) =>
    model.id === agentProfiles.find((profile) => profile.id === selectedAgentProfileId)?.model_profile_id
  )
  const selectedProvider = selectedModelProfile?.provider ?? 'local'
  const [snapshot, setSnapshot] = useState<RuntimeSettingsSnapshot | null>(null)
  const [draft, setDraft] = useState<RuntimeSettingsValues | null>(null)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [theme, setTheme] = useState<ThemePref>(themePref)

  useEffect(() => {
    void window.janus?.runtimeSettingsGet?.().then((value) => {
      setSnapshot(value)
      setDraft(value.effective)
    })
  }, [])

  const changed = snapshot && draft && (
    draft.localServer !== snapshot.effective.localServer
    || draft.modelId !== snapshot.effective.modelId
    || draft.mtpPolicy !== snapshot.effective.mtpPolicy
    || draft.modelSlots !== snapshot.effective.modelSlots
    || draft.apc !== snapshot.effective.apc
  )
  const restartTargets = snapshot && draft
    ? [
        ...(draft.localServer !== snapshot.effective.localServer
          || draft.modelId !== snapshot.effective.modelId
          || draft.mtpPolicy !== snapshot.effective.mtpPolicy
          || draft.apc !== snapshot.effective.apc
          ? ['모델 서버'] : []),
        ...(draft.modelSlots !== snapshot.effective.modelSlots
          || draft.modelId !== snapshot.effective.modelId ? ['백엔드'] : [])
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
            <div className="settings-field">
              <span className="settings-field__name">모델 (에이전트 프로필)</span>
              <Listbox
                label="모델 (에이전트 프로필)"
                value={selectedAgentProfileId}
                options={profileOptions}
                onChange={selectAgentProfile}
                compact
              />
              <em>즉시 저장되며 새 시도·새 대화부터 적용됩니다. 진행 중인 세션은 시작 시점의 모델을 유지합니다.</em>
            </div>
          ) : (
            <p className="text-[11px] text-faint">프로필을 불러오는 중입니다.</p>
          )}
          {selectedModelProfile && selectedProvider !== 'local' && (
            <SubscriptionModelFields profile={selectedModelProfile} />
          )}
        </section>

        <section className="task-card settings-section">
          <h3>화면</h3>
          <div className="settings-field">
            <span className="settings-field__name">테마</span>
            <Listbox
              label="테마"
              value={theme}
              options={(['system', 'dark', 'light'] as const).map((pref) => ({
                value: pref, label: THEME_LABEL[pref]
              }))}
              onChange={(pref) => {
                setTheme(pref)
                setThemePref(pref)
              }}
              compact
            />
            <em>바로 적용돼요. 시스템 따름은 macOS 화면 모드를 따라가요.</em>
          </div>
        </section>

        <section className="task-card settings-section">
          <h3>로컬 모델 런타임</h3>
          {selectedProvider !== 'local' && (
            <p className="settings-section__note">
              현재 선택된 모델은 구독형이라 이 섹션의 영향을 받지 않습니다.
              구독형 위주로 쓴다면 로컬 모델 서버를 꺼서 메모리(약 16GB)를 아낄 수 있습니다.
            </p>
          )}
          {!draft || !snapshot ? (
            <div className="settings-dialog__loading"><Loader2 size={14} className="animate-spin" /> 불러오는 중</div>
          ) : (
            <>
              <div className="settings-field">
                <span className="settings-field__name">로컬 모델</span>
                <ModelChoice
                  value={draft.modelId}
                  onChange={(modelId) => setDraft({ ...draft, modelId })}
                  disabled={!draft.localServer}
                />
                <em>바꾸면 저장 시 모델 서버가 그 모델로 다시 뜹니다.</em>
              </div>

              <ModelSetup disabled={!draft.localServer} />

              <label className="settings-field settings-field--row">
                <input
                  type="checkbox"
                  checked={draft.localServer}
                  onChange={(event) => setDraft({ ...draft, localServer: event.target.checked })}
                />
                <span className="settings-field__name">로컬 모델 서버</span>
                <em>
                  끄면 로컬 27B를 띄우지 않아 메모리를 아낍니다. 로컬 모델(Janus Local)
                  프로필은 이 서버가 켜져 있어야 실행됩니다.
                </em>
              </label>

              <div className="settings-field">
                <span className="settings-field__name">MTP (speculative decoding)</span>
                <Listbox
                  label="MTP (speculative decoding)"
                  value={draft.mtpPolicy}
                  options={(['required', 'preferred', 'off'] as const).map((policy) => ({
                    value: policy, label: MTP_LABEL[policy]
                  }))}
                  onChange={(policy) => setDraft({ ...draft, mtpPolicy: policy })}
                  disabled={snapshot.locked.mtpPolicy || !draft.localServer}
                  compact
                />
                {snapshot.locked.mtpPolicy && <em>JANUS_MTP_POLICY 환경변수로 고정됨</em>}
              </div>

              <label className="settings-field">
                <span className="settings-field__name">모델 동시 생성 슬롯</span>
                <input
                  type="number"
                  min={1}
                  max={8}
                  value={draft.modelSlots}
                  disabled={snapshot.locked.modelSlots || !draft.localServer}
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
                  disabled={snapshot.locked.apc || !draft.localServer}
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
