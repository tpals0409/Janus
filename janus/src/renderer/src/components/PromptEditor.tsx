import { useEffect, useState } from 'react'
import { Bot, Command, Save } from 'lucide-react'
import { useStore } from '../store'
import { Button, EmptyState, Section, Select, Status } from './ui'

export default function PromptEditor() {
  const profiles = useStore((state) => state.agentProfiles)
  const profileId = useStore((state) => state.selectedAgentProfileId)
  const selectProfile = useStore((state) => state.selectAgentProfile)
  const updateProfile = useStore((state) => state.updateAgentProfile)
  const busy = useStore((state) => state.profileBusy)
  const error = useStore((state) => state.profileError)
  const profile = profiles.find((item) => item.id === profileId) ?? null
  const [prompt, setPrompt] = useState('')

  useEffect(() => setPrompt(profile?.system_prompt ?? ''), [profile?.id, profile?.system_prompt])

  const dirty = Boolean(profile && prompt !== profile.system_prompt)
  const save = async () => {
    if (!profile || !dirty) return
    await updateProfile(profile.id, { system_prompt: prompt })
  }

  useEffect(() => {
    const saveShortcut = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 's') return
      event.preventDefault()
      void save()
    }
    window.addEventListener('keydown', saveShortcut)
    return () => window.removeEventListener('keydown', saveShortcut)
  })

  if (!profile) {
    return <EmptyState title="실행 프로필을 선택하세요" description="프롬프트는 프로필별로 저장됩니다." />
  }

  return (
    <section className="workspace-surface">
      <header className="workspace-toolbar">
        <div className="workspace-toolbar__icon">
          <Bot size={16} strokeWidth={1.5} />
        </div>
        <div className="workspace-toolbar__title">
          <h2>시스템 프롬프트</h2>
          <p>역할·판단 원칙·완료 기준</p>
        </div>
        <div className="workspace-toolbar__actions">
          <Select
            value={profileId}
            onChange={(event) => selectProfile(event.target.value)}
            disabled={dirty || busy}
            title={dirty ? '변경을 저장한 뒤 프로필을 바꾸세요' : undefined}
            aria-label="실행 프로필"
            className="workspace-profile-select"
          >
            {profiles.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </Select>
          <Status tone={dirty ? 'warning' : 'success'}>{dirty ? '변경됨' : '저장됨'}</Status>
          <Button onClick={() => void save()} disabled={!dirty || busy}>
            <Save size={13} strokeWidth={1.5} /> {busy ? '저장 중…' : '저장'}
          </Button>
        </div>
      </header>

      {error && <div className="error-strip">{error}</div>}

      <div className="workspace-split">
        <main className="workspace-main p-4">
          <div className="technical-editor h-full">
          <div className="technical-editor__bar">
            AGENTPROFILE / SYSTEM
            <span className="ml-auto">{prompt ? prompt.split('\n').length : 0}줄 · {prompt.length.toLocaleString()}자</span>
          </div>
          <textarea
            autoFocus
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder={'예: 당신은 로컬 코드베이스를 수정하는 에이전트입니다.\n변경 전 관련 코드를 읽고, 완료 후 테스트 결과를 보고하세요.'}
            aria-label={`${profile.name} 시스템 프롬프트`}
          />
        </div>
        </main>

        <aside className="workspace-inspector">
          <Section label="적용 범위" description="저장된 프롬프트는 새 Task 시도부터 적용됩니다.">
          <p className="text-[11px] leading-relaxed text-muted">
            저장한 프롬프트는 <strong className="text-fg">{profile.name}</strong>을 사용하는 새 Task 시도부터 시스템 메시지에 포함됩니다.
          </p>
          </Section>
          <Section label="실행 계약">
          <dl className="space-y-2 text-[10px]">
            <div className="flex justify-between"><dt className="text-faint">프로필 ID</dt><dd className="max-w-[120px] truncate font-mono text-muted">{profile.id}</dd></div>
            <div className="flex justify-between"><dt className="text-faint">워커 정책</dt><dd className="text-muted">{profile.worker_policy}</dd></div>
            <div className="flex justify-between"><dt className="text-faint">최대 단계</dt><dd className="font-mono text-muted">{profile.max_steps}</dd></div>
          </dl>
          </Section>
        </aside>
      </div>

      <footer className="workspace-footer">
        <Command size={10} /> <span>⌘S 또는 Ctrl+S로 저장</span>
        {!prompt.trim() && <span className="ml-auto text-warn">빈 프롬프트는 기본 오케스트레이터 지침으로 대체됩니다.</span>}
      </footer>
    </section>
  )
}
