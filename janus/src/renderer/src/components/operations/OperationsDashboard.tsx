import { useEffect } from 'react'
import {
  AlertTriangle, ArrowUpRight, Cpu, Gauge, MemoryStick, RefreshCw
} from 'lucide-react'
import { useStore } from '../../store'
import type { OperationsLane, OperationsTask } from '../../types'

const LANES: Array<{ id: OperationsLane; label: string; color: string; note: string }> = [
  { id: 'queue', label: 'Queue', color: '#7f8798', note: 'Waiting for ownership' },
  { id: 'working', label: 'Working', color: '#72a7ff', note: 'Agent or tool active' },
  { id: 'needs_you', label: 'Needs You', color: '#ff9f6e', note: 'Decision or approval' },
  { id: 'review', label: 'Review', color: '#6dd6a8', note: 'Change ready to inspect' },
  { id: 'failed', label: 'Failed', color: '#f87171', note: 'Recovery required' }
]

const TIMELINE_COLOR: Record<string, string> = {
  generation: '#8b9dff', tool: '#6dd6a8', verification: '#e3bd6a',
  queue: '#7f8798', worker: '#c495ff'
}

function bytes(value: number) {
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(0)} KiB`
  if (value < 1024 ** 3) return `${(value / 1024 ** 2).toFixed(0)} MiB`
  return `${(value / 1024 ** 3).toFixed(1)} GiB`
}

function BudgetBars({ task }: { task: OperationsTask }) {
  const values = [
    ['tok', task.budget_progress.tokens], ['step', task.budget_progress.steps],
    ['time', task.budget_progress.time], ['wrk', task.budget_progress.workers]
  ] as const
  return (
    <div className="grid grid-cols-4 gap-1.5">
      {values.map(([label, value]) => (
        <div key={label} title={`${label} ${value.toFixed(1)}%`}>
          <div className="mb-1 flex justify-between font-mono text-[7.5px] uppercase text-faint">
            <span>{label}</span><span>{Math.round(value)}</span>
          </div>
          <div className="h-1 overflow-hidden rounded-full bg-[#20202a]">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.min(100, value)}%`,
                background: value >= 90 ? '#f87171' : value >= 70 ? '#e3bd6a' : '#738cff'
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
      <div className="mb-1.5 font-mono text-[7.5px] uppercase tracking-[0.12em] text-faint">
        generation / tool / verification
      </div>
      <div className="flex h-3 items-center gap-[3px]" aria-label={`${visible.length} recent operations`}>
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
          <span className="font-mono text-[8px] text-faint">No runtime intervals yet</span>
        )}
      </div>
    </div>
  )
}

