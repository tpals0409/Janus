import { FormEvent, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Archive,
  Check,
  ChevronRight,
  CircleDot,
  FolderGit2,
  GitBranch,
  Loader2,
  MessageSquare,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  ShieldCheck,
  Square,
  Wifi,
  WifiOff,
  X
} from 'lucide-react'
import { useStore } from '../../store'
import type { Project, Task, TaskStatus } from '../../types'

const STATUS: Record<TaskStatus, { label: string; color: string; short: string }> = {
  todo: { label: 'Todo', color: 'var(--color-muted)', short: 'TO' },
  preparing: { label: 'Preparing', color: 'var(--color-warn)', short: 'PR' },
  working: { label: 'Working', color: 'var(--color-accent-fg)', short: 'WK' },
  needs_you: { label: 'Needs You', color: '#ff9f6e', short: 'NY' },
  review: { label: 'Review', color: 'var(--color-ok)', short: 'RV' },
  failed: { label: 'Failed', color: 'var(--color-danger)', short: 'FL' }
}

const RUNWAY: TaskStatus[] = ['todo', 'preparing', 'working', 'needs_you', 'review']

function StatusBadge({ status }: { status: TaskStatus }) {
  const meta = STATUS[status]
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10.5px] font-semibold"
      style={{ color: meta.color, borderColor: `color-mix(in srgb, ${meta.color} 45%, transparent)` }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: meta.color }} />
      {meta.label}
    </span>
  )
}

function TaskRunway({ status }: { status: TaskStatus }) {
  const active = status === 'failed' ? -1 : RUNWAY.indexOf(status)
  return (
    <div className="task-runway" aria-label={`Task status: ${STATUS[status].label}`}>
      {RUNWAY.map((step, index) => {
        const reached = active >= index
        const current = status === step
        return (
          <div className="task-runway-step" key={step}>
            <div
              className="task-runway-node"
              data-current={current || undefined}
              data-reached={reached || undefined}
            >
              {reached && index < active ? <Check size={10} /> : STATUS[step].short}
            </div>
            <span style={{ color: current ? STATUS[step].color : undefined }}>
              {STATUS[step].label}
            </span>
          </div>
        )
      })}
      {status === 'failed' && (
        <div className="ml-auto flex items-center gap-2 text-[11px] text-danger">
          <AlertTriangle size={13} /> Failed
        </div>
      )}
    </div>
  )
}

function ProjectPicker() {
  const projects = useStore((state) => state.projects)
  const projectId = useStore((state) => state.projectId)
  const selectProject = useStore((state) => state.selectProject)
  const addProject = useStore((state) => state.addProjectFromPicker)
  const busy = useStore((state) => state.taskBusy)

  return (
    <div className="border-b border-border p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[10px] font-semibold tracking-[0.14em] text-faint">PROJECTS</span>
        <button
          onClick={addProject}
          disabled={busy}
          title="Add a local Git repository"
          className="rounded p-1 text-faint hover:bg-raised hover:text-fg disabled:opacity-40"
        >
          <Plus size={13} />
        </button>
      </div>
      <div className="space-y-1">
        {projects.map((project) => (
          <button
            key={project.id}
            onClick={() => selectProject(project.id)}
            className="w-full rounded-md border px-2.5 py-2 text-left transition-colors"
            style={{
              borderColor: project.id === projectId ? 'var(--color-accent)' : 'transparent',
              background: project.id === projectId ? 'var(--color-accent-soft)' : 'transparent'
            }}
          >
            <div className="flex items-center gap-2">
              <FolderGit2 size={13} className="shrink-0 text-accent-fg" />
              <span className="truncate text-[12px] font-semibold">{project.name}</span>
            </div>
            <div className="mt-1 truncate pl-[21px] font-mono text-[9.5px] text-faint">
              {project.repo_path}
            </div>
          </button>
        ))}
        {projects.length === 0 && (
          <button
            onClick={addProject}
            className="w-full rounded-md border border-dashed border-border-strong px-3 py-4 text-center text-[11px] text-muted hover:border-accent hover:text-fg"
          >
            <FolderGit2 size={16} className="mx-auto mb-1.5" />
            Add local repository
          </button>
        )}
      </div>
    </div>
  )
}

