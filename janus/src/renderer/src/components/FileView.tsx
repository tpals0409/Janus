import Editor, { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor/editor/editor.api'
import { FileCode2, X } from 'lucide-react'
import { useStore } from '../store'

loader.config({ monaco })

export default function FileView() {
  const openedFile = useStore((state) => state.openedFile)
  const closeFile = useStore((state) => state.closeFile)
  if (!openedFile) return null

  return (
    <main className="workspace-surface min-w-0 flex-1">
      <header className="workspace-toolbar">
        <div className="workspace-toolbar__icon"><FileCode2 size={15} /></div>
        <div className="workspace-toolbar__title min-w-0">
          <h2 className="truncate font-mono">{openedFile.path}</h2>
          <p>선택한 프로젝트 루트 · 읽기 전용</p>
        </div>
        <button onClick={closeFile} title="파일 닫기" className="task-quiet-action ml-auto">
          <X size={11} /> 닫기
        </button>
      </header>
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
            automaticLayout: true,
            padding: { top: 12 }
          }}
        />
      </div>
    </main>
  )
}
