import { useEffect, useState } from 'react'
import Editor, { loader, type OnMount } from '@monaco-editor/react'
import * as monaco from 'monaco-editor/editor/editor.api'
import 'monaco-editor/languages/definitions/css/register'
import 'monaco-editor/languages/definitions/go/register'
import 'monaco-editor/languages/definitions/html/register'
import 'monaco-editor/languages/definitions/javascript/register'
import 'monaco-editor/languages/definitions/markdown/register'
import 'monaco-editor/languages/definitions/python/register'
import 'monaco-editor/languages/definitions/rust/register'
import 'monaco-editor/languages/definitions/shell/register'
import 'monaco-editor/languages/definitions/sql/register'
import 'monaco-editor/languages/definitions/typescript/register'
import 'monaco-editor/languages/definitions/xml/register'
import 'monaco-editor/languages/definitions/yaml/register'
import { ChevronRight, FileCode2, GitCompareArrows, LockKeyhole, X } from 'lucide-react'
import { useStore } from '../store'

loader.config({ monaco })

monaco.editor.defineTheme('janus-ide', {
  base: 'vs-dark',
  inherit: true,
  rules: [
    { token: 'diff.add', foreground: '83A995' },
    { token: 'diff.remove', foreground: 'C97878' },
    { token: 'diff.hunk', foreground: '7796AD', fontStyle: 'bold' },
    { token: 'diff.header', foreground: 'A3A7AA' }
  ],
  colors: {
    'editor.background': '#171819',
    'editor.foreground': '#d8d9d9',
    'editorLineNumber.foreground': '#515557',
    'editorLineNumber.activeForeground': '#a3a7aa',
    'editor.lineHighlightBackground': '#1d1f20',
    'editorCursor.foreground': '#91b5a2',
    'editor.selectionBackground': '#33433b',
    'editorIndentGuide.background1': '#242627',
    'editorIndentGuide.activeBackground1': '#414445',
    'editorGutter.background': '#171819',
    'minimap.background': '#151617'
  }
})

if (!monaco.languages.getLanguages().some((language) => language.id === 'janus-diff')) {
  monaco.languages.register({ id: 'janus-diff' })
  monaco.languages.setMonarchTokensProvider('janus-diff', {
    tokenizer: {
      root: [
        [/^\+\+\+.*$/, 'diff.header'],
        [/^---.*$/, 'diff.header'],
        [/^@@.*@@.*$/, 'diff.hunk'],
        [/^\+.*/, 'diff.add'],
        [/^-.*/, 'diff.remove'],
        [/^diff --git.*$/, 'diff.header'],
        [/^(?:index|new file mode|deleted file mode|similarity index).*$/, 'diff.header']
      ]
    }
  })
}

function basename(path: string): string {
  return path.split('/').filter(Boolean).pop() ?? path
}

function languageFor(path: string): string {
  const extension = path.split('.').pop()?.toLowerCase() ?? ''
  return ({
    ts: 'TypeScript', tsx: 'TypeScript React', js: 'JavaScript', jsx: 'JavaScript React',
    css: 'CSS', scss: 'SCSS', html: 'HTML', json: 'JSON', md: 'Markdown', py: 'Python',
    rs: 'Rust', go: 'Go', swift: 'Swift', sh: 'Shell', yaml: 'YAML', yml: 'YAML',
    xml: 'XML', sql: 'SQL', toml: 'TOML'
  } as Record<string, string>)[extension] ?? (extension ? extension.toUpperCase() : 'Plain Text')
}

function languageIdFor(path: string): string {
  const extension = path.split('.').pop()?.toLowerCase() ?? ''
  return ({
    ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
    css: 'css', scss: 'scss', html: 'html', json: 'json', md: 'markdown', py: 'python',
    rs: 'rust', go: 'go', sh: 'shell', bash: 'shell', yaml: 'yaml', yml: 'yaml',
    xml: 'xml', sql: 'sql'
  } as Record<string, string>)[extension] ?? 'plaintext'
}

