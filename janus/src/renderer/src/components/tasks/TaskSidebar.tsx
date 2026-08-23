import { useState } from 'react'
import { FolderGit2, Plus, Trash2 } from 'lucide-react'
import { useStore } from '../../store'
import type { Project, TaskStatus } from '../../types'
import { Button, ConfirmDialog, IconButton } from '../ui'

const STATUS: Record<TaskStatus, { label: string; color: string }> = {
  todo: { label: '할 일', color: 'var(--color-muted)' },
  preparing: { label: '준비 중', color: 'var(--color-warn)' },
  working: { label: '작업 중', color: 'var(--color-accent-fg)' },
  needs_you: { label: '확인 필요', color: 'var(--color-warn)' },
  review: { label: '검토', color: 'var(--color-ok)' },
  failed: { label: '실패', color: 'var(--color-danger)' },
}

function ProjectPicker() {
  const projects = useStore((state) => state.projects)
  const projectId = useStore((state) => state.projectId)
  const selectProject = useStore((state) => state.selectProject)
  const addProject = useStore((state) => state.addProjectFromPicker)
  const archiveProject = useStore((state) => state.archiveProject)
  const busy = useStore((state) => state.taskBusy)
  const [pendingArchive, setPendingArchive] = useState<Project | null>(null)
  return (
    <div className="border-b border-border py-2">
      <div className="mb-1 flex h-7 items-center justify-between px-4">
        <span className="resource-sidebar__label">프로젝트</span>
        <IconButton onClick={addProject} disabled={busy} label="로컬 Git 저장소 추가" className="h-7 w-7">
          <Plus size={14} strokeWidth={1.5} />
        </IconButton>
      </div>
      <div>
        {projects.map((project) => (
          <div key={project.id} className="group resource-row relative" aria-selected={project.id === projectId}>
            <button onClick={() => selectProject(project.id)} className="w-full pr-7 text-left">
              <div className="flex items-center gap-2">
                <FolderGit2 size={13} strokeWidth={1.5} className="shrink-0 text-muted" />
                <span className="truncate text-[12px] font-medium">{project.name}</span>
              </div>
              <div className="mt-1 truncate pl-[21px] font-mono text-[9.5px] text-faint">{project.repo_path}</div>
            </button>
            <button onClick={() => setPendingArchive(project)} disabled={busy} title="프로젝트 목록에서 제거" className="absolute right-2 top-2 p-1 text-faint opacity-50 hover:text-danger hover:opacity-100 focus:opacity-100 disabled:opacity-30">
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
      <ConfirmDialog open={Boolean(pendingArchive)} title={`“${pendingArchive?.name ?? ''}” 프로젝트를 제거할까요?`} description="Janus 목록에서만 제거합니다. 작업 기록과 Git 브랜치는 보존됩니다." confirmLabel="프로젝트 제거" danger onClose={() => setPendingArchive(null)} onConfirm={() => {
        const id = pendingArchive?.id
        setPendingArchive(null)
        if (id) void archiveProject(id)
      }} />
    </div>
  )
}

export default function TaskSidebar({ onNewTask }: { onNewTask: () => void }) {
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
        <Button onClick={onNewTask} disabled={!projectId} compact className="resource-sidebar__action mb-2">
          <Plus size={13} strokeWidth={1.5} /> 새 작업
        </Button>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {tasks.map((task) => (
            <button key={task.id} onClick={() => selectTask(task.id)} className="resource-row" aria-selected={task.id === taskId}>
              <div className="flex items-start gap-2">
                <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: STATUS[task.status].color }} />
                <span className="line-clamp-2 text-[12px] font-medium leading-snug">{task.title}</span>
              </div>
              <div className="mt-1.5 flex items-center justify-between pl-3.5 text-[9.5px]">
                <span style={{ color: STATUS[task.status].color }}>{STATUS[task.status].label}</span>
                <span className="font-mono text-faint">{task.base_ref}</span>
              </div>
            </button>
          ))}
          {projectId && tasks.length === 0 && <div className="px-4 py-5 text-center text-[11px] leading-relaxed text-faint">아직 작업이 없습니다.<br />먼저 작업 계약을 정의하세요.</div>}
        </div>
      </div>
    </aside>
  )
}
