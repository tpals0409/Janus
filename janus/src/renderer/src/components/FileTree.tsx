import { useEffect, useState } from 'react'
import { ChevronRight, FileText, Folder, FolderOpen, RefreshCw } from 'lucide-react'
import { useStore } from '../store'

function basename(p: string | null): string {
  if (!p) return '—'
  return p.split('/').filter(Boolean).pop() ?? p
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
            <FileText size={12} className="shrink-0 text-faint" />
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
          <FolderOpen size={12} className="shrink-0 text-accent-fg" />
        ) : (
          <Folder size={12} className="shrink-0 text-accent-fg" />
        )}
        <span className="truncate text-[12px]">{name}</span>
      </button>
      {open && <Dir rel={rel} depth={depth + 1} />}
    </div>
  )
}

export default function FileTree() {
  const workspace = useStore((s) => s.workspace)
  const pickWorkspace = useStore((s) => s.pickWorkspace)
  const refreshTree = useStore((s) => s.refreshTree)

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-1.5 border-b border-border px-3 py-2">
        <button
          onClick={pickWorkspace}
          title={workspace ?? ''}
          className="flex min-w-0 items-center gap-1.5 text-[12px] font-medium hover:text-accent-fg"
        >
          <FolderOpen size={13} className="shrink-0 text-accent-fg" />
          <span className="truncate">{basename(workspace)}</span>
        </button>
        <button
          onClick={refreshTree}
          title="새로고침"
          className="ml-auto rounded p-1 text-faint hover:text-fg"
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
