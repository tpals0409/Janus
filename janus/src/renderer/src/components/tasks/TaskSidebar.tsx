import { useEffect, useState } from 'react'
import { Boxes, ChartNoAxesColumn, ChevronDown, Files, FlaskConical, FolderGit2, ListTodo, MessageSquarePlus, Plus, Trash2 } from 'lucide-react'
import { useStore } from '../../store'
import type { Project, Task, TaskStatus } from '../../types'
import FileTree from '../FileTree'
import { ConfirmDialog, Menu, MenuItem } from '../ui'

const STATUS: Record<TaskStatus, { label: string; color: string }> = {
  todo: { label: '할 일', color: 'var(--color-muted)' },
  preparing: { label: '준비 중', color: 'var(--color-warn)' },
  working: { label: '작업 중', color: 'var(--color-accent-fg)' },
  needs_you: { label: '응답 대기', color: 'var(--color-warn)' },
  review: { label: '검토', color: 'var(--color-ok)' },
  failed: { label: '실패', color: 'var(--color-danger)' },
}

function taskStatus(task: Task) {
  if (task.status === 'needs_you' && task.attention_reason === 'conversation_idle') {
    return { label: '대화 가능', color: 'var(--color-ok)' }
  }
  if (task.status === 'needs_you' && task.attention_reason === 'mockup_review') {
    return { label: '목업 검토 필요', color: 'var(--color-warn)' }
  }
  return STATUS[task.status]
}

function ProjectSwitcher() {
  const projects = useStore((state) => state.projects)
  const projectId = useStore((state) => state.projectId)
  const selectProject = useStore((state) => state.selectProject)
  const addProject = useStore((state) => state.addProjectFromPicker)
  const archiveProject = useStore((state) => state.archiveProject)
  const busy = useStore((state) => state.taskBusy)
  const [open, setOpen] = useState(false)
  const [pendingArchive, setPendingArchive] = useState<Project | null>(null)
  const current = projects.find((project) => project.id === projectId) ?? null

  // 바깥을 누르거나 Esc를 치면 닫힌다 — 팝오버가 갇히면 안 된다.
  useEffect(() => {
    if (!open) return
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent && event.key !== 'Escape') return
      setOpen(false)
    }
    window.addEventListener('keydown', close)
    window.addEventListener('pointerdown', close)
    return () => {
      window.removeEventListener('keydown', close)
      window.removeEventListener('pointerdown', close)
    }
  }, [open])

  return (
    <div className="project-switcher" onPointerDown={(event) => event.stopPropagation()}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        className="project-switcher__trigger"
      >
        <FolderGit2 size={13} strokeWidth={1.5} className="shrink-0 text-muted" />
        <span className="project-switcher__name">{current?.name ?? '프로젝트 선택'}</span>
        <ChevronDown size={13} strokeWidth={1.5} className="shrink-0 text-faint" />
      </button>
      {current && <div className="project-switcher__path">{current.repo_path}</div>}
      {open && (
        <Menu className="project-switcher__menu" aria-label="프로젝트 전환">
          {projects.map((project) => (
            <div key={project.id} className="project-switcher__row">
              <MenuItem
                onClick={() => {
                  setOpen(false)
                  void selectProject(project.id)
                }}
                aria-current={project.id === projectId}
                className="min-w-0 flex-1"
              >
                <span className="truncate">{project.name}</span>
              </MenuItem>
              <button
                type="button"
                onClick={() => {
                  setOpen(false)
                  setPendingArchive(project)
                }}
                disabled={busy}
                title="프로젝트 목록에서 제거"
                aria-label={`${project.name} 제거`}
                className="shrink-0 p-1 text-faint opacity-50 hover:text-danger hover:opacity-100 focus:opacity-100 disabled:opacity-30"
              >
                <Trash2 size={12} />
              </button>
            </div>
          ))}
          <MenuItem
            onClick={() => {
              setOpen(false)
              void addProject()
            }}
            disabled={busy}
          >
            <Plus size={13} strokeWidth={1.5} /> <span className="ml-1.5">로컬 저장소 추가</span>
          </MenuItem>
        </Menu>
      )}
      <ConfirmDialog open={Boolean(pendingArchive)} title={`“${pendingArchive?.name ?? ''}” 프로젝트를 제거할까요?`} description="Janus 목록에서만 제거합니다. 작업 기록과 Git 브랜치는 보존됩니다." confirmLabel="프로젝트 제거" danger onClose={() => setPendingArchive(null)} onConfirm={() => {
        const id = pendingArchive?.id
        setPendingArchive(null)
        if (id) void archiveProject(id)
      }} />
    </div>
  )
}

