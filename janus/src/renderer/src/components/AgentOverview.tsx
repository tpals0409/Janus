import { BookOpen, Boxes, Braces, PackageOpen, ShieldCheck, Wrench } from 'lucide-react'
import { useStore } from '../store'
import { EmptyState, Status } from './ui'

type AgentDetailTab = '프롬프트' | '스킬' | '컨텍스트 정책' | '그래프'

export default function AgentOverview({ onOpen }: { onOpen: (tab: AgentDetailTab) => void }) {
  const profiles = useStore((state) => state.agentProfiles)
  const profileId = useStore((state) => state.selectedAgentProfileId)
  const assignments = useStore((state) => state.agentProfileSkills)
  const profile = profiles.find((item) => item.id === profileId)

  if (!profile) return <EmptyState title="실행 프로필을 선택하세요" description="역할과 실행 계약을 한 화면에서 확인합니다." />

  const activeSkills = assignments.filter((item) => item.activation_mode !== 'off')
  const context = profile.context_policy
  const summary = [
    { icon: <PackageOpen size={15} />, label: '활성 스킬', value: `${activeSkills.length}개`, detail: activeSkills.slice(0, 3).map((item) => item.name).join(' · ') || '활성화된 스킬 없음', tab: '스킬' as const },
    { icon: <BookOpen size={15} />, label: '컨텍스트', value: `${context.max_chars.toLocaleString()}자`, detail: `최근 ${context.recent_blocks}블록 · 요약 ${context.summary_max_chars.toLocaleString()}자`, tab: '컨텍스트 정책' as const },
    { icon: <Wrench size={15} />, label: '도구', value: `${profile.tools.length}개`, detail: profile.tools.slice(0, 4).join(' · ') || '도구 없음', tab: '프롬프트' as const },
    { icon: <Boxes size={15} />, label: '워커', value: profile.worker_policy, detail: `최대 ${profile.budget.workers.total_limit}명 · 단계 ${profile.max_steps}`, tab: '그래프' as const }
  ]

  return (
    <section className="agent-overview">
      <header className="agent-overview__hero">
        <div className="agent-overview__mark" aria-hidden="true"><Braces size={18} /></div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2>{profile.name}</h2>
            <Status tone="success">사용 가능</Status>
          </div>
          <p>{profile.description || '작업을 분석하고 필요한 실행 흐름을 구성하는 로컬 에이전트입니다.'}</p>
        </div>
        <button type="button" className="task-primary-action" onClick={() => onOpen('프롬프트')}>프롬프트 편집</button>
      </header>

      <div className="agent-overview__body">
        <section className="agent-overview__role">
          <span className="task-label">역할과 판단 기준</span>
          <p>{profile.system_prompt.trim() || '기본 Janus 오케스트레이터 지침을 사용합니다.'}</p>
          <button type="button" onClick={() => onOpen('프롬프트')}>전체 프롬프트 보기</button>
        </section>

        <div className="agent-overview__summary">
          {summary.map((item) => (
            <button key={item.label} type="button" onClick={() => onOpen(item.tab)}>
              <span>{item.icon}</span>
              <div><small>{item.label}</small><strong>{item.value}</strong><p>{item.detail}</p></div>
            </button>
          ))}
        </div>
      </div>

      <aside className="agent-overview__contract">
        <div><ShieldCheck size={14} /><span>실행 계약</span></div>
        <dl>
          <div><dt>프로필 ID</dt><dd>{profile.id}</dd></div>
          <div><dt>모델</dt><dd>{profile.model_profile_id}</dd></div>
          <div><dt>승인 정책</dt><dd>{profile.approval}</dd></div>
          <div><dt>토큰 한도</dt><dd>{profile.budget.dispatch.token_limit.toLocaleString()}</dd></div>
          <div><dt>시간 한도</dt><dd>{Math.round(profile.budget.dispatch.time_limit_ms / 1000)}초</dd></div>
        </dl>
        <p>변경 사항은 이 프로필을 사용하는 새 작업 시도부터 적용됩니다.</p>
      </aside>
    </section>
  )
}