function TaskSidebar({ onNewTask }: { onNewTask: () => void }) {
  const tasks = useStore((state) => state.tasks)
  const taskId = useStore((state) => state.taskId)
  const projectId = useStore((state) => state.projectId)
  const selectTask = useStore((state) => state.selectTask)

  return (
    <aside className="flex w-[272px] shrink-0 flex-col border-r border-border bg-panel">
      <ProjectPicker />
      <div className="flex min-h-0 flex-1 flex-col p-3">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[10px] font-semibold tracking-[0.14em] text-faint">TASKS</span>
          <span className="font-mono text-[10px] text-faint">{tasks.length}</span>
        </div>
        <button
          onClick={onNewTask}
          disabled={!projectId}
          className="mb-3 flex w-full items-center justify-center gap-1.5 rounded-md border border-border-strong bg-raised py-1.5 text-[11.5px] text-muted hover:border-accent hover:text-fg disabled:opacity-35"
        >
          <Plus size={13} /> New task
        </button>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {tasks.map((task) => (
            <button
              key={task.id}
              onClick={() => selectTask(task.id)}
              className="mb-1.5 w-full rounded-md border px-2.5 py-2.5 text-left"
              style={{
                borderColor: task.id === taskId ? 'var(--color-accent)' : 'transparent',
                background: task.id === taskId ? 'var(--color-accent-soft)' : 'transparent'
              }}
            >
              <div className="flex items-start gap-2">
                <span
                  className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full"
                  style={{ background: STATUS[task.status].color }}
                />
                <span className="line-clamp-2 text-[12px] font-medium leading-snug">
                  {task.title}
                </span>
              </div>
              <div className="mt-1.5 flex items-center justify-between pl-3.5 text-[9.5px]">
                <span style={{ color: STATUS[task.status].color }}>{STATUS[task.status].label}</span>
                <span className="font-mono text-faint">{task.base_ref}</span>
              </div>
            </button>
          ))}
          {projectId && tasks.length === 0 && (
            <div className="rounded-md border border-dashed border-border-strong px-3 py-5 text-center text-[11px] leading-relaxed text-faint">
              No tasks yet.
              <br />Define the work contract first.
            </div>
          )}
        </div>
      </div>
    </aside>
  )
}

function NewTaskDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const createTask = useStore((state) => state.createTask)
  const busy = useStore((state) => state.taskBusy)
  const [title, setTitle] = useState('')
  const [objective, setObjective] = useState('')
  const [acceptance, setAcceptance] = useState('')
  const [baseRef, setBaseRef] = useState('main')

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (![title, objective, acceptance, baseRef].every((value) => value.trim())) return
    await createTask({
      title: title.trim(),
      objective: objective.trim(),
      acceptance_command: acceptance.trim(),
      base_ref: baseRef.trim()
    })
    onClose()
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-[#050509bf] p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-[620px] rounded-lg border border-border-strong bg-panel shadow-2xl"
      >
        <div className="flex items-start justify-between border-b border-border px-5 py-4">
          <div>
            <div className="text-[10px] font-semibold tracking-[0.16em] text-accent-fg">
              NEW TASK · {project.name.toUpperCase()}
            </div>
            <h2 className="task-title mt-1 text-[22px] font-semibold">Define the work contract</h2>
          </div>
          <button type="button" onClick={onClose} className="rounded p-1 text-faint hover:text-fg">
            <X size={16} />
          </button>
        </div>
        <div className="space-y-4 p-5">
          <label className="block">
            <span className="task-label">Title</span>
            <input
              autoFocus
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Make session recovery deterministic"
              className="task-input"
            />
          </label>
          <label className="block">
            <span className="task-label">Objective</span>
            <textarea
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              placeholder="Describe the outcome the local agent must leave behind."
              rows={4}
              className="task-input resize-none"
            />
          </label>
          <div className="grid grid-cols-[1fr_160px] gap-3">
            <label className="block">
              <span className="task-label">Acceptance command</span>
              <input
                value={acceptance}
                onChange={(event) => setAcceptance(event.target.value)}
                placeholder="python -m pytest -q"
                className="task-input font-mono text-[11px]"
              />
            </label>
            <label className="block">
              <span className="task-label">Base ref</span>
              <input
                value={baseRef}
                onChange={(event) => setBaseRef(event.target.value)}
                className="task-input font-mono text-[11px]"
              />
            </label>
          </div>
        </div>
        <div className="flex items-center justify-between border-t border-border px-5 py-3">
          <span className="text-[10.5px] text-faint">Workspace is prepared after Task creation.</span>
          <div className="flex gap-2">
            <button type="button" onClick={onClose} className="rounded-md px-3 py-1.5 text-[11.5px] text-muted">
              Cancel
            </button>
            <button
              disabled={busy || !title.trim() || !objective.trim() || !acceptance.trim()}
              className="rounded-md bg-accent px-3.5 py-1.5 text-[11.5px] font-semibold text-white disabled:opacity-40"
            >
              {busy ? 'Creating…' : 'Create task'}
            </button>
          </div>
        </div>
      </form>
    </div>
  )
}

