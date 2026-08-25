import { ArrowRight, BadgeCheck, Brain, FilePenLine, Pause, Play, Search, ShieldCheck, Users } from 'lucide-react'
import { useStore } from '../store'
import { EmptyState, Status } from './ui'

type AgentDetailTab = '지침' | '능력' | '기억과 컨텍스트' | '작업 흐름'

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

  if (!profile) return <EmptyState title="실행 프로필을 선택하세요" description="역할과 실행 방식을 한 화면에서 확인합니다." />

  const activeSkills = assignments.filter((item) => item.activation_mode !== 'off')
  const activeLearnings = learnings.filter((item) => item.status === 'active')
  const flow = [
    { icon: Search, label: '요청 분석', detail: '범위와 검증 조건 확인', tab: '지침' as const },
    { icon: Users, label: '워커 배치', detail: profile.worker_policy === 'autonomous' ? `필요할 때 최대 ${profile.budget.workers.total_limit}명` : '제한된 배치', tab: '작업 흐름' as const },
    { icon: FilePenLine, label: '수정', detail: `${profile.tools.length}개 도구로 작업`, tab: '능력' as const },
    { icon: BadgeCheck, label: '검증과 검토', detail: '완료 전 결과 확인', tab: '작업 흐름' as const },
  ]

  return <section className="agent-overview">
    <header className="agent-overview__hero">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2"><h2>{profile.name}</h2><Status tone="success">사용 가능</Status></div>
        <p>{profile.description || '요청을 분석하고 필요한 워커를 배치하는 로컬 코딩 에이전트입니다.'}</p>
      </div>
      <button type="button" className="task-primary-action" onClick={() => onOpen('지침')}>지침 편집</button>
    </header>

    <div className="agent-overview__body">
      <section className="agent-execution-flow" aria-label="에이전트 실행 흐름">
        <header><span>작업 하나를 완료하는 방법</span><button onClick={() => onOpen('작업 흐름')}>흐름 설정</button></header>
        <div>{flow.map(({ icon: Icon, label, detail, tab }, index) => <div key={label} className="agent-execution-flow__step">
          <button type="button" onClick={() => onOpen(tab)}><Icon size={15} /><span><strong>{label}</strong><small>{detail}</small></span></button>
          {index < flow.length - 1 && <ArrowRight size={13} aria-hidden="true" />}
        </div>)}</div>
      </section>

      <div className="agent-overview__columns">
        <section className="agent-overview__role">
          <span className="task-label">역할과 판단 기준</span>
          <p>{profile.system_prompt.trim() || '기본 Janus 오케스트레이터 지침을 사용합니다.'}</p>
          <button type="button" onClick={() => onOpen('지침')}>전체 지침 보기</button>
        </section>
        <section className="agent-behavior">
          <header><ShieldCheck size={14} /><span>실행 방식</span></header>
          <dl>
            <div><dt>파일 수정</dt><dd>{profile.approval === 'ask' ? '먼저 묻기' : '자동'}</dd></div>
            <div><dt>워커 배치</dt><dd>{profile.worker_policy === 'autonomous' ? '필요할 때 자동' : '제한'}</dd></div>
            <div><dt>활성 능력</dt><dd>{activeSkills.length}개</dd></div>
            <div><dt>최대 작업 시간</dt><dd>{Math.round(profile.budget.dispatch.time_limit_ms / 60_000)}분</dd></div>
          </dl>
        </section>
      </div>

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
    </div>
  </section>
}
