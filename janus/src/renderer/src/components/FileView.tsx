import { useEffect, useState } from 'react'
import Editor, { DiffEditor, loader, type OnMount } from '@monaco-editor/react'
import { ChevronRight, FileCode2, GitCompareArrows, LockKeyhole, X } from 'lucide-react'
import { languageIdFor, monaco } from './monacoSetup'
import { useStore } from '../store'

loader.config({ monaco })

/** unified diff에서 좌(원본)/우(수정) 텍스트를 재구성한다.
 *
 * 서버는 통짜 diff만 주므로 양쪽 전체 파일은 없다 — hunk의 컨텍스트 3줄과
 * 변경 줄로 양쪽 발췌를 만들고, hunk 사이는 @@ 헤더를 양쪽에 같은 줄로 넣어
 * 정렬을 유지한다. 어떤 diff 레이어(unstaged/staged/committed)든 원본 diff와
 * 항상 일치한다는 것이 이 방식의 존재 이유다.
 * ponytail: 전체 파일·실제 줄번호가 필요해지면 base 버전 API를 추가할 것. */
export function diffToSides(diff: string): { original: string; modified: string } | null {
  if (!diff.includes('@@')) {
    // untracked/신규 파일의 의사-diff: 헤더 뒤 +줄만 있고 hunk가 없다.
    const header = diff.indexOf('\n+++ ')
    if (header === -1) return null
    const body = diff.slice(diff.indexOf('\n', header + 1) + 1)
    const added = body.split('\n')
      .filter((line) => line.startsWith('+'))
      .map((line) => line.slice(1))
    if (added.length === 0) return null
    return { original: '', modified: added.join('\n') }
  }
  const original: string[] = []
  const modified: string[] = []
  let hunks = 0
  for (const line of diff.split('\n')) {
    if (line.startsWith('@@')) {
      hunks += 1
      if (hunks > 1) {
        original.push('')
        modified.push('')
      }
      original.push(line)
      modified.push(line)
    } else if (hunks === 0 || line.startsWith('\\')) {
      continue
    } else if (line === '') {
      continue // diff 말미 개행의 split 잔여 — 실제 빈 컨텍스트 줄은 ' ' 접두다
    } else if (line.startsWith('-')) {
      original.push(line.slice(1))
    } else if (line.startsWith('+')) {
      modified.push(line.slice(1))
    } else if (line.startsWith(' ')) {
      original.push(line.slice(1))
      modified.push(line.slice(1))
    } else {
      // 다음 파일 헤더(diff --git …) — rename 등 복수 파일 diff 경계
      hunks = 0
    }
  }
  if (hunks === 0 && original.length === 0) return null
  return { original: original.join('\n'), modified: modified.join('\n') }
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
  const sides = showingDiff && diff ? diffToSides(diff) : null
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
        {sides ? (
          <DiffEditor
            height="100%"
            language={languageId}
            theme="janus-ide"
            original={sides.original}
            modified={sides.modified}
            options={{
              readOnly: true,
              renderSideBySide: true,
              useInlineViewWhenSpaceIsLimited: false,
              hideUnchangedRegions: { enabled: false },
              minimap: { enabled: false },
              fontFamily: 'Geist Mono, JetBrains Mono, SFMono-Regular, Menlo, monospace',
              fontSize: 12.5,
              lineHeight: 20,
              lineNumbers: 'off',
              folding: false,
              renderLineHighlight: 'none',
              smoothScrolling: true,
              scrollBeyondLastLine: false,
              automaticLayout: true,
              padding: { top: 10, bottom: 18 }
            }}
          />
        ) : (
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
        )}
      </div>
      <footer className="file-ide__status">
        <span>Ln {cursor.line}, Col {cursor.column}</span>
        <span>Spaces: 2</span>
        <span>UTF-8</span>
        <span>{showingDiff ? (sides ? 'Git Diff · Split' : 'Git Diff') : language}</span>
        <span className="file-ide__readonly"><LockKeyhole size={10} /> 읽기 전용</span>
      </footer>
    </main>
  )
}
