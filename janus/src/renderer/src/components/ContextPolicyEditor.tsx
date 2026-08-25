import { useEffect, useState } from 'react'
import { Brain, Pause, Play, Save } from 'lucide-react'
import type { ContextPolicy } from '../types'
import { useStore } from '../store'
import { Button, Checkbox, EmptyState, Field, Input, Section, Status } from './ui'

const fallback: ContextPolicy = {
  max_chars: 24_000,
  recent_blocks: 8,
  summary_max_chars: 4_000,
  include_task_objective: true,
  include_acceptance: true,
  include_workspace_root: true,
}

function NumberSetting({
  label, value, min, max, suffix, onChange,
}: {
  label: string; value: number; min: number; max: number; suffix: string
  onChange: (value: number) => void
}) {
  return (
    <Field label={label}>
      <div className="flex items-center gap-2">
        <Input
          type="number" min={min} max={max} value={value}
          onChange={(event) => onChange(Number(event.target.value))}
          className="min-w-0 flex-1 font-mono"
        />
        <span className="w-6 text-[10px] text-faint">{suffix}</span>
      </div>
    </Field>
  )
}

export default function ContextPolicyEditor() {
  const profiles = useStore((state) => state.agentProfiles)
  const profileId = useStore((state) => state.selectedAgentProfileId)
  const updateProfile = useStore((state) => state.updateAgentProfile)
  const busy = useStore((state) => state.profileBusy)
  const error = useStore((state) => state.profileError)
  const projects = useStore((state) => state.projects)
  const projectId = useStore((state) => state.projectId)
  const learnings = useStore((state) => state.projectLearnings)
  const setLearningStatus = useStore((state) => state.setProjectLearningStatus)
  const profile = profiles.find((item) => item.id === profileId) ?? null
  const project = projects.find((item) => item.id === projectId)
  const [policy, setPolicy] = useState<ContextPolicy>(fallback)

  useEffect(() => setPolicy(profile?.context_policy ?? fallback), [profile?.id, profile?.context_policy])

  if (!profile) return <EmptyState title="실행 프로필을 선택하세요" description="컨텍스트 정책은 프로필별로 적용됩니다." />
  const dirty = JSON.stringify(policy) !== JSON.stringify(profile.context_policy)
  const patch = (changes: Partial<ContextPolicy>) => setPolicy((current) => ({ ...current, ...changes }))

  const sources: Array<[keyof ContextPolicy, string, string]> = [
    ['include_task_objective', '작업 목표', 'Task의 목표를 새 세션의 고정 컨텍스트로 전달'],
    ['include_acceptance', '수용 검증', '완료 판정에 사용할 검증 명령을 전달'],
    ['include_workspace_root', '작업 공간 경로', '에이전트가 읽고 수정할 원본 저장소 경로를 전달'],
  ]

  return (
    <section className="workspace-surface">
      <div className="workspace-actionbar">
          <Status tone={dirty ? 'warning' : 'success'}>{dirty ? '변경됨' : '저장됨'}</Status>
          <Button onClick={() => void updateProfile(profile.id, { context_policy: policy })} disabled={!dirty || busy}>
            <Save size={13} strokeWidth={1.5} /> {busy ? '저장 중…' : '저장'}
          </Button>
      </div>

      {error && <div className="error-strip">{error}</div>}

      <div className="workspace-split">
        <main className="workspace-main">
            <Section label={`${project?.name ?? '현재 프로젝트'}에서 배운 내용`} description="완료된 작업에서 확인한 방법을 다음 세션에 자동 적용합니다.">
              <div className="memory-learning-list">
                {learnings.map((item) => <div key={item.id} data-status={item.status}>
                  <Brain size={13} />
                  <span><strong>{item.title}</strong><small>{item.content}</small></span>
                  <em>근거 {item.evidence_count}회</em>
                  <button type="button" aria-label={item.status === 'active' ? `${item.title} 일시정지` : `${item.title} 활성화`} onClick={() => void setLearningStatus(item.id, item.status === 'active' ? 'paused' : 'active')}>
                    {item.status === 'active' ? <Pause size={12} /> : <Play size={12} />}
                  </button>
                </div>)}
                {learnings.length === 0 && <p className="text-[10.5px] text-faint">아직 배운 내용이 없습니다. 작업을 완료하면 여기에 추가됩니다.</p>}
              </div>
            </Section>
            <Section label="용량과 압축" description="한도를 넘으면 오래된 대화 블록부터 요약합니다.">
              <div className="grid grid-cols-3 gap-4">
                <NumberSetting label="최대 컨텍스트" value={policy.max_chars} min={8_000} max={200_000} suffix="자" onChange={(max_chars) => patch({ max_chars })} />
                <NumberSetting label="최근 보존 블록" value={policy.recent_blocks} min={1} max={64} suffix="개" onChange={(recent_blocks) => patch({ recent_blocks })} />
                <NumberSetting label="요약 최대" value={policy.summary_max_chars} min={500} max={16_000} suffix="자" onChange={(summary_max_chars) => patch({ summary_max_chars })} />
              </div>
            </Section>

            <Section label="고정 소스" description="새 세션의 안정 prefix에 포함할 정보를 선택합니다.">
              <div>
                {sources.map(([key, label, description]) => (
                  <Checkbox
                    key={key}
                    checked={Boolean(policy[key])}
                    onChange={(event) => patch({ [key]: event.target.checked })}
                    label={<strong className="block text-[11px] font-medium text-fg">{label}</strong>}
                    description={description}
                  />
                ))}
              </div>
            </Section>
        </main>

          <aside className="workspace-inspector">
            <Section label="컨텍스트 용량">
            <div className="context-meter mt-2">
              <span style={{ width: `${Math.min(100, Math.max(8, policy.summary_max_chars / policy.max_chars * 100))}%` }} />
            </div>
            <p className="mt-2 font-mono text-[10px] text-faint">요약 예산 {Math.round(policy.summary_max_chars / policy.max_chars * 100)}% · 최근 {policy.recent_blocks}블록 보존</p>
            </Section>
            <Section label="압축 동작">
            <p className="text-[10px] leading-relaxed text-muted">
              프롬프트와 선택한 고정 소스는 안정 prefix로 유지하고, 대화가 한도를 넘으면 이전 블록만 요약합니다.
            </p>
            </Section>
            <Section label="스킬 불러오기">
            <div className="text-[10px] leading-relaxed text-faint">
              스킬은 이 정책과 별개로 메타데이터만 선주입되고, 필요할 때만 본문을 로드합니다.
            </div>
            </Section>
          </aside>
      </div>
    </section>
  )
}