export default function FileView() {
  const openedFile = useStore((state) => state.openedFile)
  const closeFile = useStore((state) => state.closeFile)
  const changeSet = useStore((state) => state.changeSet)
  const [cursor, setCursor] = useState({ line: 1, column: 1 })
  const [view, setView] = useState<'code' | 'diff'>('code')
  useEffect(() => {
    setView('code')
    setCursor({ line: 1, column: 1 })
  }, [openedFile?.path])
  if (!openedFile) return null

  const parts = openedFile.path.split('/').filter(Boolean)
  const language = languageFor(openedFile.path)
  const languageId = languageIdFor(openedFile.path)
  const changedFile = changeSet
    ? Object.values(changeSet.sections).flat().find((file) => file.path === openedFile.path)
    : undefined
  const diff = changedFile?.diff ?? null
  const showingDiff = view === 'diff' && Boolean(changedFile)
  const editorValue = showingDiff
    ? diff ?? `Binary file changed · ${changedFile?.diff_bytes ?? 0} bytes`
    : openedFile.content

  const mount: OnMount = (editor) => {
    const update = () => {
      const position = editor.getPosition()
      if (position) setCursor({ line: position.lineNumber, column: position.column })
    }
    update()
    editor.onDidChangeCursorPosition(update)
  }

  return (
    <main className="file-ide min-w-0 flex-1">
      <div className="file-ide__tabs" role="tablist" aria-label="열린 파일">
        <div className="file-ide__tab" role="tab" aria-selected="true">
          <FileCode2 size={13} aria-hidden="true" />
          <span>{basename(openedFile.path)}</span>
          <button onClick={closeFile} title="파일 닫기" aria-label={`${basename(openedFile.path)} 닫기`}>
            <X size={12} />
          </button>
        </div>
        <div className="file-ide__view-toggle" role="group" aria-label="파일 보기 방식">
          <button aria-pressed={!showingDiff} onClick={() => setView('code')}>
            <FileCode2 size={12} /> 코드
          </button>
          <button
            aria-pressed={showingDiff}
            onClick={() => setView('diff')}
            disabled={!changedFile}
            title={changedFile ? `${changedFile.status} · ${changedFile.diff_bytes} bytes` : 'Git 변경 없음'}
          >
            <GitCompareArrows size={12} /> Git diff
          </button>
        </div>
      </div>
      <nav className="file-ide__breadcrumbs" aria-label="파일 경로">
        {parts.map((part, index) => (
          <span key={`${part}-${index}`}>
            {index > 0 && <ChevronRight size={11} aria-hidden="true" />}
            <span>{part}</span>
          </span>
        ))}
      </nav>
      <div className="file-ide__editor">
        <Editor
          height="100%"
          path={showingDiff ? `${openedFile.path}.janus-diff` : openedFile.path}
          language={showingDiff ? 'janus-diff' : languageId}
          theme="janus-ide"
          value={editorValue}
          onMount={mount}
          options={{
            readOnly: true,
            readOnlyMessage: { value: '프로젝트 파일 탐색에서는 읽기 전용입니다.' },
            minimap: { enabled: !showingDiff, scale: 1, showSlider: 'mouseover', maxColumn: 88 },
            fontFamily: 'Geist Mono, JetBrains Mono, SFMono-Regular, Menlo, monospace',
            fontSize: 12.5,
            lineHeight: 20,
            lineNumbers: showingDiff ? 'off' : 'on',
            lineNumbersMinChars: 4,
            glyphMargin: !showingDiff,
            folding: !showingDiff,
            guides: { indentation: true, bracketPairs: true, highlightActiveIndentation: true },
            bracketPairColorization: { enabled: true },
            renderLineHighlight: 'all',
            renderWhitespace: 'selection',
            cursorBlinking: 'smooth',
            smoothScrolling: true,
            stickyScroll: { enabled: true },
            scrollBeyondLastLine: false,
            wordWrap: 'off',
            automaticLayout: true,
            padding: { top: 10, bottom: 18 }
          }}
        />
      </div>
      <footer className="file-ide__status">
        <span>Ln {cursor.line}, Col {cursor.column}</span>
        <span>Spaces: 2</span>
        <span>UTF-8</span>
        <span>{showingDiff ? 'Git Diff' : language}</span>
        <span className="file-ide__readonly"><LockKeyhole size={10} /> 읽기 전용</span>
      </footer>
    </main>
  )
}
