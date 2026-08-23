import { useEffect, useState } from 'react'
import { ChevronRight, FileCode2, FileText, Folder, FolderGit2, FolderOpen, RefreshCw } from 'lucide-react'
import { useStore } from '../store'

function basename(path: string): string {
  return path.split('/').filter(Boolean).pop() ?? path
}

function isCodeFile(name: string): boolean {
  return /\.(?:c|cc|cpp|cs|css|go|h|hpp|html|java|js|jsx|json|kt|lua|md|php|py|rb|rs|sh|sql|swift|toml|ts|tsx|vue|xml|ya?ml)$/i.test(name)
}

function Directory({ path, depth }: { path: string; depth: number }) {
  const entries = useStore((state) => state.tree[path])
  const loadDir = useStore((state) => state.loadDir)
  const openFile = useStore((state) => state.openFile)
  const openedFile = useStore((state) => state.openedFile)

  useEffect(() => {
    if (entries === undefined) void loadDir(path)
  }, [entries, path, loadDir])

  if (!entries) return null
  if (entries.length === 0) {
    return <div className="py-1 text-[10px] text-faint" style={{ paddingLeft: 10 + depth * 14 }}>(비어 있음)</div>
  }

  return entries.map((entry) => {
    const child = path ? `${path}/${entry.name}` : entry.name
    return entry.type === 'dir' ? (
      <DirectoryRow key={child} path={child} name={entry.name} depth={depth} />
    ) : (
      <button
        key={child}
        onClick={() => void openFile(child)}
        className="resource-file-row"
        aria-selected={openedFile?.path === child}
        style={{ paddingLeft: 10 + depth * 14 }}
      >
        {isCodeFile(entry.name)
          ? <FileCode2 size={12} className="shrink-0 text-muted" />
          : <FileText size={12} className="shrink-0 text-faint" />}
        <span className="truncate">{entry.name}</span>
      </button>
    )
  })
}

function DirectoryRow({ path, name, depth }: { path: string; name: string; depth: number }) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button
        onClick={() => setOpen((value) => !value)}
        className="resource-file-row"
        aria-expanded={open}
        style={{ paddingLeft: 10 + depth * 14 }}
      >
        <ChevronRight size={11} className={`shrink-0 text-faint transition-transform ${open ? 'rotate-90' : ''}`} />
        {open
          ? <FolderOpen size={12} className="shrink-0 text-muted" />
          : <Folder size={12} className="shrink-0 text-muted" />}
        <span className="truncate">{name}</span>
      </button>
      {open && <Directory path={path} depth={depth + 1} />}
    </div>
  )
}

export default function FileTree() {
  const projects = useStore((state) => state.projects)
  const projectId = useStore((state) => state.projectId)
  const refreshTree = useStore((state) => state.refreshTree)
  const project = projects.find((item) => item.id === projectId)

  if (!project) {
    return (
      <div className="grid min-h-0 flex-1 place-items-center px-5 text-center">
        <div>
          <FolderGit2 size={20} className="mx-auto mb-2 text-faint" />
          <p className="text-[11px] text-muted">선택된 프로젝트가 없습니다</p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-start gap-2 border-b border-border px-3 py-2.5" title={project.repo_path}>
        <FolderGit2 size={13} className="mt-0.5 shrink-0 text-muted" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[11px] font-medium">{project.name}</div>
          <div className="truncate font-mono text-[9px] text-faint">{basename(project.repo_path)} · 프로젝트 루트</div>
        </div>
        <button onClick={refreshTree} title="파일 트리 새로고침" className="p-1 text-faint hover:text-fg">
          <RefreshCw size={11} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        <Directory path="" depth={0} />
      </div>
    </div>
  )
}
