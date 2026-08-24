import { useEffect } from 'react'
import {
  AlertTriangle, ArrowUpRight, CheckCircle2, Cpu, Gauge, MemoryStick, RefreshCw
} from 'lucide-react'
import { useStore } from '../../store'
import { useDomainEvent } from '../../domainEvents'
import type { OperationsLane, OperationsTask } from '../../types'
import { Button, Status } from '../ui'

const LANES: Array<{ id: OperationsLane; label: string; tone: 'muted' | 'success' | 'warning' | 'danger'; color: string; note: string }> = [
  { id: 'queue', label: '대기', tone: 'muted', color: 'var(--text-muted)', note: '소유권 대기 중' },
  { id: 'working', label: '작업 중', tone: 'success', color: 'var(--accent)', note: '에이전트 또는 도구 실행 중' },
  { id: 'idle', label: '대화 가능', tone: 'muted', color: 'var(--text-secondary)', note: '후속 지시를 받을 수 있음' },
  { id: 'needs_you', label: '확인 필요', tone: 'warning', color: 'var(--warning)', note: '결정 또는 승인 필요' },
  { id: 'review', label: '검토', tone: 'success', color: 'var(--success)', note: '변경 검토 준비됨' },
  { id: 'failed', label: '실패', tone: 'danger', color: 'var(--danger)', note: '복구 필요' }
]

const TIMELINE_COLOR: Record<string, string> = {
  generation: 'var(--info)', tool: 'var(--success)', verification: 'var(--warning)',
  queue: 'var(--text-muted)', worker: 'var(--text-secondary)'
}

const STATUS_LABEL: Record<string, string> = {
  todo: '할 일', preparing: '준비 중', working: '작업 중', needs_you: '확인 필요',
  review: '검토', failed: '실패', created: '생성됨', running: '실행 중', idle: '대기 중',
  stopped: '중단됨', completed: '완료', queued: '대기열', error: '오류'
}

