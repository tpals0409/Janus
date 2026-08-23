import { useEffect, useState } from 'react'
import { ChevronRight, FileCode2, FileText, Folder, FolderGit2, FolderOpen, RefreshCw } from 'lucide-react'
import { useStore } from '../store'

function basename(path: string): string {
  return path.split('/').filter(Boolean).pop() ?? path
}

function isCodeFile(name: string): boolean {
  return /\.(?:c|cc|cpp|cs|css|go|h|hpp|html|java|js|jsx|json|kt|lua|md|php|py|rb|rs|sh|sql|swift|toml|ts|tsx|vue|xml|ya?ml)$/i.test(name)
}

/** 디렉토리 내용 목록 — 필요하면 로드하고, 항목을 그린다. 접기/펼치기는 DirRow 몫. */
function Dir({ rel, depth }: { rel: string; depth: number }) {
  const entries = useStore((s) => s.tree[rel])
  const loadDir = useStore((s) => s.loadDir)
  const openFile = useStore((s) => s.openFile)
  const openedFile = useStore((s) => s.openedFile)

  useEffect(() => {
    if (entries === undefined) loadDir(rel)
  }, [entries, rel, loadDir])

  if (!entries) return null
  if (!entries.length)
    return (
      <div className="py-0.5 text-[11px] text-faint" style={{ paddingLeft: 8 + depth * 14 }}>
        (비어 있음)
      </div>
    )

  return (
    <div>
      {entries.map((e) => {
        const child = rel ? `${rel}/${e.name}` : e.name
        return e.type === 'dir' ? (
          <DirRow key={child} rel={child} name={e.name} depth={depth} />
        ) : (
          <button
            key={child}
            onClick={() => openFile(child)}
            className="flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left hover:bg-raised"
            style={{
              paddingLeft: 8 + depth * 14,
              background: openedFile?.path === child ? 'var(--color-accent-soft)' : undefined
            }}
          >
            {isCodeFile(e.name) ? (
              <FileCode2 size={12} className="shrink-0 text-muted" />
            ) : (
              <FileText size={12} className="shrink-0 text-faint" />
            )}
            <span className="truncate text-[12px] text-muted">{e.name}</span>
          </button>
        )
      })}
    </div>
  )
}

function DirRow({ rel, name, depth }: { rel: string; name: string; depth: number }) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left hover:bg-raised"
        style={{ paddingLeft: 8 + depth * 14 }}
      >
        <ChevronRight
          size={11}
          className="shrink-0 text-faint transition-transform"
          style={{ transform: open ? 'rotate(90deg)' : 'none' }}
        />
        {open ? (
          <FolderOpen size={12} className="shrink-0 text-muted" />
        ) : (
          <Folder size={12} className="shrink-0 text-muted" />
        )}
        <span className="truncate text-[12px]">{name}</span>
      </button>
      {open && <Dir rel={rel} depth={depth + 1} />}
    </div>
  )
}

export default function FileTree() {
  const projects = useStore((s) => s.projects)
  const projectId = useStore((s) => s.projectId)
  const refreshTree = useStore((s) => s.refreshTree)
  const project = projects.find((item) => item.id === projectId)

  if (!project) {
    return (
      <div className="grid h-full place-items-center px-6 text-center">
        <div>
          <FolderGit2 size={22} className="mx-auto mb-2 text-faint" />
          <p className="text-[12px] text-muted">선택된 프로젝트가 없습니다</p>
          <p className="mt-1 text-[10.5px] leading-relaxed text-faint">
            작업 탭에서 프로젝트를 선택하면<br />이곳에 파일이 표시됩니다.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start gap-2 border-b border-border px-3 py-2.5" title={project.repo_path}>
        <FolderGit2 size={14} className="mt-0.5 shrink-0 text-muted" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[11.5px] font-semibold">{project.name}</div>
          <div className="truncate font-mono text-[9.5px] text-faint">
            {basename(project.repo_path)} · 프로젝트 루트
          </div>
        </div>
        <button
          onClick={refreshTree}
          title="새로고침"
          className="rounded p-1 text-faint hover:bg-raised hover:text-fg"
        >
          <RefreshCw size={11} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
        <Dir rel="" depth={0} />
      </div>
    </div>
  )
}
