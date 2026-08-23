import Editor, { loader } from '@monaco-editor/react'
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
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Camera, Clipboard, Code2, Columns2, ExternalLink, FileSearch, FolderTree,
  MousePointer2, Play, Save, Search, Square, Terminal as TerminalIcon
} from 'lucide-react'
import type { Task, TaskBrowserInspection, TaskBrowserStatus } from '../../types'
import { useDomainEvent } from '../../domainEvents'
import { janusApi as api } from '../../api'
import { Button, EmptyState } from '../ui'

loader.config({ monaco })

interface TerminalRecord {
  id: string; task_id: string; pane_id: 'primary' | 'secondary'; cwd: string
  state: 'running' | 'exited' | 'stopped'; exit_code: number | null
  output: string; output_offset: number
}

interface OpenFile {
  path: string; content: string; size: number; mtime_ns: number
}

interface SearchMatch { path: string; line: number; text: string }
type SurfaceTab = 'terminal' | 'editor' | 'preview'

function stripAnsi(value: string): string {
  // Covers CSI color/cursor sequences emitted by ordinary shells without mutating persistence.
  return value.replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, '').replace(/\r/g, '')
}

function WindowedOutput({ value }: { value: string }) {
  const lines = useMemo(() => stripAnsi(value).split('\n'), [value])
  const visible = lines.slice(-400)
  const omitted = Math.max(0, lines.length - visible.length)
  const bottom = useRef<HTMLDivElement>(null)
  useEffect(() => bottom.current?.scrollIntoView({ block: 'end' }), [value])
  return (
    <div className="h-full overflow-auto bg-base p-2 font-mono text-[10px] leading-[1.55] text-secondary">
      {omitted > 0 && <div className="mb-2 text-faint">… 이전 {omitted}줄 숨김</div>}
      <pre className="whitespace-pre-wrap break-words">{visible.join('\n')}</pre>
      <div ref={bottom} />
    </div>
  )
}

function TerminalPane({
  terminal, onInput, onStop
}: {
  terminal: TerminalRecord
  onInput: (terminal: TerminalRecord, data: string) => Promise<void>
  onStop: (terminal: TerminalRecord) => Promise<void>
}) {
  const [command, setCommand] = useState('')
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!command || terminal.state !== 'running') return
    const value = command
    setCommand('')
    await onInput(terminal, `${value}\n`)
  }
  return (
    <div className="flex min-w-0 flex-1 flex-col border border-border bg-base">
      <div className="flex h-7 items-center gap-2 border-b border-border px-2 font-mono text-[8.5px] text-faint">
        <TerminalIcon size={10} /> {terminal.pane_id === 'primary' ? '기본' : '보조'} · {{ running: '실행 중', exited: '종료됨', stopped: '중단됨' }[terminal.state]}
        <span className="ml-auto truncate">{terminal.cwd}</span>
        <button onClick={() => void navigator.clipboard.writeText(stripAnsi(terminal.output))} title="출력 복사" className="hover:text-fg"><Clipboard size={10} /></button>
        {terminal.state === 'running' && <button onClick={() => void onStop(terminal)} title="셸 중단" className="hover:text-danger"><Square size={9} /></button>}
      </div>
      <div className="min-h-0 flex-1"><WindowedOutput value={terminal.output} /></div>
      <form onSubmit={submit} className="flex border-t border-border">
        <span className="px-2 py-1.5 font-mono text-[10px] text-secondary">$</span>
        <input
          value={command} onChange={(event) => setCommand(event.target.value)}
          disabled={terminal.state !== 'running'} placeholder="이 작업 워크트리에서 실행"
          className="min-w-0 flex-1 bg-transparent py-1.5 pr-2 font-mono text-[10px] outline-none disabled:opacity-40"
        />
      </form>
    </div>
  )
}