function WorkspaceCard({ task }: { task: Task }) {
  const prepare = useStore((state) => state.prepareWorkspace)
  const retry = useStore((state) => state.retryWorkspace)
  const inspect = useStore((state) => state.inspectWorkspace)
  const archive = useStore((state) => state.archiveWorkspace)
  const deleteBranch = useStore((state) => state.deleteWorkspaceBranch)
  const updateTask = useStore((state) => state.updateTask)
  const inspection = useStore((state) => state.workspaceInspection)
  const busy = useStore((state) => state.taskBusy)
  const [danger, setDanger] = useState<'force' | 'branch' | null>(null)
  const [editingBase, setEditingBase] = useState(false)
  const [baseRef, setBaseRef] = useState(task.base_ref)
  const workspace = task.workspace
  const gitStatus = inspection?.git_status

  useEffect(() => {
    setBaseRef(task.base_ref)
    setEditingBase(false)
  }, [task.id, task.base_ref])

  if (!workspace) {
    return (
      <section className="task-card">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="task-label">Workspace</div>
            <h3 className="mt-1 text-[14px] font-semibold">No worktree yet</h3>
            <p className="mt-1 max-w-[560px] text-[11px] leading-relaxed text-faint">
              Validate the repository and base ref, then create a Task-owned branch and worktree.
            </p>
          </div>
          <button onClick={prepare} disabled={busy} className="task-primary-action">
            <FolderGit2 size={13} /> Prepare workspace
          </button>
        </div>
      </section>
    )
  }

  const stateColor =
    workspace.state === 'ready'
      ? 'var(--color-ok)'
      : workspace.state === 'failed'
        ? 'var(--color-danger)'
        : workspace.state === 'preparing'
          ? 'var(--color-warn)'
          : 'var(--color-muted)'

  return (
    <section className="task-card">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="task-label">Workspace</div>
          <div className="mt-1 flex items-center gap-2">
            {workspace.state === 'preparing' ? (
              <Loader2 size={14} className="animate-spin" style={{ color: stateColor }} />
            ) : (
              <CircleDot size={14} style={{ color: stateColor }} />
            )}
            <h3 className="text-[14px] font-semibold capitalize">{workspace.state}</h3>
            <span className="font-mono text-[10px] text-faint">{workspace.progress}</span>
          </div>
        </div>
        {workspace.state === 'failed' && (
          <button onClick={retry} disabled={busy} className="task-primary-action">
            <RotateCcw size={13} /> Retry preparation
          </button>
        )}
        {workspace.state === 'ready' && (
          <button onClick={inspect} disabled={busy} className="task-quiet-action">
            <RefreshCw size={12} /> Check changes
          </button>
        )}
      </div>

      {workspace.error && (
        <div className="mt-3 rounded-md border border-[#f8717140] bg-[#f8717112] px-3 py-2 font-mono text-[10px] leading-relaxed text-danger">
          {workspace.error}
        </div>
      )}

      {workspace.state === 'failed' && (
        <div className="mt-3 rounded-md border border-border bg-raised p-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="task-label">Repair base ref</div>
              <p className="mt-1 text-[10.5px] text-faint">
                Update the Task contract before retrying if the recorded ref does not exist.
              </p>
            </div>
            {!editingBase && (
              <button onClick={() => setEditingBase(true)} className="task-quiet-action">
                Edit ref
              </button>
            )}
          </div>
          {editingBase && (
            <div className="mt-3 flex gap-2">
              <input
                value={baseRef}
                onChange={(event) => setBaseRef(event.target.value)}
                className="task-input mt-0 min-w-0 flex-1 font-mono text-[10.5px]"
              />
              <button
                onClick={async () => {
                  await updateTask({ base_ref: baseRef.trim() })
                  setEditingBase(false)
                }}
                disabled={busy || !baseRef.trim()}
                className="task-primary-action"
              >
                Save ref
              </button>
            </div>
          )}
        </div>
      )}

      <dl className="mt-4 grid grid-cols-[100px_1fr] gap-x-4 gap-y-2 border-t border-border pt-3 text-[10.5px]">
        <dt className="text-faint">Branch</dt>
        <dd className="flex min-w-0 items-center gap-1.5 font-mono text-muted">
          <GitBranch size={11} className="shrink-0" />
          <span className="truncate">{workspace.branch_name ?? 'allocating…'}</span>
        </dd>
        <dt className="text-faint">Root</dt>
        <dd className="truncate font-mono text-muted">{workspace.root_path ?? 'allocating…'}</dd>
      </dl>

      {workspace.state === 'ready' && gitStatus && (
        <div
          className="mt-3 flex items-center gap-2 rounded-md border px-3 py-2 text-[10.5px]"
          style={{
            borderColor: gitStatus.dirty ? '#fbbf2440' : '#34d39935',
            background: gitStatus.dirty ? '#fbbf240d' : '#34d3990b'
          }}
        >
          {gitStatus.dirty ? <AlertTriangle size={13} className="text-warn" /> : <ShieldCheck size={13} className="text-ok" />}
          <span className={gitStatus.dirty ? 'text-warn' : 'text-ok'}>
            {gitStatus.dirty
              ? `${gitStatus.tracked_changes.length} tracked · ${gitStatus.untracked.length} untracked · ${gitStatus.unmerged.length} unmerged`
              : 'Clean · safe to archive'}
          </span>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between border-t border-border pt-3">
        <span className="text-[10px] text-faint">
          Safe archive removes the worktree and preserves its branch.
        </span>
        <div className="flex gap-2">
          {workspace.state === 'ready' && (
            <>
              <button
                onClick={() => archive(false)}
                disabled={busy}
                className="task-quiet-action"
              >
                <Archive size={12} /> Safe archive
              </button>
              <button onClick={() => setDanger('force')} className="task-danger-link">
                Force remove…
              </button>
            </>
          )}
          {workspace.state === 'archived' && workspace.branch_name && (
            <button onClick={() => setDanger('branch')} className="task-danger-link">
              Delete branch…
            </button>
          )}
        </div>
      </div>

      {danger && (
        <div className="mt-3 flex items-center justify-between gap-4 rounded-md border border-[#f8717140] bg-[#f871710d] px-3 py-2">
          <div className="text-[10.5px] leading-relaxed text-danger">
            {danger === 'force'
              ? 'Discard worktree changes now. The branch is preserved.'
              : `Permanently delete ${workspace.branch_name}.`}
          </div>
          <div className="flex shrink-0 gap-2">
            <button onClick={() => setDanger(null)} className="rounded px-2 py-1 text-[10.5px] text-muted">
              Cancel
            </button>
            <button
              onClick={() => {
                if (danger === 'force') archive(true)
                else deleteBranch()
                setDanger(null)
              }}
              className="rounded bg-[#f8717126] px-2 py-1 text-[10.5px] font-semibold text-danger"
            >
              Confirm
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

function TaskRuntimeCard({ task }: { task: Task }) {
  const profiles = useStore((state) => state.agentProfiles)
  const selectedProfileId = useStore((state) => state.selectedAgentProfileId)
  const selectProfile = useStore((state) => state.selectAgentProfile)
  const session = useStore((state) => state.taskSession)
  const events = useStore((state) => state.taskSessionEvents)
  const connected = useStore((state) => state.taskConnected)
  const active = useStore((state) => state.taskTurnActive)
  const busy = useStore((state) => state.taskBusy)
  const runtimeError = useStore((state) => state.taskRuntimeError)
  const approvals = useStore((state) => state.taskApprovals)
  const startSession = useStore((state) => state.startTaskSession)
  const resumeSession = useStore((state) => state.resumeTaskSession)
  const sendMessage = useStore((state) => state.sendTaskMessage)
  const cancelTurn = useStore((state) => state.cancelTaskTurn)
  const stopSession = useStore((state) => state.stopTaskSession)
  const respondApproval = useStore((state) => state.respondTaskApproval)
  const [message, setMessage] = useState('')
  const ready = task.workspace?.state === 'ready'
  const resumable = session?.status === 'created' || session?.status === 'idle'

  const transcript = useMemo(() => {
    const persisted = events.filter((event) => event.kind === 'transcript')
    const lastTranscriptSeq = persisted.at(-1)?.seq ?? 0
    const live = events.filter((event) => {
      if (event.seq <= lastTranscriptSeq || event.kind !== 'agent_event') return false
      const kind = String(event.payload.kind ?? '')
      return kind === 'user' || kind === 'assistant'
    })
    return [...persisted, ...live].map((event) => {
      const payload = event.kind === 'transcript' ? event.payload : event.payload
      return {
        key: `${event.seq}-${event.kind}`,
        role: String(payload.kind ?? 'event'),
        content: String(payload.content ?? payload.text ?? '')
      }
    }).filter((item) => item.content)
  }, [events])

  const activity = events.filter((event) => event.kind !== 'transcript').slice(-7)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!message.trim()) return
    sendMessage(message)
    setMessage('')
  }

  return (
    <section className="task-card task-runtime-card">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="task-label">Agent session</div>
          <div className="mt-1 flex items-center gap-2">
            <MessageSquare size={14} className="text-accent-fg" />
            <h3 className="text-[14px] font-semibold">
              {session ? `Attempt ${session.dispatch.attempt}` : 'No runtime attempt'}
            </h3>
            {session && (
              <span className="rounded-full border border-border-strong px-2 py-0.5 font-mono text-[9px] uppercase text-muted">
                {session.status}
              </span>
            )}
            <span className={`flex items-center gap-1 text-[9.5px] ${connected ? 'text-ok' : 'text-faint'}`}>
              {connected ? <Wifi size={10} /> : <WifiOff size={10} />}
              {connected ? 'connected' : 'offline'}
            </span>
          </div>
        </div>
        <div className="flex items-end gap-2">
          <label>
            <span className="task-label">Agent profile</span>
            <select
              value={selectedProfileId}
              onChange={(event) => selectProfile(event.target.value)}
              disabled={busy}
              className="task-select mt-1"
            >
              {profiles.map((profile) => (
                <option key={profile.id} value={profile.id}>{profile.name}</option>
              ))}
            </select>
          </label>
          <button
            onClick={() => {
              if (session && resumable && !window.confirm('Start a new attempt and stop the resumable one?')) return
              void startSession()
            }}
            disabled={!ready || busy || active}
            className="task-primary-action"
            title={ready ? 'Create a new persisted Dispatch attempt' : 'Prepare the workspace first'}
          >
            <Play size={12} /> {session ? 'New attempt' : 'Start'}
          </button>
        </div>
      </div>

      {session && (
        <div className="mt-3 grid grid-cols-3 gap-2 border-t border-border pt-3 font-mono text-[9px] text-faint">
          <span className="truncate" title={session.id}>SESSION · {session.id}</span>
          <span className="truncate" title={session.dispatch_id}>DISPATCH · {session.dispatch_id}</span>
          <span className="truncate" title={session.agent_profile_id}>PROFILE · {session.agent_profile_id}</span>
        </div>
      )}

      {runtimeError && (
        <div className="mt-3 rounded-md border border-[#f8717140] bg-[#f8717112] px-3 py-2 text-[10.5px] text-danger">
          {runtimeError}
        </div>
      )}

      <div className="task-session-console mt-4">
        <div className="task-transcript">
          {transcript.length === 0 ? (
            <div className="grid h-full place-items-center px-6 text-center text-[10.5px] leading-relaxed text-faint">
              {session
                ? 'Connect this persisted session, then send the next instruction.'
                : 'Choose a profile and start an attempt. Runtime logs remain after restart.'}
            </div>
          ) : transcript.map((item) => (
            <div key={item.key} className="task-message" data-role={item.role}>
              <span>{item.role === 'user' ? 'YOU' : 'JANUS'}</span>
              <p>{item.content}</p>
            </div>
          ))}
        </div>
        <div className="task-activity">
          <div className="task-label mb-2">Live activity</div>
          {activity.length === 0 ? (
            <div className="text-[9.5px] text-faint">No events</div>
          ) : activity.map((event) => (
            <div key={`${event.seq}-${event.kind}`} className="task-activity-row">
              <span>{event.seq}</span>
              <strong>{event.kind}</strong>
              <em>{String(event.payload.kind ?? event.payload.type ?? '')}</em>
            </div>
          ))}
        </div>
      </div>

      {approvals.map((approval) => (
        <div key={approval.id} className="mt-3 flex items-center justify-between gap-4 rounded-md border border-[#fbbf2440] bg-[#fbbf240d] px-3 py-2">
          <div className="min-w-0 text-[10.5px] text-warn">
            Approve <code className="font-mono">{approval.tool}</code> in this Task workspace?
          </div>
          <div className="flex shrink-0 gap-2">
            <button onClick={() => respondApproval(approval.id, false)} className="task-quiet-action">Deny</button>
            <button onClick={() => respondApproval(approval.id, true)} className="task-primary-action">Approve</button>
          </div>
        </div>
      ))}

      <form onSubmit={submit} className="mt-3 flex gap-2 border-t border-border pt-3">
        {!connected && resumable && (
          <button type="button" onClick={() => void resumeSession()} disabled={busy} className="task-quiet-action">
            <Play size={11} /> Resume
          </button>
        )}
        <input
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          disabled={!connected || active || !resumable}
          placeholder={connected ? 'Send the next Task instruction…' : 'Resume the session to continue'}
          className="task-input mt-0 min-w-0 flex-1"
        />
        {active ? (
          <button type="button" onClick={cancelTurn} className="task-danger-link border border-[#f8717140]">
            <Square size={11} /> Cancel turn
          </button>
        ) : (
          <button disabled={!connected || !message.trim() || !resumable} className="task-primary-action">
            <Send size={11} /> Send
          </button>
        )}
        {session && ['created', 'running', 'idle'].includes(session.status) && (
          <button type="button" onClick={() => void stopSession()} className="task-quiet-action">
            Stop session
          </button>
        )}
      </form>
    </section>
  )
}

function TaskDetail({ task }: { task: Task }) {
  const archiveTask = useStore((state) => state.archiveSelectedTask)
  const busy = useStore((state) => state.taskBusy)
  const session = useStore((state) => state.taskSession)
  const connected = useStore((state) => state.taskConnected)
  const canArchiveTask = !task.workspace || task.workspace.state === 'archived'

  return (
    <main className="min-w-0 flex-1 overflow-y-auto bg-bg">
      <div className="mx-auto max-w-[1080px] px-8 py-7">
        <div className="mb-6 flex items-start justify-between gap-6">
          <div className="min-w-0">
            <div className="mb-2 flex items-center gap-2">
              <StatusBadge status={task.status} />
              <span className="font-mono text-[9.5px] text-faint">{task.id}</span>
            </div>
            <h1 className="task-title text-[30px] font-semibold leading-tight tracking-[-0.025em]">
              {task.title}
            </h1>
          </div>
          <button
            onClick={() => {
              if (window.confirm(`Archive Task “${task.title}”?`)) archiveTask()
            }}
            disabled={busy || !canArchiveTask}
            title={canArchiveTask ? 'Archive Task' : 'Archive its workspace first'}
            className="task-quiet-action disabled:opacity-30"
          >
            <Archive size={12} /> Archive task
          </button>
        </div>

        <TaskRunway status={task.status} />

        <div className="mt-6 grid grid-cols-[minmax(0,1fr)_280px] gap-5">
          <div className="space-y-5">
            <section className="task-card">
              <div className="task-label">Objective</div>
              <p className="mt-2 whitespace-pre-wrap text-[13px] leading-6 text-fg">{task.objective}</p>
              <div className="mt-5 grid grid-cols-[1fr_150px] gap-4 border-t border-border pt-4">
                <div>
                  <div className="task-label">Acceptance</div>
                  <code className="mt-1.5 block rounded-md border border-border bg-[#08080d] px-3 py-2 font-mono text-[10.5px] text-accent-fg">
                    {task.acceptance_command}
                  </code>
                </div>
                <div>
                  <div className="task-label">Base ref</div>
                  <div className="mt-1.5 flex items-center gap-1.5 rounded-md border border-border px-3 py-2 font-mono text-[10.5px] text-muted">
                    <GitBranch size={11} /> {task.base_ref}
                  </div>
                </div>
              </div>
            </section>
            <WorkspaceCard task={task} />
          </div>

          <aside className="space-y-4">
            <section className="task-card border-accent/30">
              <div className="task-label">Next action</div>
              <h3 className="mt-2 text-[14px] font-semibold">
                {!task.workspace
                  ? 'Prepare the workspace'
                  : task.workspace.state === 'preparing'
                    ? 'Creating the worktree'
                    : task.workspace.state === 'failed'
                      ? 'Repair preparation'
                      : task.workspace.state === 'ready'
                        ? !session
                          ? 'Start an agent session'
                          : connected
                            ? 'Send the next instruction'
                            : session.status === 'idle' || session.status === 'created'
                              ? 'Resume the session'
                              : 'Start a new attempt'
                        : 'Workspace archived'}
              </h3>
              <p className="mt-2 text-[11px] leading-relaxed text-faint">
                {!task.workspace
                  ? 'Janus validates the Git repo and base ref before creating an isolated branch.'
                  : task.workspace.state === 'preparing'
                    ? `Background stage: ${task.workspace.progress}`
                    : task.workspace.state === 'failed'
                      ? 'Fix the repository or base ref, then retry without losing recorded ownership.'
                      : task.workspace.state === 'ready'
                        ? !session
                          ? 'Choose an AgentProfile. Janus will persist the Dispatch, transcript, and runtime log.'
                          : `Attempt ${session.dispatch.attempt} · ${session.status} · ${session.agent_profile_id}`
                        : 'The branch remains available until you explicitly delete it.'}
              </p>
              <div className="mt-4 flex items-center gap-1.5 text-[10px] text-faint">
                <ChevronRight size={11} /> Latest Dispatch owns all runtime events
              </div>
            </section>
            <section className="task-card">
              <div className="task-label">Ownership</div>
              <dl className="mt-3 space-y-2 text-[10.5px]">
                <div className="flex justify-between gap-3">
                  <dt className="text-faint">Task</dt>
                  <dd className="truncate font-mono text-muted">{task.id}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-faint">Workspace</dt>
                  <dd className="truncate font-mono text-muted">{task.workspace?.id ?? 'not-created'}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-faint">Attempts</dt>
                  <dd className="font-mono text-muted">{task.dispatches?.length ?? 0}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-faint">Session</dt>
                  <dd className="truncate font-mono text-muted">{session?.id ?? 'not-started'}</dd>
                </div>
              </dl>
            </section>
          </aside>
          <div className="col-span-2">
            <TaskRuntimeCard task={task} />
          </div>
        </div>
      </div>
    </main>
  )
}

function EmptyTaskState({ hasProject, onNewTask }: { hasProject: boolean; onNewTask: () => void }) {
  const addProject = useStore((state) => state.addProjectFromPicker)
  return (
    <div className="grid min-w-0 flex-1 place-items-center bg-bg px-8 text-center">
      <div className="max-w-[420px]">
        <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-lg border border-border-strong bg-panel text-accent-fg">
          <FolderGit2 size={22} />
        </div>
        <h2 className="task-title text-[22px] font-semibold">
          {hasProject ? 'Turn an objective into a Task' : 'Add a local Git repository'}
        </h2>
        <p className="mt-2 text-[11.5px] leading-relaxed text-faint">
          {hasProject
            ? 'Define the outcome, acceptance command, and base ref before any agent starts work.'
            : 'Janus creates Task-owned branches and worktrees without modifying the main checkout.'}
        </p>
        <button onClick={hasProject ? onNewTask : addProject} className="task-primary-action mx-auto mt-5">
          <Plus size={13} /> {hasProject ? 'New task' : 'Add repository'}
        </button>
      </div>
    </div>
  )
}

export default function TaskWorkspace() {
  const projects = useStore((state) => state.projects)
  const projectId = useStore((state) => state.projectId)
  const task = useStore((state) => state.task)
  const refresh = useStore((state) => state.refreshSelectedTask)
  const error = useStore((state) => state.taskActionError)
  const clearError = useStore((state) => state.clearTaskError)
  const [creating, setCreating] = useState(false)
  const project = useMemo(
    () => projects.find((item) => item.id === projectId) ?? null,
    [projects, projectId]
  )

  useEffect(() => {
    if (task?.workspace?.state !== 'preparing') return
    const timer = window.setInterval(refresh, 650)
    return () => window.clearInterval(timer)
  }, [task?.workspace?.state, refresh])

  return (
    <>
      <TaskSidebar onNewTask={() => setCreating(true)} />
      <div className="relative flex min-w-0 flex-1">
        {task ? (
          <TaskDetail task={task} />
        ) : (
          <EmptyTaskState hasProject={Boolean(project)} onNewTask={() => setCreating(true)} />
        )}
        {error && (
          <div className="absolute bottom-4 left-1/2 z-30 flex max-w-[680px] -translate-x-1/2 items-start gap-3 rounded-md border border-[#f8717150] bg-[#241318] px-3 py-2.5 shadow-xl">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-danger" />
            <span className="text-[10.5px] leading-relaxed text-danger">{error}</span>
            <button onClick={clearError} className="ml-2 text-danger/70 hover:text-danger">
              <X size={13} />
            </button>
          </div>
        )}
      </div>
      {creating && project && (
        <NewTaskDialog project={project} onClose={() => setCreating(false)} />
      )}
    </>
  )
}
