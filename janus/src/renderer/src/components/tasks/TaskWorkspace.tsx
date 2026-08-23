import { FormEvent, Suspense, lazy, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Archive,
  Check,
  ChevronRight,
  CircleDot,
  FolderGit2,
  GitPullRequest,
  GitBranch,
  ExternalLink,
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
  Trash2,
  X
} from 'lucide-react'
import { useStore } from '../../store'
import type { ChangeLayer, ChangeSetFile, Project, Task, TaskStatus } from '../../types'
import ContextInspector from './ContextInspector'
import { Button, EmptyState, Field, IconButton, Input, Status, Textarea } from '../ui'

const TaskDevelopmentSurface = lazy(() => import('./TaskDevelopmentSurface'))

const STATUS: Record<TaskStatus, { label: string; color: string; short: string }> = {
  todo: { label: '할 일', color: 'var(--color-muted)', short: '할' },
  preparing: { label: '준비 중', color: 'var(--color-warn)', short: '준' },
  working: { label: '작업 중', color: 'var(--color-accent-fg)', short: '작' },
  needs_you: { label: '확인 필요', color: 'var(--color-warn)', short: '확' },
  review: { label: '검토', color: 'var(--color-ok)', short: '검' },
  failed: { label: '실패', color: 'var(--color-danger)', short: '실' }
}

const RUNWAY: TaskStatus[] = ['todo', 'preparing', 'working', 'needs_you', 'review']
const STATE_LABEL: Record<string, string> = {
  created: '생성됨', idle: '대기 중', running: '실행 중', stopped: '중단됨',
  completed: '완료', queued: '대기열', passed: '통과', failed: '실패', error: '오류',
  preparing: '준비 중', ready: '준비됨', archived: '보관됨', merged: '병합됨',
  pending: '대기 중', success: '성공', neutral: '중립', skipped: '건너뜀', unknown: '알 수 없음',
  validating: '검증 중', recovered: '복구됨', allocating: '할당 중', creating: '생성 중',
  interrupted: '중단됨', force_removed: '강제 제거됨'
}
const stateLabel = (value: string) => STATE_LABEL[value.toLowerCase()] ?? value

function StatusBadge({ status }: { status: TaskStatus }) {
  const meta = STATUS[status]
  const tone = status === 'failed' ? 'danger' : status === 'preparing' || status === 'needs_you' ? 'warning' : status === 'todo' ? 'muted' : 'success'
  return <Status tone={tone}>{meta.label}</Status>
}

