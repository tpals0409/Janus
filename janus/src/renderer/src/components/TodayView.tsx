import { useMemo } from 'react'
import { Plus } from 'lucide-react'
import { useStore } from '../store'
import type { Task } from '../types'
import { Button, EmptyState } from './ui'

/* 오늘(홈) — 계약 §3·§10: 상태는 색이 아니라 문장으로 읽힌다.
   그룹은 "누구 차례인가"로 나눈다: 사람 차례(기다림) → 진행 중 → 준비됨. */

function statusSentence(task: Task): string {
  if (task.status === 'needs_you') {
    if (task.attention_reason === 'conversation_idle') return '대화를 기다리고 있어요'
    if (task.attention_reason === 'mockup_review') return '목업 검토를 기다리고 있어요'
    return '응답을 기다리고 있어요'
  }
  if (task.status === 'review') return '검토를 기다리고 있어요'
  if (task.status === 'failed') return '실패했어요 — 이유를 확인해 주세요'
  if (task.status === 'working') return '돌고 있어요'
  if (task.status === 'preparing') return '작업 공간을 준비하고 있어요'
  return '준비됐어요 — 시작만 하면 돼요'
}

function statusGlyph(task: Task): { glyph: string; className: string } {
  if (task.status === 'failed') return { glyph: '×', className: 'text-danger' }
  if (task.status === 'needs_you' || task.status === 'review') return { glyph: '△', className: 'text-warn' }
  if (task.status === 'working' || task.status === 'preparing') return { glyph: '●', className: 'text-accent' }
  return { glyph: '○', className: 'text-faint' }
}

function headline(waiting: number, running: number, total: number): string {
  if (waiting > 0 && running > 0) return `${waiting}개가 사람을 기다리고, ${running}개가 돌고 있어요`
  if (waiting > 0) return `작업 ${waiting}개가 사람을 기다리고 있어요`
  if (running > 0) return `작업 ${running}개가 돌고 있어요`
  if (total > 0) return '모두 조용해요'
  return '아직 작업이 없어요'
}

function TodayRow({ task, primary, onOpen }: { task: Task; primary: boolean; onOpen: () => void }) {
  const { glyph, className } = statusGlyph(task)
  return (
    <div className="today-row">
      <span aria-hidden="true" className={`w-4 text-[12px] ${className}`}>{glyph}</span>
      <button type="button" onClick={onOpen} className="today-row__title" title={task.title}>
        {task.title}
      </button>
      <span className="today-row__sentence">{statusSentence(task)}</span>
      <span className="font-mono text-[10.5px] text-faint">{task.base_ref}</span>
      <Button variant={primary ? 'primary' : 'secondary'} compact onClick={onOpen}>
        {primary ? '보러 가기' : '열기'}
      </Button>
    </div>
  )
}

function TodaySection({ label, tasks, primary, onOpen }: {
  label: string
  tasks: Task[]
  primary?: boolean
  onOpen: (taskId: string) => void
}) {
  if (tasks.length === 0) return null
  return (
    <section>
      <div className="today-section-label">
        {label} <span className="font-mono text-faint">{tasks.length}</span>
      </div>
      <div className="border-t border-border">
        {tasks.map((task) => (
          <TodayRow key={task.id} task={task} primary={Boolean(primary)} onOpen={() => onOpen(task.id)} />
        ))}
      </div>
    </section>
  )
}

export default function TodayView({ onOpenTask, onNewTask }: {
  onOpenTask: () => void
  onNewTask: () => void
}) {
  const tasks = useStore((state) => state.tasks)
  const projectId = useStore((state) => state.projectId)
  const currentTaskId = useStore((state) => state.taskId)
  const selectTask = useStore((state) => state.selectTask)

  const groups = useMemo(() => ({
    waiting: tasks.filter((task) => ['needs_you', 'review', 'failed'].includes(task.status)),
    running: tasks.filter((task) => task.status === 'working' || task.status === 'preparing'),
    ready: tasks.filter((task) => task.status === 'todo')
  }), [tasks])

  const open = (taskId: string) => {
    // 이미 열려 있는 작업이면 다시 로드하지 않는다 — 진행 중인 화면 상태를 지키기 위해서다.
    if (taskId !== currentTaskId) void selectTask(taskId)
    onOpenTask()
  }

  return (
    <main className="today-view" aria-label="오늘">
      <div className="today-body">
        <header className="today-header">
          <h1 className="text-[13.5px] font-semibold text-fg">오늘</h1>
          <p className="text-[12px] text-muted">{headline(groups.waiting.length, groups.running.length, tasks.length)}</p>
          <span className="ml-auto font-mono text-[10.5px] text-faint">
            {new Intl.DateTimeFormat('ko', { month: 'long', day: 'numeric', weekday: 'short' }).format(new Date())}
          </span>
        </header>
        {tasks.length === 0 ? (
          <div className="grid flex-1 place-items-center py-24">
            <EmptyState
              title={projectId ? '아직 작업이 없어요' : '먼저 로컬 저장소를 추가하세요'}
              description={projectId
                ? '목표를 위임하면 여기에서 진행 상황이 문장으로 읽혀요.'
                : '작업 화면의 사이드바에서 Git 저장소를 추가하면 시작할 수 있어요.'}
              action={projectId ? (
                <Button variant="primary" onClick={onNewTask}><Plus size={13} /> 새 작업</Button>
              ) : undefined}
            />
          </div>
        ) : (
          <div className="grid gap-1">
            <TodaySection label="기다림" tasks={groups.waiting} primary onOpen={open} />
            <TodaySection label="진행 중" tasks={groups.running} onOpen={open} />
            <TodaySection label="준비됨" tasks={groups.ready} onOpen={open} />
          </div>
        )}
      </div>
    </main>
  )
}