export default function TaskDevelopmentSurface({ task }: { task: Task }) {
  const storageKey = `janus.dev-surface.${task.id}`
  const restored = useMemo(() => {
    try { return JSON.parse(localStorage.getItem(storageKey) ?? '{}') as Record<string, unknown> }
    catch { return {} }
  }, [storageKey])
  const [tab, setTab] = useState<SurfaceTab>((restored.tab as SurfaceTab) ?? 'terminal')
  const [split, setSplit] = useState(Boolean(restored.split))
  const [terminals, setTerminals] = useState<TerminalRecord[]>([])
  const [file, setFile] = useState<OpenFile | null>(null)
  const [draft, setDraft] = useState('')
  const [dirty, setDirty] = useState(false)
  const [search, setSearch] = useState('')
  const [matches, setMatches] = useState<SearchMatch[]>([])
  const [previewUrl, setPreviewUrl] = useState(String(restored.previewUrl ?? 'http://localhost:5173'))
  const [browser, setBrowser] = useState<TaskBrowserStatus | null>(null)
  const [inspection, setInspection] = useState<TaskBrowserInspection | null>(null)
  const [screenshot, setScreenshot] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const remember = useCallback((patch: Record<string, unknown>) => {
    let current: Record<string, unknown> = {}
    try { current = JSON.parse(localStorage.getItem(storageKey) ?? '{}') } catch { /* reset */ }
    localStorage.setItem(storageKey, JSON.stringify({ ...current, ...patch }))
  }, [storageKey])

  const loadTerminals = useCallback(async () => {
    try {
      const value = await api<TerminalRecord[]>(`/tasks/${task.id}/terminals`)
      setTerminals(value)
    } catch (reason) { setError(String(reason)) }
  }, [task.id])

  useEffect(() => { void loadTerminals() }, [loadTerminals])
  useDomainEvent('terminal', (event) => {
    if (event.task_id !== task.id || typeof event.terminal_id !== 'string') return
    setTerminals((current) => current.map((terminal) => {
      if (terminal.id !== event.terminal_id) return terminal
      if (event.event === 'exit') {
        return {
          ...terminal,
          state: String(event.state) as TerminalRecord['state'],
          exit_code: typeof event.exit_code === 'number' ? event.exit_code : null
        }
      }
      if (event.event !== 'output' || typeof event.output !== 'string' || typeof event.output_offset !== 'number') {
        return terminal
      }
      const chunkStart = event.output_offset - event.output.length
      if (chunkStart !== terminal.output_offset) {
        void loadTerminals()
        return terminal
      }
      return {
        ...terminal,
        output: `${terminal.output}${event.output}`.slice(-262_144),
        output_offset: event.output_offset
      }
    }))
  })

  const openTerminal = async (pane: 'primary' | 'secondary') => {
    setError(null)
    try {
      await api(`/tasks/${task.id}/terminals`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pane_id: pane })
      })
      await loadTerminals()
    } catch (reason) { setError(String(reason)) }
  }
  const inputTerminal = async (terminal: TerminalRecord, data: string) => {
    try {
      await api(`/tasks/${task.id}/terminals/${terminal.id}/input`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ data })
      })
    } catch (reason) { setError(String(reason)) }
  }
  const stopTerminal = async (terminal: TerminalRecord) => {
    try {
      await api(`/tasks/${task.id}/terminals/${terminal.id}`, { method: 'DELETE' })
      await loadTerminals()
    } catch (reason) { setError(String(reason)) }
  }

  const openFile = useCallback(async (path: string) => {
    try {
      const value = await api<OpenFile>(`/tasks/${task.id}/development/file?path=${encodeURIComponent(path)}`)
      setFile(value); setDraft(value.content); setDirty(false); setTab('editor')
      remember({ tab: 'editor', openFile: path })
    } catch (reason) { setError(String(reason)) }
  }, [task.id, remember])
  useEffect(() => {
    const path = restored.openFile
    if (typeof path === 'string') void openFile(path)
    // Restore exactly once for this Task; openFile intentionally excluded.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id])

  const saveFile = useCallback(async () => {
    if (!file || !dirty) return
    try {
      const saved = await api<{ mtime_ns: number }>(`/tasks/${task.id}/development/file`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: file.path, content: draft, expected_mtime_ns: file.mtime_ns })
      })
      setFile({ ...file, content: draft, mtime_ns: saved.mtime_ns }); setDirty(false)
    } catch (reason) { setError(String(reason)) }
  }, [task.id, file, draft, dirty])

  const searchFiles = async (event?: FormEvent) => {
    event?.preventDefault()
    if (!search.trim()) return
    try {
      const value = await api<{ matches: SearchMatch[] }>(`/tasks/${task.id}/development/search?q=${encodeURIComponent(search.trim())}`)
      setMatches(value.matches)
    } catch (reason) { setError(String(reason)) }
  }

  const refreshBrowser = useCallback(async () => {
    if (!window.janus) return
    try { setBrowser(await window.janus.taskBrowserStatus(task.id)) }
    catch (reason) { setError(String(reason)) }
  }, [task.id])
  useEffect(() => {
    if (tab !== 'preview' || !window.janus) return
    void refreshBrowser()
    const timer = window.setInterval(() => void refreshBrowser(), 1000)
    return () => window.clearInterval(timer)
  }, [tab, refreshBrowser])

  const openPreview = async () => {
    if (!window.janus) return setError('작업 미리보기는 Electron 앱에서 사용할 수 있습니다')
    try {
      const value = await window.janus.taskBrowserOpen({ taskId: task.id, url: previewUrl })
      setBrowser(value); remember({ previewUrl })
    } catch (reason) { setError(String(reason)) }
  }
  const capture = async () => {
    try { setScreenshot((await window.janus!.taskBrowserScreenshot(task.id)).dataUrl) }
    catch (reason) { setError(String(reason)) }
  }
  const inspect = async () => {
    try { setInspection(await window.janus!.taskBrowserInspect(task.id)) }
    catch (reason) { setError(String(reason)) }
  }

  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey)) return
      if (event.key.toLowerCase() === 'j') { event.preventDefault(); setTab('terminal'); remember({ tab: 'terminal' }) }
      if (event.key.toLowerCase() === 'p') { event.preventDefault(); setTab('editor'); remember({ tab: 'editor' }) }
      if (event.key.toLowerCase() === 'b' && event.shiftKey) { event.preventDefault(); setTab('preview'); remember({ tab: 'preview' }) }
      if (event.key.toLowerCase() === 's' && tab === 'editor') { event.preventDefault(); void saveFile() }
    }
    window.addEventListener('keydown', shortcut)
    return () => window.removeEventListener('keydown', shortcut)
  }, [tab, remember, saveFile])

  const chooseTab = (next: SurfaceTab) => { setTab(next); remember({ tab: next }) }
  const primary = terminals.find((item) => item.pane_id === 'primary')
  const secondary = terminals.find((item) => item.pane_id === 'secondary')

  return (
    <section className="task-card overflow-hidden p-0">
      <div className="flex h-9 items-center border-b border-border px-2">
        <div className="mr-3 font-mono text-[8px] uppercase tracking-[0.16em] text-muted">작업 개발 화면</div>
        {([
          ['terminal', TerminalIcon, '터미널', '⌘J'], ['editor', Code2, '편집기', '⌘P'],
          ['preview', ExternalLink, '미리보기', '⌘⇧B']
        ] as const).map(([id, Icon, label, key]) => (
          <button key={id} onClick={() => chooseTab(id)} className="ui-tab flex h-full items-center gap-1.5 px-2.5 text-[10px]" aria-selected={tab === id}>
            <Icon size={11} />{label}<kbd className="font-mono text-[7px] text-faint">{key}</kbd>
          </button>
        ))}
        <span className="ml-auto max-w-[280px] truncate font-mono text-[8px] text-faint">{task.workspace?.root_path}</span>
      </div>
      {error && <div className="error-strip border-x-0 border-t-0 text-[9px]">{error}</div>}

      {tab === 'terminal' && (
        <div className="flex h-[330px] flex-col">
          <div className="flex h-8 items-center gap-1.5 border-b border-border px-2">
            {!primary && <Button onClick={() => void openTerminal('primary')} compact><Play size={9} /> 터미널 열기</Button>}
            {primary && <button onClick={() => { setSplit(!split); remember({ split: !split }); if (!split && !secondary) void openTerminal('secondary') }} className="task-quiet-action"><Columns2 size={9} /> {split ? '단일 화면' : '분할'}</button>}
            <span className="ml-auto font-mono text-[8px] text-faint">PTY · 작업 공간에 cwd 고정 · 최근 출력 400줄</span>
          </div>
          <div className="flex min-h-0 flex-1 gap-px bg-border p-px">
            {primary ? <TerminalPane terminal={primary} onInput={inputTerminal} onStop={stopTerminal} /> : <EmptyState title="터미널 없음" description="이 작업 소유의 셸을 여세요." />}
            {split && secondary && <TerminalPane terminal={secondary} onInput={inputTerminal} onStop={stopTerminal} />}
          </div>
        </div>
      )}

      {tab === 'editor' && (
        <div className="grid h-[420px] grid-cols-[230px_minmax(0,1fr)]">
          <aside className="min-h-0 border-r border-border bg-panel">
            <form onSubmit={(event) => void searchFiles(event)} className="flex border-b border-border p-2">
              <Search size={11} className="mr-1.5 mt-1.5 text-faint" />
              <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="작업 공간 텍스트 검색" className="min-w-0 flex-1 bg-transparent text-[9.5px] outline-none" />
            </form>
            <div className="h-[calc(100%-37px)] overflow-auto p-1.5">
              {matches.map((match, index) => (
                <button key={`${match.path}-${match.line}-${index}`} onClick={() => void openFile(match.path)} className="mb-1 w-full rounded px-2 py-1.5 text-left hover:bg-raised">
                  <div className="truncate font-mono text-[8.5px] text-secondary">{match.path}:{match.line}</div>
                  <div className="mt-0.5 truncate text-[8.5px] text-faint">{match.text}</div>
                </button>
              ))}
              {!matches.length && <div className="px-2 py-6 text-center text-[9px] text-faint"><FileSearch size={14} className="mx-auto mb-2" />검색 결과를 Monaco 편집기로 엽니다.</div>}
            </div>
          </aside>
          <div className="min-w-0">
            {file ? (
              <div className="flex h-full flex-col">
                <div className="flex h-8 items-center gap-2 border-b border-border px-2 font-mono text-[9px] text-faint">
                  <FolderTree size={10} /> <span className="truncate">{file.path}</span>
                  {dirty && <span className="text-warn">● 수정됨</span>}
                  <button onClick={() => void saveFile()} disabled={!dirty} className="task-quiet-action ml-auto"><Save size={9} /> 저장 ⌘S</button>
                </div>
                <div className="min-h-0 flex-1">
                  <Editor height="100%" path={file.path} theme="vs-dark" value={draft} onChange={(value) => { setDraft(value ?? ''); setDirty((value ?? '') !== file.content) }} options={{ minimap: { enabled: false }, fontSize: 11, scrollBeyondLastLine: false, automaticLayout: true }} />
                </div>
              </div>
            ) : <div className="grid h-full place-items-center text-[9px] text-faint">작업 공간의 파일을 검색해 여세요.</div>}
          </div>
        </div>
      )}

      {tab === 'preview' && (
        <div className="min-h-[400px]">
          <div className="flex h-9 items-center gap-1.5 border-b border-border px-2">
            <input value={previewUrl} onChange={(event) => setPreviewUrl(event.target.value)} className="task-input mt-0 min-w-0 flex-1 font-mono text-[9px]" />
            <button onClick={() => void openPreview()} className="task-primary-action"><ExternalLink size={9} /> 미리보기 열기</button>
            <button onClick={() => void inspect()} disabled={!browser?.open} className="task-quiet-action"><MousePointer2 size={9} /> 요소 선택</button>
            <button onClick={() => void capture()} disabled={!browser?.open} className="task-quiet-action"><Camera size={9} /> 화면 캡처</button>
          </div>
          <div className="grid h-[360px] grid-cols-[1fr_1fr] gap-px bg-border">
            <div className="min-h-0 overflow-auto bg-panel p-2">
              <div className="mb-2 flex items-center justify-between task-label"><span>콘솔 · {browser?.console.length ?? 0}</span><button onClick={() => void navigator.clipboard.writeText((browser?.console ?? []).map((item) => `[${item.level}] ${item.message}`).join('\n'))}><Clipboard size={9} /></button></div>
              {(browser?.console ?? []).slice(-200).map((item, index) => <div key={`${item.at}-${index}`} className={`border-b border-border/50 py-1 font-mono text-[8.5px] ${item.level === 'error' ? 'text-danger' : 'text-muted'}`}>[{item.level}] {item.message}</div>)}
              <div className="mb-2 mt-4 task-label">네트워크 · {browser?.network.length ?? 0}</div>
              {(browser?.network ?? []).slice(-200).map((item, index) => <div key={`${item.at}-${index}`} className="flex gap-2 border-b border-border/50 py-1 font-mono text-[8px]"><span className={item.error || (item.status ?? 0) >= 400 ? 'text-danger' : 'text-ok'}>{item.error ?? item.status ?? '…'}</span><span className="text-faint">{item.method}</span><span className="truncate text-muted">{item.url}</span></div>)}
            </div>
            <div className="min-h-0 overflow-auto bg-panel p-2">
              {inspection ? (
                <div>
                  <div className="task-label">선택한 요소 · {inspection.element.tag}{inspection.element.id ? `#${inspection.element.id}` : ''}</div>
                  <div className="mt-2 border border-border bg-base p-2 font-mono text-[8px] text-muted">
                    <div>소스 · {inspection.element.sourceContext ?? '노출된 소스 속성 없음'}</div>
                    <div>영역 · {Math.round(inspection.element.rect.width)}×{Math.round(inspection.element.rect.height)} @ {Math.round(inspection.element.rect.x)},{Math.round(inspection.element.rect.y)}</div>
                    <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap text-faint">{JSON.stringify(inspection.element.css, null, 2)}</pre>
                  </div>
                  <img src={inspection.screenshotDataUrl} alt="요소 검사 화면" className="mt-2 w-full rounded border border-border" />
                </div>
              ) : screenshot ? <img src={screenshot} alt="작업 미리보기 화면" className="w-full rounded border border-border" /> : <div className="grid h-full place-items-center text-center text-[9px] leading-relaxed text-faint">작업별로 격리된 브라우저 프로필에서 localhost 앱을 여세요.<br />콘솔, 네트워크, 화면 캡처, DOM/CSS 컨텍스트가 이 작업에 유지됩니다.</div>}
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
