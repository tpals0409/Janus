import { Brain, CircleCheck, Pause, Play, ShieldCheck } from 'lucide-react'
import { useStore } from '../store'
import { EmptyState, Status } from './ui'

type AgentDetailTab = '지침' | '스킬' | '컨텍스트' | '그래프'

export default function AgentOverview({ onOpen }: { onOpen: (tab: AgentDetailTab) => void }) {
  const profiles = useStore((state) => state.agentProfiles)
  const profileId = useStore((state) => state.selectedAgentProfileId)
  const assignments = useStore((state) => state.agentProfileSkills)
  const projects = useStore((state) => state.projects)
  const projectId = useStore((state) => state.projectId)
  const learnings = useStore((state) => state.projectLearnings)
  const learningError = useStore((state) => state.learningError)
  const setLearningStatus = useStore((state) => state.setProjectLearningStatus)
  const profile = profiles.find((item) => item.id === profileId)
  const project = projects.find((item) => item.id === projectId)
  const tasks = useStore((state) => state.tasks)

  if (!profile) return <EmptyState title="실행 프로필을 선택하세요" description="역할과 실행 방식을 한 화면에서 확인합니다." />

  const activeSkills = assignments.filter((item) => item.activation_mode !== 'off')
  const activeLearnings = learnings.filter((item) => item.status === 'active')
  const workingTasks = tasks.filter((item) => item.status === 'working' || item.status === 'preparing').length
  const attentionTasks = tasks.filter((item) => item.status === 'needs_you').length
  const reviewTasks = tasks.filter((item) => item.status === 'review').length
  return <section className="agent-overview">
    <div className="agent-overview__body">
      <div className="agent-dashboard-grid">
        <main className="agent-dashboard-main">
          <section className="agent-status-grid" aria-label="에이전트 운영 현황">
            {[
              { label: '진행 중', value: workingTasks, detail: '준비·실행 작업', icon: Play },
              { label: '확인 필요', value: attentionTasks, detail: '사용자 판단 대기', icon: Pause },
              { label: '검토 준비', value: reviewTasks, detail: '변경 확인 대기', icon: CircleCheck },
              { label: '적용 중인 기억', value: activeLearnings.length, detail: '자동 재사용', icon: Brain },
            ].map(({ label, value, detail, icon: Icon }) => <article key={label}>
              <Icon size={15} strokeWidth={1.5} />
              <div><span>{label}</span><small>{detail}</small></div>
              <strong>{value}</strong>
            </article>)}
          </section>

          <section className="agent-learning">
            <header><div><Brain size={15} /><span>{project?.name ?? '현재 프로젝트'}에서 배운 내용</span></div><Status tone={activeLearnings.length ? 'success' : 'muted'}>{activeLearnings.length}개 적용 중</Status></header>
            <p className="agent-learning__description">이 프로젝트에서 완료한 작업의 검증 방법과 명시적인 선호만 다음 세션에 자동 적용합니다.</p>
            {learningError && <p className="text-danger">{learningError}</p>}
            <div className="agent-learning__list">
              {learnings.slice(0, 6).map((item) => <article key={item.id} data-status={item.status}>
                <div className="min-w-0 flex-1"><div><strong>{item.title}</strong><small>근거 {item.evidence_count}회 · {item.status === 'active' ? '이 프로젝트에 적용' : '일시정지'}</small></div><p>{item.content}</p></div>
                <button type="button" title={item.status === 'active' ? '자동 적용 일시정지' : '다시 자동 적용'} aria-label={item.status === 'active' ? `${item.title} 일시정지` : `${item.title} 활성화`} onClick={() => void setLearningStatus(item.id, item.status === 'active' ? 'paused' : 'active')}>
                  {item.status === 'active' ? <Pause size={13} /> : <Play size={13} />}
                </button>
              </article>)}
              {learnings.length === 0 && <div className="agent-learning__empty">작업을 완료하면 재사용할 수 있는 방법을 자동으로 배웁니다.</div>}
            </div>
          </section>
        </main>

        <aside className="agent-dashboard-sidebar">
          <section className="agent-behavior">
          <header><ShieldCheck size={14} /><span>실행 방식</span></header>
          <dl>
            <div><dt>파일 수정</dt><dd>{profile.approval === 'ask' ? '먼저 묻기' : '자동'}</dd></div>
            <div><dt>워커 배치</dt><dd>{profile.worker_policy === 'autonomous' ? '필요할 때 자동' : '제한'}</dd></div>
            <div><dt>활성 스킬</dt><dd>{activeSkills.length}개</dd></div>
            <div><dt>최대 작업 시간</dt><dd>{Math.round(profile.budget.dispatch.time_limit_ms / 60_000)}분</dd></div>
          </dl>
          </section>
          <section className="agent-overview__role">
            <span className="task-label">역할과 판단 기준</span>
            <p>{profile.system_prompt.trim() || '기본 Janus 오케스트레이터 지침을 사용합니다.'}</p>
            <button type="button" onClick={() => onOpen('지침')}>전체 지침 보기</button>
          </section>
        </aside>
      </div>
    </div>
  </section>
}