function bytes(value: number) {
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(0)} KiB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(0)} MiB`
  return `${(value / 1024 ** 3).toFixed(1)} GiB`
}

function BudgetBars({ task }: { task: OperationsTask }) {
  const values = [
    ['토큰', task.budget_progress.tokens], ['단계', task.budget_progress.steps],
    ['시간', task.budget_progress.time], ['워커', task.budget_progress.workers]
  ] as const
  return (
    <div className="grid grid-cols-4 gap-1.5">
      {values.map(([label, value]) => (
        <div key={label} title={`${label} ${value.toFixed(1)}%`}>
          <div className="mb-1 flex justify-between font-mono text-[10px] uppercase text-faint">
            <span>{label}</span><span>{Math.round(value)}</span>
          </div>
          <div className="h-1 overflow-hidden bg-active">
            <div
              className="h-full"
              style={{
                width: `${Math.min(100, value)}%`,
                background: value >= 90 ? 'var(--danger)' : value >= 70 ? 'var(--warning)' : 'var(--border-strong)'
              }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

function Timeline({ task }: { task: OperationsTask }) {
  const visible = task.timeline.slice(-14)
  return (
    <div className="mt-3">
      <div className="mb-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-faint">
        생성 / 도구 / 검증
      </div>
      <div className="flex h-3 items-center gap-[3px]" aria-label={`최근 작업 ${visible.length}건`}>
        {visible.length ? visible.map((item, index) => (
          <span
            key={`${item.at}-${index}`}
            className="h-2.5 min-w-[4px] flex-1 rounded-[2px]"
            title={`${item.category} · ${item.kind}${item.status ? ` · ${item.status}` : ''}`}
            style={{
              background: TIMELINE_COLOR[item.category],
              opacity: item.status === 'failed' || item.status === 'error' ? 1 : 0.68
            }}
          />
        )) : (
          <span className="font-mono text-[10px] text-faint">아직 실행 구간이 없습니다</span>
        )}
      </div>
    </div>
  )
}

function TaskStrip({ task, onOpen, attention = false }: { task: OperationsTask; onOpen: () => void; attention?: boolean }) {
  return (
    <button
      onClick={onOpen}
      className={`operations-task-row group ${attention ? 'operations-task-row--attention' : ''}`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-mono text-[10.5px] text-faint">
            {task.project_name} · {task.dispatch ? `시도 ${task.dispatch.attempt}` : '미배정'}
          </div>
          <div className="mt-1 truncate text-[14px] font-medium text-fg">
            {task.title}
          </div>
        </div>
        <ArrowUpRight size={11} className="mt-0.5 shrink-0 text-faint group-hover:text-fg" />
      </div>
      <div className="mt-2 flex items-center gap-3 font-mono text-[10.5px] text-faint">
        <span>{STATUS_LABEL[task.session?.status ?? task.status] ?? task.session?.status ?? task.status}</span>
        <span className={task.budget_progress.peak >= 90 ? 'text-danger' : ''}>
          예산 최대 {Math.round(task.budget_progress.peak)}%
        </span>
      </div>
      </div>
      <div className="operations-task-row__telemetry">
        <BudgetBars task={task} />
        <Timeline task={task} />
      </div>
    </button>
  )
}

export default function OperationsDashboard({ onOpenTask }: { onOpenTask: () => void }) {
  const snapshot = useStore((state) => state.operations)
  const error = useStore((state) => state.operationsError)
  const load = useStore((state) => state.loadOperations)
  const projectId = useStore((state) => state.projectId)
  const selectProject = useStore((state) => state.selectProject)
  const selectTask = useStore((state) => state.selectTask)

  useEffect(() => {
    void load()
  }, [load])
  useDomainEvent('operations', () => void load())

  const open = async (task: OperationsTask) => {
    if (projectId !== task.project_id) await selectProject(task.project_id)
    await selectTask(task.id)
    onOpenTask()
  }

  const model = snapshot?.scheduler.resources.model_generation
  const priorityLanes: OperationsLane[] = ['needs_you', 'failed']
  const flowLanes: OperationsLane[] = ['working', 'idle', 'queue', 'review']
  const renderGroup = (laneId: OperationsLane, attention = false) => {
    if (!snapshot) return null
    const lane = LANES.find((item) => item.id === laneId)!
    const tasks = snapshot.tasks.filter((task) => task.lane === laneId)
    if (!tasks.length && attention) return null
    return (
      <section key={lane.id} className="operations-group">
        <header>
          <div><Status tone={lane.tone}>{lane.label}</Status><span>{lane.note}</span></div>
          <strong style={{ color: lane.color }}>{tasks.length}</strong>
        </header>
        <div className="operations-group__list">
          {tasks.map((task) => <TaskStrip key={task.id} task={task} attention={attention} onOpen={() => void open(task)} />)}
          {tasks.length === 0 && <div className="operations-group__empty">현재 작업 없음</div>}
        </div>
      </section>
    )
  }
  return (
    <main className="workspace-surface min-w-0 flex-1 overflow-y-auto">
      <section className="operations-header">
        <div className="flex items-start justify-between gap-6">
          <div>
            <h1 className="task-title text-[16px] font-medium">운영</h1>
            <p className="mt-1 text-[11.5px] text-faint">개입이 필요한 작업을 먼저 확인하고 실행 흐름을 추적합니다.</p>
          </div>
          <Button onClick={() => void load()} variant="secondary" compact><RefreshCw size={11} /> 새로고침</Button>
        </div>

        {snapshot && (
          <div className="operations-summary">
            <div><strong>{snapshot.summary.attention}</strong><span>확인 필요</span></div>
            <div><strong>{snapshot.tasks.filter((task) => task.lane === 'working').length}</strong><span>실행 중</span></div>
            <div><strong>{snapshot.tasks.filter((task) => task.lane === 'idle').length}</strong><span>대화 가능</span></div>
            <div><Cpu size={13} /><span>모델 {model?.active ?? 0}/{model?.cap ?? 1} · 대기 {model?.queued ?? 0}</span></div>
            <div><MemoryStick size={13} /><span>메모리 {bytes(snapshot.memory.janus_process_peak_rss_bytes)}</span></div>
            <div><Gauge size={13} /><span>리스 {snapshot.scheduler.active_leases}개</span></div>
          </div>
        )}
      </section>

      {error && (
        <div className="error-strip m-5">
          <AlertTriangle size={13} /> {error}
        </div>
      )}

      {!snapshot ? (
        <div className="grid h-[420px] place-items-center font-mono text-[10px] text-faint">운영 현황 로딩 중…</div>
      ) : (
        <section className="operations-content">
          <div className="operations-attention">
            {priorityLanes.map((lane) => renderGroup(lane, true))}
            {snapshot.summary.attention === 0 && <div className="operations-clear"><CheckCircle2 size={16} /> 지금 확인할 작업이 없습니다.</div>}
          </div>
          <div className="operations-flow">{flowLanes.map((lane) => renderGroup(lane))}</div>
        </section>
      )}
    </main>
  )
}