export default function TaskSidebar({
  onNewConversation,
  onNavigate
}: {
  onNewConversation?: () => void
  onNavigate?: (destination: string) => void
}) {
  const tasks = useStore((state) => state.tasks)
  const taskId = useStore((state) => state.taskId)
  const projectId = useStore((state) => state.projectId)
  const selectTask = useStore((state) => state.selectTask)
  const sidebarTab = useStore((state) => state.sidebarTab)
  const setSidebarTab = useStore((state) => state.setSidebarTab)
  const archiveTask = useStore((state) => state.archiveTask)
  const session = useStore((state) => state.taskSession)
  const stopTaskSession = useStore((state) => state.stopTaskSession)
  const busy = useStore((state) => state.taskBusy)
  const [pendingDelete, setPendingDelete] = useState<Task | null>(null)
  const deletingActiveSession = Boolean(
    pendingDelete?.id === taskId
    && session
    && ['created', 'running', 'idle'].includes(session.status)
  )
  return (
    <aside className="resource-sidebar">
      <ProjectSwitcher />
      <div className="task-sidebar-actions">
        <button type="button" onClick={onNewConversation} disabled={!projectId} className="task-sidebar-new-chat">
          <MessageSquarePlus size={15} strokeWidth={1.5} />
          <span>새 대화</span>
        </button>
      </div>
      <div className="grid h-8 shrink-0 grid-cols-2 border-b border-border p-0.5" role="tablist" aria-label="프로젝트 리소스">
        <button role="tab" aria-selected={sidebarTab === 'tasks'} onClick={() => setSidebarTab('tasks')} className="resource-mode-tab">
          <ListTodo size={11} /> 작업
          <span className="font-mono text-[10px] text-faint">{tasks.length}</span>
        </button>
        <button role="tab" aria-selected={sidebarTab === 'files'} onClick={() => setSidebarTab('files')} className="resource-mode-tab">
          <Files size={11} /> 파일
        </button>
      </div>
      {sidebarTab === 'files' ? <FileTree /> : (
      <div className="flex min-h-0 flex-1 flex-col py-2">
        <div className="min-h-0 flex-1 overflow-y-auto">
          {tasks.map((task) => (
            <div key={task.id} className="group resource-row relative" aria-selected={task.id === taskId}>
              <button onClick={() => selectTask(task.id)} className="w-full pr-7 text-left">
                <div className="flex items-start gap-2">
                  <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: taskStatus(task).color }} />
                  <span className="line-clamp-2 text-[12px] font-medium leading-snug">{task.title}</span>
                </div>
                <div className="mt-1.5 flex items-center justify-between pl-3.5 text-[10px]">
                  <span style={{ color: taskStatus(task).color }}>{taskStatus(task).label}</span>
                  <span className="font-mono text-faint">{task.base_ref}</span>
                </div>
              </button>
              <button type="button" onClick={() => setPendingDelete(task)} disabled={busy} title="작업 목록에서 제거" aria-label={`${task.title} 제거`} className="absolute right-2 top-2 p-1 text-faint opacity-50 hover:text-danger hover:opacity-100 focus:opacity-100 disabled:opacity-30">
                <Trash2 size={12} />
              </button>
            </div>
          ))}
          {projectId && tasks.length === 0 && <div className="px-4 py-5 text-center text-[11px] leading-relaxed text-faint">아직 실행 단위가 없습니다.<br />오른쪽 입력창에서 목표를 위임하세요.</div>}
        </div>
        <ConfirmDialog
          open={Boolean(pendingDelete)}
          title={deletingActiveSession
            ? `“${pendingDelete?.title ?? ''}” 세션을 먼저 중단할까요?`
            : `“${pendingDelete?.title ?? ''}” 작업을 제거할까요?`}
          description={deletingActiveSession
            ? '실행 중인 세션은 바로 제거할 수 없습니다. 세션을 중단한 뒤 작업 제거를 한 번 더 확인합니다.'
            : '목록에서만 제거합니다. 대화 기록과 Git 브랜치는 보존됩니다.'}
          confirmLabel={deletingActiveSession ? '세션 중단' : '작업 제거'}
          danger
          onClose={() => setPendingDelete(null)}
          onConfirm={() => {
            const target = pendingDelete
            if (!target) return
            setPendingDelete(null)
            if (deletingActiveSession) {
              void stopTaskSession().then(() => {
                if (!useStore.getState().taskRuntimeError) setPendingDelete(target)
              })
              return
            }
            void archiveTask(target.id)
          }}
        />
      </div>
      )}
      <nav className="task-sidebar-nav" aria-label="기본 탐색">
        <button onClick={() => onNavigate?.('agents')}><Boxes size={14} /> 에이전트</button>
        <button onClick={() => onNavigate?.('evals')}><FlaskConical size={14} /> 평가</button>
        <button onClick={() => onNavigate?.('monitor')}><ChartNoAxesColumn size={14} /> 모니터</button>
      </nav>
    </aside>
  )
}
