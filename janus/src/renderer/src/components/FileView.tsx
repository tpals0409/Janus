import Editor, { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'
import { X } from 'lucide-react'
import { useStore } from '../store'

// CDN에서 받지 않고 번들된 monaco를 쓴다 (CSP가 외부 요청을 막는다)
loader.config({ monaco })

/** 워크스페이스 파일 읽기 전용 뷰어. path를 넘기면 monaco가 언어를 알아서 고른다. */
export default function FileView() {
  const openedFile = useStore((s) => s.openedFile)
  const closeFile = useStore((s) => s.closeFile)
  if (!openedFile) return null

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-[30px] shrink-0 items-center gap-2 border-b border-border bg-panel px-3">
        <span className="truncate font-mono text-[11.5px] text-muted">{openedFile.path}</span>
        <span className="text-[10px] text-faint">읽기 전용</span>
        <button onClick={closeFile} title="닫기 (그래프로)" className="ml-auto rounded p-1 text-faint hover:text-fg">
          <X size={13} />
        </button>
      </div>
      <div className="min-h-0 flex-1">
        <Editor
          height="100%"
          path={openedFile.path}
          theme="vs-dark"
          value={openedFile.content}
          options={{
            readOnly: true,
            minimap: { enabled: false },
            fontSize: 12,
            scrollBeyondLastLine: false,
            renderLineHighlight: 'none',
            padding: { top: 10 }
          }}
        />
      </div>
    </div>
  )
}
