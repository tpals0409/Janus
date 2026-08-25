import { useEffect, useState } from 'react'
import { Command, Save } from 'lucide-react'
import { useStore } from '../store'
import { Button, EmptyState, Section, Status } from './ui'

export default function PromptEditor() {
  const profiles = useStore((state) => state.agentProfiles)
  const profileId = useStore((state) => state.selectedAgentProfileId)
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
      <div className="workspace-actionbar">
          <Status tone={dirty ? 'warning' : 'success'}>{dirty ? '변경됨' : '저장됨'}</Status>
          <Button onClick={() => void save()} disabled={!dirty || busy}>
            <Save size={13} strokeWidth={1.5} /> {busy ? '저장 중…' : '저장'}
          </Button>
      </div>

      {error && <div className="error-strip">{error}</div>}

      <div className="workspace-split">
        <main className="workspace-main p-4">
          <div className="flex h-full min-h-0 flex-col gap-3">
            <div className="technical-editor min-h-0 flex-1">
              <div className="technical-editor__bar">
                Janus 기본 지침 <span className="ml-auto">내장 · 읽기 전용</span>
              </div>
              <textarea readOnly value={profile.base_system_prompt ?? ''} aria-label="Janus 기본 지침" />
            </div>
            <div className="technical-editor min-h-0 flex-1">
              <div className="technical-editor__bar">
                코딩 규칙 <span className="ml-auto">내장 · 항상 적용</span>
              </div>
              <textarea readOnly value={profile.coding_rules_prompt ?? ''} aria-label="코딩 규칙" />
            </div>
            <div className="technical-editor min-h-0 flex-1">
              <div className="technical-editor__bar">
                프로필 추가 지침
                <span className="ml-auto">{prompt ? prompt.split('\n').length : 0}줄 · {prompt.length.toLocaleString()}자</span>
              </div>
              <textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder="기본 Janus 지침에 덧붙일 프로필별 강조사항이 있을 때만 입력하세요."
                aria-label={`${profile.name} 추가 지침`}
              />
            </div>
          </div>
        </main>

        <aside className="workspace-inspector">
          <Section label="적용 시점" description="저장하면 다음 작업부터 적용됩니다.">
          <p className="text-[11px] leading-relaxed text-muted">
            Janus 기본 지침과 코딩 규칙은 항상 적용됩니다. 저장한 추가 지침은 <strong className="text-fg">{profile.name}</strong>을 사용하는 새 Task 시도부터 뒤에 이어붙습니다.
          </p>
          </Section>
          <Section label="실행 방식">
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
        {!prompt.trim() && <span className="ml-auto text-muted">추가 지침 없음 · 기본 지침과 코딩 규칙 적용</span>}
      </footer>
    </section>
  )
}