function TaskStrip({ task, onOpen }: { task: OperationsTask; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      className="group w-full rounded-md border border-border bg-[#0b0b11] p-3 text-left transition-colors hover:border-[#7180b8] hover:bg-[#101019] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-mono text-[8px] uppercase tracking-[0.12em] text-faint">
            {task.project_name} · {task.dispatch ? `attempt ${task.dispatch.attempt}` : 'unassigned'}
          </div>
          <div className="mt-1 line-clamp-2 text-[11.5px] font-semibold leading-[1.35] text-fg">
            {task.title}
          </div>
        </div>
        <ArrowUpRight size={11} className="mt-0.5 shrink-0 text-faint group-hover:text-accent-fg" />
      </div>
      <div className="mt-2.5 flex items-center justify-between font-mono text-[8px] uppercase text-faint">
        <span>{task.session?.status ?? task.status}</span>
        <span className={task.budget_progress.peak >= 90 ? 'text-danger' : ''}>
          peak {Math.round(task.budget_progress.peak)}%
        </span>
      </div>
      <div className="mt-2"><BudgetBars task={task} /></div>
      <Timeline task={task} />
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
    const timer = window.setInterval(() => void load(), 2000)
    return () => window.clearInterval(timer)
  }, [load])

  const open = async (task: OperationsTask) => {
    if (projectId !== task.project_id) await selectProject(task.project_id)
    await selectTask(task.id)
    onOpenTask()
  }

  const model = snapshot?.scheduler.resources.model_generation
  return (
    <main className="min-w-0 flex-1 overflow-y-auto bg-[#08080d]">
      <section className="border-b border-border bg-panel px-5 py-4">
        <div className="flex items-start justify-between gap-6">
          <div>
            <div className="font-mono text-[9px] uppercase tracking-[0.2em] text-[#9dacff]">
              Local agent control room
            </div>
            <h1 className="task-title mt-1 text-[23px] font-semibold">Attention before throughput.</h1>
            <p className="mt-1 max-w-[680px] text-[10.5px] text-faint">
              One board for every Task that is waiting, running, asking, ready, or recovering.
            </p>
          </div>
          <button onClick={() => void load()} className="task-quiet-action"><RefreshCw size={11} /> Refresh</button>
        </div>

        {snapshot && (
          <div className="mt-4 grid grid-cols-[minmax(0,1fr)_auto_auto_auto] items-center gap-4 border-t border-border pt-3">
            <div>
              <div className="mb-1.5 flex justify-between font-mono text-[8px] uppercase text-faint">
                <span>Attention rail · {snapshot.summary.total} tasks</span>
                <span>{snapshot.summary.attention} need action</span>
              </div>
              <div className="flex h-2 gap-[2px] overflow-hidden rounded-full bg-[#15151d]">
                {snapshot.tasks.map((task) => {
                  const lane = LANES.find((item) => item.id === task.lane)!
                  return <span key={task.id} title={`${task.title} · ${lane.label}`} className="min-w-[4px] flex-1" style={{ background: lane.color }} />
                })}
              </div>
            </div>
            <div className="flex items-center gap-2 border-l border-border pl-4 text-[9.5px] text-muted">
              <Cpu size={12} className="text-[#9dacff]" /> model {model?.active ?? 0}/{model?.cap ?? 1} · q {model?.queued ?? 0}
            </div>
            <div className="flex items-center gap-2 border-l border-border pl-4 text-[9.5px] text-muted">
              <MemoryStick size={12} className="text-[#c495ff]" /> peak {bytes(snapshot.memory.janus_process_peak_rss_bytes)}
            </div>
            <div className="flex items-center gap-2 border-l border-border pl-4 text-[9.5px] text-muted">
              <Gauge size={12} className="text-[#6dd6a8]" /> {snapshot.scheduler.active_leases} leases
            </div>
          </div>
        )}
      </section>

      {error && (
        <div className="m-5 flex items-center gap-2 rounded-md border border-[#f8717140] bg-[#f8717112] p-3 text-[11px] text-danger">
          <AlertTriangle size={13} /> {error}
        </div>
      )}

      {!snapshot ? (
        <div className="grid h-[420px] place-items-center font-mono text-[10px] text-faint">Loading operations…</div>
      ) : (
        <section className="grid min-w-[1180px] grid-cols-5 gap-3 p-4">
          {LANES.map((lane) => {
            const tasks = snapshot.tasks.filter((task) => task.lane === lane.id)
            return (
              <div key={lane.id} className="min-w-0">
                <div className="mb-2 flex items-end justify-between border-b pb-2" style={{ borderColor: `${lane.color}55` }}>
                  <div>
                    <div className="flex items-center gap-2 text-[11px] font-semibold" style={{ color: lane.color }}>
                      <span className="h-1.5 w-1.5 rounded-full" style={{ background: lane.color }} />{lane.label}
                    </div>
                    <div className="mt-0.5 text-[8.5px] text-faint">{lane.note}</div>
                  </div>
                  <span className="font-mono text-[18px] font-semibold" style={{ color: lane.color }}>{tasks.length}</span>
                </div>
                <div className="space-y-2">
                  {tasks.map((task) => <TaskStrip key={task.id} task={task} onOpen={() => void open(task)} />)}
                  {tasks.length === 0 && (
                    <div className="rounded-md border border-dashed border-border px-3 py-8 text-center font-mono text-[8.5px] text-faint">Clear</div>
                  )}
                </div>
              </div>
            )
          })}
        </section>
      )}
    </main>
  )
}