function TaskRunway({ status }: { status: TaskStatus }) {
  const active = status === 'failed' ? -1 : RUNWAY.indexOf(status)
  return (
    <div className="task-runway" aria-label={`작업 상태: ${STATUS[status].label}`}>
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
          <AlertTriangle size={13} /> 실패
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
  const archiveProject = useStore((state) => state.archiveProject)
  const busy = useStore((state) => state.taskBusy)

  return (
    <div className="border-b border-border py-2">
      <div className="mb-1 flex h-7 items-center justify-between px-4">
        <span className="resource-sidebar__label">프로젝트</span>
        <IconButton
          onClick={addProject}
          disabled={busy}
          label="로컬 Git 저장소 추가"
          className="h-7 w-7"
        >
          <Plus size={14} strokeWidth={1.5} />
        </IconButton>
      </div>
      <div>
        {projects.map((project) => (
          <div
            key={project.id}
            className="group resource-row relative"
            aria-selected={project.id === projectId}
          >
            <button
              onClick={() => selectProject(project.id)}
              className="w-full pr-7 text-left"
            >
              <div className="flex items-center gap-2">
                <FolderGit2 size={13} strokeWidth={1.5} className="shrink-0 text-muted" />
                <span className="truncate text-[12px] font-medium">{project.name}</span>
              </div>
              <div className="mt-1 truncate pl-[21px] font-mono text-[9.5px] text-faint">
                {project.repo_path}
              </div>
            </button>
            <button
              onClick={() => {
                if (window.confirm(`“${project.name}” 프로젝트를 목록에서 제거할까요?\n작업 기록과 Git 브랜치는 보존됩니다.`)) {
                  void archiveProject(project.id)
                }
              }}
              disabled={busy}
              title="프로젝트 목록에서 제거"
              className="absolute right-2 top-2 p-1 text-faint opacity-50 hover:text-danger hover:opacity-100 focus:opacity-100 disabled:opacity-30"
            >
              <Trash2 size={12} />
            </button>
          </div>
        ))}
        {projects.length === 0 && (
          <Button onClick={addProject} variant="ghost" className="resource-sidebar__action">
            <FolderGit2 size={14} strokeWidth={1.5} /> 로컬 저장소 추가
          </Button>
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
    <aside className="resource-sidebar">
      <ProjectPicker />
      <div className="flex min-h-0 flex-1 flex-col py-2">
        <div className="mb-1 flex h-7 items-center justify-between px-4">
          <span className="resource-sidebar__label">작업</span>
          <span className="font-mono text-[10px] text-faint">{tasks.length}</span>
        </div>
        <Button
          onClick={onNewTask}
          disabled={!projectId}
          compact
          className="resource-sidebar__action mb-2"
        >
          <Plus size={13} strokeWidth={1.5} /> 새 작업
        </Button>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {tasks.map((task) => (
            <button
              key={task.id}
              onClick={() => selectTask(task.id)}
              className="resource-row"
              aria-selected={task.id === taskId}
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
            <div className="px-4 py-5 text-center text-[11px] leading-relaxed text-faint">
              아직 작업이 없습니다.
              <br />먼저 작업 계약을 정의하세요.
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
    <div className="ui-dialog-backdrop">
      <form
        onSubmit={submit}
        className="ui-dialog w-full max-w-[620px]"
      >
        <div className="flex items-start justify-between border-b border-border px-5 py-4">
          <div>
            <div className="text-[10px] font-semibold tracking-[0.16em] text-muted">
              새 작업 · {project.name.toUpperCase()}
            </div>
            <h2 className="task-title mt-1 text-[16px] font-semibold">작업 계약 정의</h2>
          </div>
          <IconButton type="button" onClick={onClose} label="닫기">
            <X size={16} />
          </IconButton>
        </div>
        <div className="space-y-4 p-5">
          <Field label="제목">
            <Input
              autoFocus
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="세션 복구를 결정적으로 만들기"
            />
          </Field>
          <Field label="목표">
            <Textarea
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              placeholder="로컬 에이전트가 남겨야 할 결과를 설명하세요."
              rows={4}
            />
          </Field>
          <div className="grid grid-cols-[1fr_160px] gap-3">
            <Field label="수용 검증 명령">
              <Input
                value={acceptance}
                onChange={(event) => setAcceptance(event.target.value)}
                placeholder="python -m pytest -q"
                className="font-mono"
              />
            </Field>
            <Field label="기준 리프">
              <Input
                value={baseRef}
                onChange={(event) => setBaseRef(event.target.value)}
                className="font-mono"
              />
            </Field>
          </div>
        </div>
        <div className="flex items-center justify-between border-t border-border px-5 py-3">
          <span className="text-[10.5px] text-faint">작업을 생성한 뒤 작업 공간을 준비합니다.</span>
          <div className="flex gap-2">
            <Button type="button" onClick={onClose} variant="ghost">취소</Button>
            <Button
              disabled={busy || !title.trim() || !objective.trim() || !acceptance.trim()}
            >
              {busy ? '생성 중…' : '작업 생성'}
            </Button>
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
            <div className="task-label">작업 공간</div>
            <h3 className="mt-1 text-[14px] font-semibold">아직 워크트리가 없습니다</h3>
            <p className="mt-1 max-w-[560px] text-[11px] leading-relaxed text-faint">
              저장소와 기준 리프를 검증한 뒤 작업 소유 브랜치와 워크트리를 만듭니다.
            </p>
          </div>
          <button onClick={prepare} disabled={busy} className="task-primary-action">
            <FolderGit2 size={13} /> 작업 공간 준비
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
          <div className="task-label">작업 공간</div>
          <div className="mt-1 flex items-center gap-2">
            {workspace.state === 'preparing' ? (
              <Loader2 size={14} className="animate-spin" style={{ color: stateColor }} />
            ) : (
              <CircleDot size={14} style={{ color: stateColor }} />
            )}
            <h3 className="text-[14px] font-semibold">{stateLabel(workspace.state)}</h3>
            <span className="font-mono text-[10px] text-faint">{stateLabel(workspace.progress)}</span>
          </div>
        </div>
        {workspace.state === 'failed' && (
          <button onClick={retry} disabled={busy} className="task-primary-action">
            <RotateCcw size={13} /> 준비 재시도
          </button>
        )}
        {workspace.state === 'ready' && (
          <button onClick={inspect} disabled={busy} className="task-quiet-action">
            <RefreshCw size={12} /> 변경 확인
          </button>
        )}
      </div>

      {workspace.error && (
        <div className="error-strip mt-3 font-mono text-[10px] leading-relaxed">
          {workspace.error}
        </div>
      )}

      {workspace.state === 'failed' && (
        <div className="mt-3 border border-border bg-surface p-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="task-label">기준 리프 복구</div>
              <p className="mt-1 text-[10.5px] text-faint">
                기록된 리프가 없다면 재시도 전에 작업 계약을 수정하세요.
              </p>
            </div>
            {!editingBase && (
              <button onClick={() => setEditingBase(true)} className="task-quiet-action">
                리프 편집
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
                리프 저장
              </button>
            </div>
          )}
        </div>
      )}

      <dl className="mt-4 grid grid-cols-[100px_1fr] gap-x-4 gap-y-2 border-t border-border pt-3 text-[10.5px]">
        <dt className="text-faint">브랜치</dt>
        <dd className="flex min-w-0 items-center gap-1.5 font-mono text-muted">
          <GitBranch size={11} className="shrink-0" />
          <span className="truncate">{workspace.branch_name ?? '할당 중…'}</span>
        </dd>
        <dt className="text-faint">루트</dt>
        <dd className="truncate font-mono text-muted">{workspace.root_path ?? '할당 중…'}</dd>
      </dl>

      {workspace.state === 'ready' && gitStatus && (
        <div className="mt-3 flex items-center gap-2 border border-border bg-panel px-3 py-2 text-[10.5px]">
          {gitStatus.dirty ? <AlertTriangle size={13} className="text-warn" /> : <ShieldCheck size={13} className="text-ok" />}
          <span className={gitStatus.dirty ? 'text-warn' : 'text-ok'}>
            {gitStatus.dirty
              ? `추적 ${gitStatus.tracked_changes.length}건 · 미추적 ${gitStatus.untracked.length}건 · 미병합 ${gitStatus.unmerged.length}건`
              : '깨끗함 · 안전하게 보관 가능'}
          </span>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between border-t border-border pt-3">
        <span className="text-[10px] text-faint">
          안전 보관은 워크트리를 제거하고 브랜치는 보존합니다.
        </span>
        <div className="flex gap-2">
          {workspace.state === 'ready' && (
            <>
              <button
                onClick={() => archive(false)}
                disabled={busy}
                className="task-quiet-action"
              >
                <Archive size={12} /> 안전 보관
              </button>
              <button onClick={() => setDanger('force')} className="task-danger-link">
                강제 제거…
              </button>
            </>
          )}
          {workspace.state === 'archived' && workspace.branch_name && (
            <button onClick={() => setDanger('branch')} className="task-danger-link">
              브랜치 삭제…
            </button>
          )}
        </div>
      </div>

      {danger && (
        <div className="mt-3 flex items-center justify-between gap-4 border border-danger bg-panel px-3 py-2">
          <div className="text-[10.5px] leading-relaxed text-danger">
            {danger === 'force'
              ? '워크트리 변경을 즉시 폐기합니다. 브랜치는 보존됩니다.'
              : `${workspace.branch_name} 브랜치를 영구 삭제합니다.`}
          </div>
          <div className="flex shrink-0 gap-2">
            <button onClick={() => setDanger(null)} className="rounded px-2 py-1 text-[10.5px] text-muted">
              취소
            </button>
            <button
              onClick={() => {
                if (danger === 'force') archive(true)
                else deleteBranch()
                setDanger(null)
              }}
              className="ui-button ui-button--danger ui-button--compact"
            >
              확인
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
  const [runtimeView, setRuntimeView] = useState<'conversation' | 'context'>('conversation')
  const ready = task.workspace?.state === 'ready'
  const resumable = session?.status === 'created' || session?.status === 'idle'
  const selectedProfile = profiles.find((profile) => profile.id === selectedProfileId)
  const budget = session?.dispatch.budget ?? selectedProfile?.budget
  const usage = session?.dispatch.usage
  const adaptive = session?.dispatch.adaptive_decision
  const [priority, setPriority] = useState(selectedProfile?.budget.queue.priority ?? 0)
  const [queueTimeout, setQueueTimeout] = useState(
    Math.round((selectedProfile?.budget.queue.timeout_ms ?? 300000) / 1000)
  )

  useEffect(() => {
    if (!selectedProfile) return
    setPriority(selectedProfile.budget.queue.priority)
    setQueueTimeout(Math.round(selectedProfile.budget.queue.timeout_ms / 1000))
  }, [selectedProfile])

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
  const queueWait = useMemo(() => {
    const waiting = new Map<string, Record<string, unknown>>()
    for (const event of events) {
      if (event.kind !== 'agent_event') continue
      const kind = String(event.payload.kind ?? '')
      const operationId = String(event.payload.operation_id ?? '')
      if (!operationId) continue
      if (kind === 'resource_queue_wait') waiting.set(operationId, event.payload)
      if (kind === 'resource_lease_acquired' || kind === 'resource_queue_end') {
        waiting.delete(operationId)
      }
    }
    return [...waiting.values()].at(-1) ?? null
  }, [events])
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
          <div className="task-label">에이전트 세션</div>
          <div className="mt-1 flex items-center gap-2">
            <MessageSquare size={14} className="text-muted" />
            <h3 className="text-[14px] font-semibold">
              {session ? `시도 ${session.dispatch.attempt}` : '실행 시도 없음'}
            </h3>
            {session && (
              <span className="rounded-full border border-border-strong px-2 py-0.5 font-mono text-[9px] uppercase text-muted">
                {stateLabel(session.status)}
              </span>
            )}
            <span className={`flex items-center gap-1 text-[9.5px] ${connected ? 'text-ok' : 'text-faint'}`}>
              {connected ? <Wifi size={10} /> : <WifiOff size={10} />}
              {connected ? '연결됨' : '오프라인'}
            </span>
          </div>
        </div>
        <div className="flex items-end gap-2">
          <label>
            <span className="task-label">에이전트 프로필</span>
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
          <label>
            <span className="task-label">우선순위</span>
            <input
              type="number"
              value={priority}
              onChange={(event) => setPriority(Number(event.target.value))}
              disabled={busy}
              className="task-input mt-1 w-16"
            />
          </label>
          <label>
            <span className="task-label">대기열 초</span>
            <input
              type="number"
              min={1}
              value={queueTimeout}
              onChange={(event) => setQueueTimeout(Math.max(1, Number(event.target.value)))}
              disabled={busy}
              className="task-input mt-1 w-20"
            />
          </label>
          <button
            onClick={() => {
              if (session && resumable && !window.confirm('재개 가능한 세션을 중단하고 새 시도를 시작할까요?')) return
              void startSession({ priority, queue_timeout_ms: queueTimeout * 1000 })
            }}
            disabled={!ready || busy || active}
            className="task-primary-action"
            title={ready ? '영속화되는 새 디스패치 시도 생성' : '먼저 작업 공간을 준비하세요'}
          >
            <Play size={12} /> {session ? '새 시도' : '시작'}
          </button>
        </div>
      </div>

      {session && (
        <div className="mt-3 border-t border-border pt-3 font-mono text-[9px] text-faint">
          <div className="grid grid-cols-3 gap-2">
            <span className="truncate" title={session.id}>세션 · {session.id}</span>
            <span className="truncate" title={session.dispatch_id}>디스패치 · {session.dispatch_id}</span>
            <span className="truncate" title={session.agent_profile_id}>프로필 · {session.agent_profile_id}</span>
          </div>
          {(session.skills?.length ?? 0) > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-border pt-2">
              <span className="mr-1 text-faint">고정된 스킬</span>
              {session.skills?.map((skill) => (
                <span key={skill.skill_version_id} className="rounded border border-border-strong bg-raised px-1.5 py-0.5 text-muted">
                  {skill.namespace}:{skill.name} · v{skill.version} · {skill.activation_mode === 'auto' ? '자동' : '수동'}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {budget && (
        <div className="mt-3 grid grid-cols-4 gap-2 border border-border bg-base px-3 py-2 font-mono text-[9px] text-faint">
          <span>토큰 · {usage ? usage.prompt_tokens + usage.completion_tokens : 0}/{budget.dispatch.token_limit}</span>
          <span>단계 · {usage?.steps ?? 0}/{budget.dispatch.step_limit}</span>
          <span>시간 · {Math.round((usage?.active_time_ms ?? 0) / 1000)}초/{Math.round(budget.dispatch.time_limit_ms / 1000)}초</span>
          <span>워커 · {usage?.workers_started ?? 0}/{budget.workers.total_limit}</span>
          {session?.dispatch.budget_exhausted_reason && (
            <strong className="col-span-4 text-danger">
              예산 소진 · {session.dispatch.budget_exhausted_reason}
            </strong>
          )}
        </div>
      )}

      {adaptive?.effective && (
        <div className="mt-3 border border-border bg-panel px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[9px] uppercase tracking-[0.08em]">
            <span className="text-secondary">적응형 · {adaptive.task_class?.replaceAll('_', ' ')}</span>
            <span className="text-muted">정책 {adaptive.effective.worker_policy}</span>
            <span className="text-muted">
              역할 {adaptive.effective.worker_roles.length
                ? adaptive.effective.worker_roles.join(' → ')
                : '상위 에이전트만'}
            </span>
            <span className="text-muted">
              슬롯 {adaptive.scheduler?.model_generation.active ?? 0}/
              {adaptive.scheduler?.model_generation.cap ?? 1} · 대기 {adaptive.scheduler?.model_generation.queued ?? 0}
            </span>
          </div>
          {adaptive.retry?.failure_type && (
            <div className="mt-2 flex items-center justify-between gap-3 border-t border-border-subtle pt-2 text-[10px]">
              <span className="text-warn">
                재시도 · {adaptive.retry.failure_type.replaceAll('_', ' ')} → {adaptive.retry.strategy.replaceAll('_', ' ')}
              </span>
              <span className="font-mono text-[9px] text-faint">
                {adaptive.retry.allowed ? '제한된 재시도' : '수동 전용'}
              </span>
            </div>
          )}
        </div>
      )}

      {runtimeError && (
        <div className="error-strip mt-3">
          {runtimeError}
        </div>
      )}

      {queueWait && (
        <div className="mt-3 flex items-center justify-between gap-3 border border-warning bg-panel px-3 py-2 text-[10.5px] text-warn">
          <span>
            <strong>{String(queueWait.resource).replaceAll('_', ' ')}</strong> 대기 중
            {' · '}{queueWait.reason === 'capacity_exhausted'
              ? '로컬 용량 사용 중'
              : '우선순위가 높은 작업이 앞에 있음'}
          </span>
          <span className="shrink-0 font-mono text-[9.5px]">
            대기 {String(queueWait.position)} · 실행 {String(queueWait.active)}/{String(queueWait.cap)}
          </span>
        </div>
      )}

      <div className="ui-tabs mt-4 h-8 border-y border-border bg-base px-2">
        <button onClick={() => setRuntimeView('conversation')} className="ui-tab text-[10px]" aria-selected={runtimeView === 'conversation'}>
          대화와 활동
        </button>
        <button onClick={() => setRuntimeView('context')} disabled={!session} className="ui-tab text-[10px] disabled:opacity-30" aria-selected={runtimeView === 'context'}>
          컨텍스트 검사기
        </button>
        {session?.context && <span className="ml-auto font-mono text-[8.5px] text-faint">고정 ~{session.context.estimated_static_tokens.toLocaleString()} TOKENS</span>}
      </div>

      {runtimeView === 'context' && session ? <ContextInspector session={session} events={events} /> : <div className="task-session-console">
        <div className="task-transcript">
          {transcript.length === 0 ? (
            <div className="grid h-full place-items-center px-6 text-center text-[10.5px] leading-relaxed text-faint">
              {session
                ? '영속화된 세션에 연결한 뒤 다음 지시를 보내세요.'
                : '프로필을 선택하고 시도를 시작하세요. 실행 로그는 재시작 후에도 남습니다.'}
            </div>
          ) : transcript.map((item) => (
            <div key={item.key} className="task-message" data-role={item.role}>
              <span>{item.role === 'user' ? '나' : 'JANUS'}</span>
              <p>{item.content}</p>
            </div>
          ))}
        </div>
        <div className="task-activity">
          <div className="task-label mb-2">실시간 활동</div>
          {activity.length === 0 ? (
            <div className="text-[9.5px] text-faint">이벤트 없음</div>
          ) : activity.map((event) => (
            <div key={`${event.seq}-${event.kind}`} className="task-activity-row">
              <span>{event.seq}</span>
              <strong>{event.kind === 'skill_loaded' ? '스킬 로딩' : event.kind}</strong>
              <em>
                {event.kind === 'skill_loaded'
                  ? `${String(event.payload.namespace ?? '')}:${String(event.payload.name ?? '')} · ${Number(event.payload.prompt_tokens ?? 0)}토큰 · ${String(event.payload.reason ?? '')}`
                  : String(event.payload.kind ?? event.payload.type ?? '')}
              </em>
            </div>
          ))}
        </div>
      </div>}

      {approvals.map((approval) => (
        <div key={approval.id} className="mt-3 flex items-center justify-between gap-4 border border-warning bg-panel px-3 py-2">
          <div className="min-w-0 text-[10.5px] text-warn">
            이 작업 공간에서 <code className="font-mono">{approval.tool}</code> 도구를 승인할까요?
          </div>
          <div className="flex shrink-0 gap-2">
            <button onClick={() => respondApproval(approval.id, false)} className="task-quiet-action">거부</button>
            <button onClick={() => respondApproval(approval.id, true)} className="task-primary-action">승인</button>
          </div>
        </div>
      ))}

      <form onSubmit={submit} className="mt-3 flex gap-2 border-t border-border pt-3">
        {!connected && resumable && (
          <button type="button" onClick={() => void resumeSession()} disabled={busy} className="task-quiet-action">
            <Play size={11} /> 재개
          </button>
        )}
        <input
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          disabled={!connected || active || !resumable}
          placeholder={connected ? '다음 작업 지시 보내기…' : '계속하려면 세션을 재개하세요'}
          className="task-input mt-0 min-w-0 flex-1"
        />
        {active ? (
          <button type="button" onClick={cancelTurn} className="task-danger-link border border-danger">
            <Square size={11} /> 턴 취소
          </button>
        ) : (
          <button disabled={!connected || !message.trim() || !resumable} className="task-primary-action">
            <Send size={11} /> 보내기
          </button>
        )}
        {session && ['created', 'running', 'idle'].includes(session.status) && (
          <button type="button" onClick={() => void stopSession()} className="task-quiet-action">
            세션 중단
          </button>
        )}
      </form>
    </section>
  )
}

const CHANGE_LAYERS: ChangeLayer[] = ['committed', 'staged', 'unstaged', 'untracked']
const CHANGE_LAYER_LABEL: Record<ChangeLayer, string> = {
  committed: '커밋됨', staged: '스테이징됨', unstaged: '스테이징 안 됨', untracked: '미추적'
}

function diffLines(diff: string | null) {
  let oldLine = 0
  let newLine = 0
  let hunk: string | null = null
  return (diff ?? '').split('\n').map((text, index) => {
    const header = text.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
    if (header) {
      oldLine = Number(header[1])
      newLine = Number(header[2])
      hunk = text
      return { index, text, oldLine: null, newLine: null, hunk, header: true }
    }
    if (text.startsWith('+') && !text.startsWith('+++')) {
      return { index, text, oldLine: null, newLine: newLine++, hunk, header: false }
    }
    if (text.startsWith('-') && !text.startsWith('---')) {
      return { index, text, oldLine: oldLine++, newLine: null, hunk, header: false }
    }
    if (text.startsWith(' ')) {
      return { index, text, oldLine: oldLine++, newLine: newLine++, hunk, header: false }
    }
    return { index, text, oldLine: null, newLine: null, hunk, header: false }
  })
}

function ChangeSetCard() {
  const changeSet = useStore((state) => state.changeSet)
  const refresh = useStore((state) => state.inspectWorkspace)
  const [layer, setLayer] = useState<ChangeLayer>('unstaged')
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [commentLine, setCommentLine] = useState<ReturnType<typeof diffLines>[number] | null>(null)
  const [commentBody, setCommentBody] = useState('')
  const review = useStore((state) => state.review)
  const addComment = useStore((state) => state.addReviewComment)
  const resolveComment = useStore((state) => state.resolveReviewComment)
  const files = changeSet?.sections[layer] ?? []
  const selected: ChangeSetFile | undefined =
    files.find((item) => item.path === selectedPath) ?? files[0]
  const lines = useMemo(() => diffLines(selected?.diff ?? null), [selected?.diff])
  const hunks = lines.filter((item) => item.header)
  const comments = review?.comments.filter(
    (item) => item.layer === layer && item.file_path === selected?.path
  ) ?? []

  useEffect(() => {
    setSelectedPath(null)
    setCommentLine(null)
    setCommentBody('')
  }, [layer, changeSet?.head_commit, changeSet?.derived_at])

  if (!changeSet) return null
  return (
    <section className="task-card">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="task-label">Git 변경 목록</div>
          <div className="mt-1 font-mono text-[10px] text-faint">
            {changeSet.base_ref}…{changeSet.head_commit.slice(0, 8)} · Git에서 파생됨
          </div>
        </div>
        <button onClick={() => void refresh()} className="task-quiet-action">
          <RefreshCw size={12} /> diff 새로고침
        </button>
      </div>
      {changeSet.unmerged.length > 0 && (
        <div className="error-strip mt-3">
          <AlertTriangle size={12} className="mr-1.5 inline" />
          미병합 변경 {changeSet.unmerged.length}건으로 검토와 배포가 차단됩니다.
        </div>
      )}
      <div className="mt-4 flex gap-1 border-b border-border">
        {CHANGE_LAYERS.map((item) => (
          <button
            key={item}
            onClick={() => setLayer(item)}
            className="border-b-2 px-2.5 py-2 text-[10.5px] capitalize"
            style={{
              borderColor: item === layer ? 'var(--color-accent)' : 'transparent',
              color: item === layer ? 'var(--color-fg)' : 'var(--color-faint)'
            }}
          >
            {CHANGE_LAYER_LABEL[item]} <span className="ml-1 font-mono">{changeSet.counts[item]}</span>
          </button>
        ))}
      </div>
      <div className="grid min-h-[240px] grid-cols-[220px_minmax(0,1fr)] border-x border-b border-border">
        <div className="border-r border-border bg-raised/40 p-2">
          {files.map((file) => (
            <button
              key={`${file.status}:${file.old_path ?? ''}:${file.path}`}
              onClick={() => setSelectedPath(file.path)}
              className="mb-1 flex w-full items-start gap-2 rounded px-2 py-1.5 text-left hover:bg-panel"
              style={{ background: selected?.path === file.path ? 'var(--color-accent-soft)' : undefined }}
            >
              <span className="w-7 shrink-0 font-mono text-[9.5px] text-secondary">{file.status}</span>
              <span className="min-w-0 truncate font-mono text-[9.5px] text-muted" title={file.path}>
                {file.old_path ? `${file.old_path} → ${file.path}` : file.path}
              </span>
            </button>
          ))}
          {files.length === 0 && <div className="p-3 text-[10.5px] text-faint">{CHANGE_LAYER_LABEL[layer]} 변경이 없습니다.</div>}
        </div>
        <div className="min-w-0 overflow-auto bg-base p-3">
          {selected ? (
            selected.binary ? (
              <div className="text-[11px] text-faint">바이너리 파일 · {selected.diff_bytes}바이트</div>
            ) : (
              <>
                {selected.large && (
                  <div className="mb-2 text-[10px] text-warn">큰 diff · 미리보기 잘림</div>
                )}
                {hunks.length > 0 && (
                  <div className="sticky top-0 z-10 mb-2 flex gap-1 bg-base pb-2">
                    {hunks.map((item, index) => (
                      <button
                        key={item.index}
                        onClick={() => document.getElementById(`diff-${layer}-${item.index}`)?.scrollIntoView({ block: 'nearest' })}
                        className="rounded border border-border px-1.5 py-0.5 font-mono text-[8.5px] text-faint hover:text-fg"
                      >
                        변경 구간 {index + 1}
                      </button>
                    ))}
                  </div>
                )}
                <div className="min-w-max font-mono text-[9.5px] leading-4 text-muted">
                  {lines.map((item) => (
                    <button
                      id={`diff-${layer}-${item.index}`}
                      key={item.index}
                      onClick={() => {
                        if (item.oldLine || item.newLine) setCommentLine(item)
                      }}
                      className="block w-full whitespace-pre text-left hover:bg-hover"
                      style={{
                        color: item.text.startsWith('+') ? 'var(--color-ok)'
                          : item.text.startsWith('-') ? 'var(--color-danger)' : undefined
                      }}
                    >
                      <span className="mr-3 inline-block w-16 select-none text-right text-faint">
                        {item.oldLine ?? '·'} {item.newLine ?? '·'}
                      </span>{item.text || ' '}
                    </button>
                  ))}
                </div>
                {commentLine && (
                  <div className="sticky bottom-0 mt-3 flex gap-2 border border-border-strong bg-panel p-2">
                    <input
                      autoFocus value={commentBody}
                      onChange={(event) => setCommentBody(event.target.value)}
                      placeholder={`${commentLine.newLine ?? commentLine.oldLine}번 줄에 의견 추가`}
                      className="task-input mt-0 min-w-0 flex-1"
                    />
                    <button
                      disabled={!commentBody.trim()}
                      onClick={async () => {
                        await addComment({
                          revision: changeSet.revision, layer, file_path: selected.path,
                          old_line: commentLine.oldLine, new_line: commentLine.newLine,
                          hunk_header: commentLine.hunk, body: commentBody.trim()
                        })
                        setCommentLine(null)
                        setCommentBody('')
                      }}
                      className="task-primary-action"
                    >
                      <MessageSquare size={11} /> 추가
                    </button>
                    <button onClick={() => setCommentLine(null)} className="task-quiet-action">취소</button>
                  </div>
                )}
                {comments.length > 0 && (
                  <div className="mt-3 space-y-1 border-t border-border pt-2">
                    {comments.map((comment) => (
                      <div key={comment.id} className="flex items-center gap-2 rounded bg-panel px-2 py-1.5 text-[9.5px]">
                        <span className="font-mono text-faint">L{comment.new_line ?? comment.old_line}</span>
                        <span className={comment.resolved_at ? 'flex-1 line-through text-faint' : 'flex-1 text-fg'}>
                          {comment.body}
                        </span>
                        <button
                          onClick={() => void resolveComment(comment.id, !comment.resolved_at)}
                          className="task-quiet-action"
                        >
                          {comment.resolved_at ? '다시 열기' : '해결'}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )
          ) : (
            <div className="text-[10.5px] text-faint">변경된 파일을 선택하세요.</div>
          )}
        </div>
      </div>
    </section>
  )
}

function VerificationCard({ task }: { task: Task }) {
  const projects = useStore((state) => state.projects)
  const runs = useStore((state) => state.verificationRuns)
  const busy = useStore((state) => state.verificationBusy)
  const saveCommands = useStore((state) => state.setProjectVerificationCommands)
  const runAll = useStore((state) => state.runVerifications)
  const rerun = useStore((state) => state.rerunVerification)
  const load = useStore((state) => state.loadVerifications)
  const project = projects.find((item) => item.id === task.project_id)
  const [commands, setCommands] = useState<Record<'test' | 'lint' | 'typecheck', string>>({
    test: '', lint: '', typecheck: ''
  })

  useEffect(() => {
    const configured = project?.verification_commands ?? []
    setCommands({
      test: configured.find((item) => item.kind === 'test')?.command ?? '',
      lint: configured.find((item) => item.kind === 'lint')?.command ?? '',
      typecheck: configured.find((item) => item.kind === 'typecheck')?.command ?? ''
    })
  }, [project?.id, project?.updated_at])

  useEffect(() => {
    if (!runs.some((item) => item.status === 'queued' || item.status === 'running')) return
    const timer = window.setInterval(() => void load(), 500)
    return () => window.clearInterval(timer)
  }, [runs, load])

  const save = async () => {
    await saveCommands(
      (Object.entries(commands) as Array<[keyof typeof commands, string]>)
        .filter(([, command]) => command.trim())
        .map(([kind, command]) => ({ kind, command: command.trim() }))
    )
  }
  const latest = runs.slice(0, 8)
  return (
    <section className="task-card">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="task-label">독립 검증</div>
          <h3 className="mt-1 text-[14px] font-semibold">Janus 실행기</h3>
          <p className="mt-1 text-[10.5px] text-faint">
            에이전트의 주장은 표시일 뿐입니다. Janus 상태는 관측한 종료 코드로 판정합니다.
          </p>
        </div>
        <button onClick={() => void runAll()} disabled={busy} className="task-primary-action">
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          모두 실행
        </button>
      </div>
      <div className="mt-4 grid grid-cols-3 gap-2">
        {(['test', 'lint', 'typecheck'] as const).map((kind) => (
          <label key={kind}>
            <span className="task-label">{{ test: '테스트', lint: '린트', typecheck: '타입 검사' }[kind]}</span>
            <input
              value={commands[kind]}
              onChange={(event) => setCommands({ ...commands, [kind]: event.target.value })}
              placeholder={`${{ test: '테스트', lint: '린트', typecheck: '타입 검사' }[kind]} 명령`}
              className="task-input mt-1 font-mono text-[9.5px]"
            />
          </label>
        ))}
      </div>
      <div className="mt-2 flex items-center justify-between">
        <code className="truncate text-[9.5px] text-faint">수용 검증 · {task.acceptance_command}</code>
        <button onClick={() => void save()} disabled={busy} className="task-quiet-action">
          <Check size={11} /> 프로젝트 명령 저장
        </button>
      </div>
      <div className="mt-4 space-y-2 border-t border-border pt-3">
        {latest.map((run) => {
          const running = run.status === 'queued' || run.status === 'running'
          const color = run.status === 'passed'
            ? 'var(--color-ok)'
            : running ? 'var(--color-warn)' : 'var(--color-danger)'
          return (
            <div key={run.id} className="rounded-md border border-border bg-raised/40 px-3 py-2">
              <div className="flex items-center gap-2 text-[10.5px]">
                {running && <Loader2 size={11} className="animate-spin" style={{ color }} />}
                <span className="font-semibold" style={{ color }}>{stateLabel(run.status)}</span>
                <span className="rounded bg-panel px-1.5 py-0.5 font-mono text-[9px] text-muted">{({ test: '테스트', lint: '린트', typecheck: '타입 검사', acceptance: '수용 검증' } as Record<string, string>)[run.kind] ?? run.kind}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-[9.5px] text-faint">{run.command}</span>
                <span className="font-mono text-[9px] text-faint">
                  {run.duration_ms == null ? '—' : `${Math.round(run.duration_ms)}ms`} · 종료 {run.exit_code ?? '—'}
                </span>
                {!running && (
                  <button onClick={() => void rerun(run.id)} disabled={busy} className="task-quiet-action">
                    <RotateCcw size={10} /> 재실행
                  </button>
                )}
              </div>
              <div className="mt-1 flex gap-4 text-[9.5px] text-faint">
                <span>에이전트 주장: {run.agent_claim ?? '기록 없음'}</span>
                <span>Janus 결과: <b style={{ color }}>{stateLabel(run.status)}</b></span>
              </div>
              {(run.stdout || run.stderr || run.error) && (
                <pre className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap bg-base p-2 font-mono text-[9px] leading-4 text-muted">
                  {[run.stdout, run.stderr, run.error].filter(Boolean).join('\n')}
                </pre>
              )}
            </div>
          )
        })}
        {latest.length === 0 && (
          <div className="py-3 text-center text-[10.5px] text-faint">아직 독립 검증 실행이 없습니다.</div>
        )}
      </div>
    </section>
  )
}

function ReviewDecisionCard({ task }: { task: Task }) {
  const review = useStore((state) => state.review)
  const decide = useStore((state) => state.decideReview)
  const busy = useStore((state) => state.taskBusy)
  const [message, setMessage] = useState('')
  const unresolved = review?.comments.filter((item) => !item.resolved_at) ?? []
  const unmerged = review?.unmerged.length ?? 0

  return (
    <section className="task-card">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="task-label">검토 결정</div>
          <h3 className="mt-1 text-[14px] font-semibold">
            미해결 {unresolved.length}건 · 미병합 {unmerged}건
          </h3>
          <p className="mt-1 text-[10.5px] text-faint">
            수용은 현재 리비전의 독립 검증을 통과해야 합니다.
          </p>
        </div>
        <span className="font-mono text-[9px] text-faint">{review?.revision.slice(0, 10) ?? '로딩 중'}</span>
      </div>
      <textarea
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        placeholder="일괄 수정 지시"
        rows={2}
        className="task-input mt-3 resize-none"
      />
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          onClick={() => void decide({ decision: 'accept', message })}
          disabled={busy || unresolved.length > 0 || unmerged > 0}
          className="task-primary-action"
        >
          <Check size={11} /> 수용
        </button>
        <button
          onClick={() => void decide({ decision: 'request_changes', message })}
          disabled={busy || unresolved.length === 0 || unmerged > 0}
          className="task-quiet-action"
        >
          <MessageSquare size={11} /> 변경 요청 ({unresolved.length})
        </button>
        <button
          onClick={() => {
            const confirmation = window.prompt(`커밋하지 않은 모든 변경을 폐기하려면 작업 ID를 입력하세요:\n${task.id}`)
            if (confirmation !== task.id || !task.workspace) return
            void decide({
              decision: 'discard', message,
              confirm_workspace_id: task.workspace.id, confirm_discard: confirmation
            })
          }}
          disabled={busy || unmerged > 0}
          className="task-danger-link ml-auto"
          title={unmerged ? '먼저 미병합 변경을 수동으로 해결하세요' : '커밋하지 않은 변경 폐기'}
        >
          <X size={11} /> 변경 폐기…
        </button>
      </div>
      {review?.decisions.length ? (
        <div className="mt-3 border-t border-border pt-2 text-[9.5px] text-faint">
          최신: {{ accept: '수용', request_changes: '변경 요청', discard: '폐기' }[review.decisions[review.decisions.length - 1].decision] ?? review.decisions[review.decisions.length - 1].decision}
        </div>
      ) : null}
    </section>
  )
}

function TaskShippingCard() {
  const task = useStore((state) => state.task)
  const review = useStore((state) => state.review)
  const changeSet = useStore((state) => state.changeSet)
  const shipments = useStore((state) => state.shipments)
  const handoff = useStore((state) => state.shipHandoff)
  const pullRequestSnapshot = useStore((state) => state.taskPullRequest)
  const busy = useStore((state) => state.taskBusy)
  const commitTask = useStore((state) => state.commitTask)
  const pushTask = useStore((state) => state.pushTask)
  const loadHandoff = useStore((state) => state.loadShipHandoff)
  const createPullRequest = useStore((state) => state.createTaskPullRequest)
  const refreshPullRequest = useStore((state) => state.refreshTaskPullRequest)
  const archiveWorkspace = useStore((state) => state.archiveWorkspace)
  const [message, setMessage] = useState('')
  const [prTitle, setPrTitle] = useState(task?.title ?? '')
  const [prBody, setPrBody] = useState(task?.objective ?? '')
  const [prBase, setPrBase] = useState(task?.base_ref.replace(/^origin\//, '') ?? 'main')
  const [showCreatePr, setShowCreatePr] = useState(false)
  const latestDecision = review?.decisions[review.decisions.length - 1]
  const accepted = Boolean(
    latestDecision?.decision === 'accept' && latestDecision.revision === changeSet?.revision
  )
  const commit = [...shipments].reverse().find(
    (item) => item.action === 'commit' && item.status === 'completed'
  )
  const failedCommit = [...shipments].reverse().find(
    (item) => item.action === 'commit' && item.status === 'failed'
  )
  const pushed = commit && shipments.some(
    (item) => item.action === 'push' && item.status === 'completed'
      && item.commit_sha === commit.commit_sha
  )
  const failedPush = [...shipments].reverse().find(
    (item) => item.action === 'push' && item.status === 'failed'
  )
  const pullRequest = pullRequestSnapshot?.pull_request
  const checksPassed = Boolean(
    pullRequest?.checks.length && pullRequest.checks.every((check) =>
      ['SUCCESS', 'NEUTRAL', 'SKIPPED', 'PASS'].includes(String(check.state ?? check.bucket).toUpperCase())
    )
  )
  const releaseStages = [
    ['커밋', Boolean(commit)], ['푸시', Boolean(pushed)], ['PR', Boolean(pullRequest?.number)],
    ['검사', checksPassed], ['병합', pullRequest?.state === 'merged']
  ] as const

  useEffect(() => {
    if (commit && !handoff) void loadHandoff()
  }, [commit?.id, handoff, loadHandoff])

  return (
    <section className="task-card">
      <div className="task-label">작업 브랜치 출하</div>
      <div className="mt-2 grid grid-cols-5 gap-1.5" aria-label="배포 진행률">
        {releaseStages.map(([label, reached], index) => (
          <div key={label} className="relative">
            <div className="mb-1 flex items-center gap-1.5 font-mono text-[8px] uppercase tracking-[0.1em] text-faint">
              <span className="grid h-3.5 w-3.5 place-items-center rounded-full border text-[7px]" style={{
                borderColor: reached ? 'var(--color-ok)' : 'var(--color-border-strong)',
                color: reached ? 'var(--color-ok)' : 'var(--color-faint)'
              }}>{reached ? '✓' : index + 1}</span>
              {label}
            </div>
            <div className="h-px" style={{ background: reached ? 'var(--color-ok)' : 'var(--border-default)' }} />
          </div>
        ))}
      </div>
      <div className="mt-2 flex gap-2">
        <input
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="커밋 메시지"
          className="task-input mt-0 min-w-0 flex-1"
        />
        <button
          onClick={() => void commitTask(message.trim())}
          disabled={busy || !accepted || !message.trim()}
          className="task-primary-action"
        >
          <GitBranch size={11} /> Janus에서 커밋
        </button>
        <button
          onClick={() => void pushTask('origin')}
          disabled={busy || !commit || Boolean(pushed)}
          className="task-quiet-action"
        >
          <Send size={11} /> {pushed ? '푸시됨' : '브랜치 푸시'}
        </button>
      </div>
      <p className="mt-2 text-[9.5px] text-faint">
        Janus는 작업 워크트리 안에서만 커밋하며 main 체크아웃을 전환하거나 수정하지 않습니다.
      </p>
      {failedCommit && !commit && (
        <div className="error-strip mt-2 text-[9.5px]">
          커밋 실패 · {failedCommit.error}. 작업 공간의 변경은 그대로입니다. Git 신원 또는 디스크 접근 문제를 해결한 뒤 재시도하세요.
        </div>
      )}
      {failedPush && !pushed && (
        <div className="error-strip mt-2 text-[9.5px]">
          푸시 실패 · {failedPush.error}. 커밋과 작업 브랜치는 유지됩니다. 원격 접근 문제를 해결한 뒤 재시도하세요.
        </div>
      )}
      {commit && (
        <div className="mt-3 rounded-md border border-border bg-raised/40 p-2.5">
          <div className="flex items-center gap-2 text-[10px]">
            <span className="text-ok">커밋됨</span>
            <code className="text-muted">{commit.commit_sha.slice(0, 12)}</code>
            <code className="min-w-0 flex-1 truncate text-faint">{commit.branch_name}</code>
          </div>
          {handoff && (
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate bg-base px-2 py-1.5 text-[9px] text-faint">
                {handoff.local_apply_command}
              </code>
              <button
                onClick={() => void navigator.clipboard.writeText(handoff.local_apply_command)}
                className="task-quiet-action"
              >
                cherry-pick 복사
              </button>
            </div>
          )}
        </div>
      )}

      {pushed && !pullRequest?.number && (
        <div className="mt-3 border-t border-border pt-3">
          {!showCreatePr ? (
            <button onClick={() => setShowCreatePr(true)} className="task-primary-action">
              <GitPullRequest size={11} /> GitHub PR 생성
            </button>
          ) : (
            <div className="border border-border-strong bg-panel p-3">
              <div className="mb-2 font-mono text-[9px] uppercase tracking-[0.14em] text-secondary">
                검토 경계 게시
              </div>
              <div className="grid grid-cols-[1fr_110px] gap-2">
                <input value={prTitle} onChange={(event) => setPrTitle(event.target.value)} className="task-input mt-0" placeholder="PR 제목" />
                <input value={prBase} onChange={(event) => setPrBase(event.target.value)} className="task-input mt-0 font-mono" placeholder="기준 브랜치" />
              </div>
              <textarea value={prBody} onChange={(event) => setPrBody(event.target.value)} rows={3} className="task-input mt-2 resize-none" placeholder="변경 내용과 검증 방법" />
              <div className="mt-2 flex justify-end gap-2">
                <button onClick={() => setShowCreatePr(false)} className="task-quiet-action">취소</button>
                <button
                  onClick={() => void createPullRequest({ title: prTitle.trim(), body: prBody, base: prBase.trim(), draft: false })}
                  disabled={busy || !prTitle.trim() || !prBase.trim()}
                  className="task-primary-action"
                >
                  <GitPullRequest size={11} /> PR 생성
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {pullRequest && (
        <div className="mt-3 overflow-hidden border border-border bg-panel">
          <div className="flex items-start justify-between gap-3 border-b border-border px-3 py-2.5">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <GitPullRequest size={12} className="text-secondary" />
                <span className="font-mono text-[9px] text-faint">PR #{pullRequest.number ?? '—'}</span>
                <span className="rounded-full border px-1.5 py-0.5 font-mono text-[8px] uppercase" style={{
                  color: pullRequest.state === 'merged' ? 'var(--color-ok)' : pullRequest.state === 'error' ? 'var(--color-danger)' : 'var(--color-accent-fg)',
                  borderColor: pullRequest.state === 'merged' ? 'var(--success)' : pullRequest.state === 'error' ? 'var(--danger)' : 'var(--border-strong)'
                }}>{stateLabel(pullRequest.state)}</span>
              </div>
              <div className="mt-1 truncate text-[11px] font-medium">{pullRequest.title}</div>
              <div className="mt-1 font-mono text-[8px] text-faint">
                {pullRequest.head_branch} → {pullRequest.base_branch} · {pullRequest.merge_state ? stateLabel(pullRequest.merge_state) : '원격 대기 중'} · {pullRequest.review_decision ?? '검토 결정 없음'}
              </div>
            </div>
            <div className="flex shrink-0 gap-1">
              <button onClick={() => void refreshPullRequest()} disabled={busy || !pullRequest.number} className="task-quiet-action">
                <RefreshCw size={10} /> 새로고침
              </button>
              {pullRequest.url && (
                <button onClick={() => window.open(pullRequest.url!, '_blank', 'noopener,noreferrer')} className="task-quiet-action">
                  <ExternalLink size={10} /> 열기
                </button>
              )}
            </div>
          </div>

          {pullRequest.error && (
            <div className="border-b border-danger bg-panel px-3 py-2 text-[9.5px] text-danger">
              동기화 실패 · {pullRequest.error}. 저장된 PR과 CI 데이터는 유지됩니다.
            </div>
          )}
          <div className="grid grid-cols-2 gap-px bg-border">
            <div className="bg-panel p-3">
              <div className="task-label">CI 검사 · {pullRequest.checks.length}</div>
              <div className="mt-2 space-y-1.5">
                {pullRequest.checks.map((check) => {
                  const passed = ['SUCCESS', 'NEUTRAL', 'SKIPPED', 'PASS'].includes(String(check.state ?? check.bucket).toUpperCase())
                  return (
                    <div key={`${check.workflow}-${check.name}`} className="flex items-center justify-between gap-2 text-[9.5px]">
                      <span className="truncate text-muted">{check.workflow ? `${check.workflow} / ` : ''}{check.name}</span>
                      <span className={passed ? 'text-ok' : String(check.bucket).toLowerCase() === 'pending' ? 'text-warn' : 'text-danger'}>
                        {stateLabel(String(check.state ?? check.bucket ?? 'unknown'))}
                      </span>
                    </div>
                  )
                })}
                {pullRequest.checks.length === 0 && <div className="text-[9px] text-faint">보고된 검사가 없습니다.</div>}
              </div>
            </div>
            <div className="bg-panel p-3">
              <div className="task-label">실패 로그 · {pullRequest.failed_logs.length}</div>
              <div className="mt-2 space-y-1.5">
                {pullRequest.failed_logs.map((failure) => (
                  <details key={failure.run_id} className="border border-danger bg-base px-2 py-1.5">
                    <summary className="cursor-pointer truncate text-[9px] text-danger">{failure.name} · {failure.conclusion}</summary>
                    <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[8px] leading-relaxed text-muted">{failure.log || '실패 로그 출력이 없습니다.'}</pre>
                    {failure.truncated && <div className="mt-1 text-[8px] text-warn">영속화 안전 한도에서 로그가 잘렸습니다.</div>}
                  </details>
                ))}
                {pullRequest.failed_logs.length === 0 && <div className="text-[9px] text-faint">실패한 워크플로 로그가 없습니다.</div>}
              </div>
            </div>
          </div>
          {pullRequestSnapshot?.archive_reason && (
            <div className="flex items-center justify-between gap-3 border-t border-border px-3 py-2.5 text-[9.5px]">
              <span className={pullRequestSnapshot.archive_recommended ? 'text-ok' : 'text-faint'}>
                {pullRequestSnapshot.archive_reason}. 로컬 브랜치는 보존됩니다.
              </span>
              {pullRequestSnapshot.archive_recommended && (
                <button onClick={() => void archiveWorkspace(false)} disabled={busy} className="task-quiet-action">
                  <Archive size={10} /> 작업 공간 보관
                </button>
              )}
            </div>
          )}
        </div>
      )}
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
    <main className="workspace-surface min-w-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-[1120px] px-5 py-5">
        <div className="mb-4 flex items-start justify-between gap-6">
          <div className="min-w-0">
            <div className="mb-2 flex items-center gap-2">
              <StatusBadge status={task.status} />
              <span className="font-mono text-[9.5px] text-faint">{task.id}</span>
            </div>
            <h1 className="task-title text-[20px] font-semibold leading-tight tracking-[-0.01em]">
              {task.title}
            </h1>
          </div>
          <Button
            onClick={() => {
              if (window.confirm(`“${task.title}” 작업을 보관할까요?`)) archiveTask()
            }}
            disabled={busy || !canArchiveTask}
            title={canArchiveTask ? '작업 보관' : '먼저 작업 공간을 보관하세요'}
            variant="ghost"
            compact
          >
            <Archive size={12} /> 작업 보관
          </Button>
        </div>

        <TaskRunway status={task.status} />

        <div className="mt-4 grid grid-cols-[minmax(0,1fr)_300px] gap-0 border-x border-b border-border-subtle">
          <div className="space-y-4 p-4">
            <section className="task-card">
              <div className="task-label">목표</div>
              <p className="mt-2 whitespace-pre-wrap text-[13px] leading-6 text-fg">{task.objective}</p>
              <div className="mt-4 grid grid-cols-[1fr_150px] gap-4 border-t border-border-subtle pt-4">
                <div>
                  <div className="task-label">수용 검증</div>
                  <code className="mt-1.5 block border border-border bg-base px-3 py-2 font-mono text-[10.5px] text-secondary">
                    {task.acceptance_command}
                  </code>
                </div>
                <div>
                  <div className="task-label">기준 리프</div>
                  <div className="mt-1.5 flex items-center gap-1.5 border border-border px-3 py-2 font-mono text-[10.5px] text-muted">
                    <GitBranch size={11} /> {task.base_ref}
                  </div>
                </div>
              </div>
            </section>
            <WorkspaceCard task={task} />
            {task.workspace?.state === 'ready' && <ChangeSetCard />}
            {task.workspace?.state === 'ready' && <VerificationCard task={task} />}
            {task.workspace?.state === 'ready' && <ReviewDecisionCard task={task} />}
            {task.workspace?.state === 'ready' && <TaskShippingCard />}
          </div>

          <aside className="task-inspector space-y-0">
            <section className="task-card">
              <div className="task-label">다음 행동</div>
              <h3 className="mt-2 text-[14px] font-semibold">
                {!task.workspace
                  ? '작업 공간 준비'
                  : task.workspace.state === 'preparing'
                    ? '워크트리 생성 중'
                    : task.workspace.state === 'failed'
                      ? '준비 복구'
                      : task.workspace.state === 'ready'
                        ? !session
                          ? '에이전트 세션 시작'
                          : connected
                            ? '다음 지시 보내기'
                            : session.status === 'idle' || session.status === 'created'
                              ? '세션 재개'
                              : '새 시도 시작'
                        : '작업 공간 보관됨'}
              </h3>
              <p className="mt-2 text-[11px] leading-relaxed text-faint">
                {!task.workspace
                  ? 'Janus는 격리된 브랜치를 생성하기 전에 Git 저장소와 기준 리프를 검증합니다.'
                  : task.workspace.state === 'preparing'
                    ? `백그라운드 단계: ${stateLabel(task.workspace.progress)}`
                    : task.workspace.state === 'failed'
                      ? '저장소 또는 기준 리프를 수정한 뒤 기록된 소유권을 잃지 않고 재시도하세요.'
                      : task.workspace.state === 'ready'
                        ? !session
                          ? '에이전트 프로필을 선택하세요. Janus가 디스패치, 대화 기록, 실행 로그를 영속화합니다.'
                          : `시도 ${session.dispatch.attempt} · ${stateLabel(session.status)} · ${session.agent_profile_id}`
                        : '명시적으로 삭제할 때까지 브랜치는 유지됩니다.'}
              </p>
              <div className="mt-4 flex items-center gap-1.5 text-[10px] text-faint">
                <ChevronRight size={11} /> 최신 디스패치가 모든 실행 이벤트를 소유합니다
              </div>
            </section>
            <section className="task-card">
              <div className="task-label">소유권</div>
              <dl className="mt-3 space-y-2 text-[10.5px]">
                <div className="flex justify-between gap-3">
                  <dt className="text-faint">작업</dt>
                  <dd className="truncate font-mono text-muted">{task.id}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-faint">작업 공간</dt>
                  <dd className="truncate font-mono text-muted">{task.workspace?.id ?? '미생성'}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-faint">시도</dt>
                  <dd className="font-mono text-muted">{task.dispatches?.length ?? 0}</dd>
                </div>
                <div className="flex justify-between gap-3">
                  <dt className="text-faint">세션</dt>
                  <dd className="truncate font-mono text-muted">{session?.id ?? '미시작'}</dd>
                </div>
              </dl>
            </section>
          </aside>
          <div className="col-span-2 space-y-4 border-t border-border-subtle p-4">
            <TaskRuntimeCard task={task} />
            {task.workspace?.state === 'ready' && (
              <Suspense fallback={<section className="task-card text-[9px] text-faint">작업 개발 화면 로딩 중…</section>}>
                <TaskDevelopmentSurface task={task} />
              </Suspense>
            )}
          </div>
        </div>
      </div>
    </main>
  )
}

function EmptyTaskState({ hasProject, onNewTask }: { hasProject: boolean; onNewTask: () => void }) {
  const addProject = useStore((state) => state.addProjectFromPicker)
  return (
    <div className="workspace-surface grid min-w-0 flex-1 place-items-center px-8 text-center">
      <EmptyState
        title={hasProject ? '목표를 작업으로 만들기' : '로컬 Git 저장소 추가'}
        description={hasProject
          ? '에이전트가 작업을 시작하기 전에 결과, 수용 검증 명령, 기준 리프를 정의하세요.'
          : 'Janus는 main 체크아웃을 수정하지 않고 작업 소유 브랜치와 워크트리를 만듭니다.'}
        action={(
          <Button onClick={hasProject ? onNewTask : addProject}>
          <Plus size={13} /> {hasProject ? '새 작업' : '저장소 추가'}
          </Button>
        )}
      />
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
          <div className="toast-error">
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
