import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { ReactFlowProvider } from '@xyflow/react'
import { ChevronDown, FolderOpen, Loader2, Play, Rocket, Save, FlaskConical, ShieldAlert, Square } from 'lucide-react'
import { useStore } from './store'
import Canvas from './components/Canvas'
import Inspector from './components/Inspector'
import TracePanel from './components/traces/TracePanel'
import ApprovalCard from './components/ApprovalCard'
import FileTree from './components/FileTree'
import { AgentList, NavRail, StatusBar } from './components/Shell'

// Monaco는 무겁다 — YAML 뷰를 열기 전엔 로드하지 않는다
const YamlView = lazy(() => import('./components/YamlView'))
const FileView = lazy(() => import('./components/FileView'))

const DESIGN_TABS = ['Design', 'Prompt', 'Tools', 'Context', 'Memory', 'Settings'] as const
const BOTTOM_TABS = ['Run', 'Traces', 'Logs', 'Metrics'] as const
const WIRED_BOTTOM = new Set(['Run', 'Traces'])

/** 상하 분할용 드래그 핸들 */
function useDragHeight(initial: number, min = 140, max = 520) {
  const [h, setH] = useState(initial)
  const start = useRef<{ y: number; h: number } | null>(null)
  const onMouseDown = (e: React.MouseEvent) => {
    start.current = { y: e.clientY, h }
    const move = (ev: MouseEvent) => {
      if (!start.current) return
      setH(Math.min(max, Math.max(min, start.current.h - (ev.clientY - start.current.y))))
    }
    const up = () => {
      start.current = null
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
  }
  return { h, onMouseDown }
}


/** 실행 입력 필드는 start 노드의 outputs에서 나온다 — 그래프마다 다르다. */
function useStartFields(): string[] {
  const spec = useStore((s) => s.spec)
  const sync = useStore((s) => s.syncRunFields)
  const fields = spec?.nodes.find((n) => n.type === 'start')?.outputs ?? []
  useEffect(() => sync(fields), [fields.join(' ')])
  return fields
}

/** IDE의 '폴더 열기' — 프로젝트가 1급 시민이 되는 지점. 최근 폴더 포함. */
function ProjectButton() {
  const workspace = useStore((s) => s.workspace)
  const recents = useStore((s) => s.recentFolders)
  const pickWorkspace = useStore((s) => s.pickWorkspace)
  const setWorkspaceTo = useStore((s) => s.setWorkspaceTo)
  const [open, setOpen] = useState(false)
  const name = workspace?.split('/').filter(Boolean).pop() ?? '폴더 열기'

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        title={workspace ?? '작업할 프로젝트 폴더를 선택하세요'}
        className="flex items-center gap-1.5 rounded-md border border-border-strong px-2.5 py-1.5 text-[12px] hover:border-accent"
      >
        <FolderOpen size={13} className="text-accent-fg" />
        <span className="max-w-[180px] truncate">{name}</span>
        <ChevronDown size={11} className="text-faint" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full z-20 mt-1 w-[340px] rounded-md border border-border-strong bg-raised py-1 shadow-lg">
            <button
              onClick={() => {
                setOpen(false)
                pickWorkspace()
              }}
              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] hover:bg-panel-2"
            >
              <FolderOpen size={12} /> 다른 폴더 열기…
            </button>
            {recents.length > 0 && (
              <div className="mt-1 border-t border-border pt-1">
                <div className="px-3 py-0.5 text-[10px] tracking-wider text-faint">최근</div>
                {recents.map((f) => (
                  <button
                    key={f}
                    onClick={() => {
                      setOpen(false)
                      setWorkspaceTo(f)
                    }}
                    className="block w-full truncate px-3 py-1 text-left font-mono text-[11px] text-muted hover:bg-panel-2 hover:text-fg"
                    style={{ color: f === workspace ? 'var(--color-accent-fg)' : undefined }}
                  >
                    {f}
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}

function RunBar() {
  const run = useStore((s) => s.run)
  const running = useStore((s) => s.running)
  const spec = useStore((s) => s.spec)
  const errors = useStore((s) => s.errors)
  const values = useStore((s) => s.runInputs)
  const setRunInput = useStore((s) => s.setRunInput)
  const fields = useStartFields()

  if (!fields.length) {
    return (
      <div className="grid h-full place-items-center text-[12px] text-faint">
        start 노드에 출력 필드가 없습니다. 인스펙터에서 추가하세요.
      </div>
    )
  }

  return (
    <div className="flex h-full items-start gap-3 overflow-x-auto px-4 py-3">
      {fields.map((f) => (
        <label key={f} className="min-w-[180px] flex-1">
          <div className="mb-1 font-mono text-[10px] tracking-wider text-faint">{f}</div>
          <input
            value={values[f] ?? ''}
            onChange={(e) => setRunInput(f, e.target.value)}
            className="w-full rounded-md border border-border-strong bg-raised px-2.5 py-1.5 text-[12px] outline-none focus:border-accent"
          />
        </label>
      ))}
      <button
        disabled={running || !spec || errors.length > 0}
        onClick={() => run(values)}
        className="mt-[18px] flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-50"
        style={{ background: 'var(--color-accent)' }}
      >
        {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />}
        {running ? '실행 중' : 'Run'}
      </button>
    </div>
  )
}

export default function App() {
  const boot = useStore((s) => s.boot)
  const serverUp = useStore((s) => s.serverUp)
  const backendStatus = useStore((s) => s.backendStatus)
  const authFailed = useStore((s) => s.authFailed)
  const spec = useStore((s) => s.spec)
  const view = useStore((s) => s.view)
  const setView = useStore((s) => s.setView)
  const save = useStore((s) => s.save)
  const dirty = useStore((s) => s.dirty)
  const errors = useStore((s) => s.errors)
  const running = useStore((s) => s.running)
  const runFn = useStore((s) => s.run)
  const cancel = useStore((s) => s.cancel)
  const sidebarTab = useStore((s) => s.sidebarTab)
  const setSidebarTab = useStore((s) => s.setSidebarTab)

  const [nav, setNav] = useState('agents')
  const [tab, setTab] = useState<(typeof DESIGN_TABS)[number]>('Design')
  const [bottom, setBottom] = useState<(typeof BOTTOM_TABS)[number]>('Run')
  const { h, onMouseDown } = useDragHeight(280)

  const pollHealth = useStore((s) => s.pollHealth)

  useEffect(() => {
    boot()
  }, [boot])

  // 앱이 백엔드를 직접 띄우므로(main process) 처음 몇 초는 연결이 안 되는 게 정상.
  // 붙을 때까지 자동 재시도하고, 붙은 뒤엔 모델 서버 상태를 따라간다.
  useEffect(() => {
    if (serverUp === false) {
      const t = setTimeout(boot, authFailed ? 15000 : 2000)
      return () => clearTimeout(t)
    }
    const t = setInterval(pollHealth, 5000)
    return () => clearInterval(t)
  }, [serverUp, authFailed, boot, pollHealth])

  if (authFailed) {
    // 서버는 살아 있는데 토큰/Origin이 거부됐다. 스피너를 돌리면 "곧 될 것"이라는
    // 거짓말이 된다 — 기다려서 풀리는 상황이 아니다.
    return (
      <div className="grid h-full place-items-center px-8 text-center">
        <div className="max-w-[560px]">
          <ShieldAlert size={28} className="mx-auto mb-3 text-warn" />
          <p className="mb-2 text-[14px]">인증이 거부됐습니다 (백엔드는 정상)</p>
          <p className="text-[11px] leading-relaxed text-faint">
            janus-server는 응답하고 있지만 이 창의 토큰이나 Origin을 받아들이지 않습니다.
            <br />
            Janus는 기동마다 토큰을 새로 만들어 Electron 창에만 전달합니다 — 브라우저에서
            <br />
            <code className="text-muted">localhost:5173</code>을 직접 열었거나, 앱과 따로 띄운
            서버에 붙은 경우입니다.
          </p>
          <p className="mt-3 text-[11px] text-faint">
            Janus 앱 창에서 사용하세요. 터미널에서 서버를 직접 띄우려면 앱과 같은
            <br />
            <code className="text-muted">JANUS_AUTH_TOKEN</code> 환경변수로 실행해야 합니다.
          </p>
        </div>
      </div>
    )
  }

  if (serverUp === false) {
    const service = backendStatus?.server
    const retrySeconds = Math.max(1, Math.ceil((service?.retryInMs ?? 0) / 1000))
    const title =
      service?.phase === 'external'
        ? ':8765 포트를 다른 프로세스가 사용 중입니다'
        : service?.phase === 'failed'
          ? `백엔드가 반복 종료됐습니다 · ${retrySeconds}초 후 재시도`
          : service?.phase === 'restarting'
            ? '백엔드를 재시작하는 중…'
            : '백엔드를 시작하는 중…'
    const detail =
      service?.phase === 'external'
        ? '현재 앱의 인증 토큰과 맞지 않는 이전 서버일 수 있습니다. 해당 프로세스를 종료하거나 같은 토큰으로 앱을 실행하세요.'
        : service?.lastError
          ? `최근 종료 원인: ${service.lastError}`
          : 'janus-server와 MLX 모델 서버를 앱이 직접 관리합니다.'

    return (
      <div className="grid h-full place-items-center px-8 text-center">
        <div>
          <Loader2 size={28} className="mx-auto mb-3 animate-spin text-accent-fg" />
          <p className="mb-2 text-[14px]">{title}</p>
          <p className="max-w-[560px] text-[11px] text-faint">{detail}</p>
          <details className="mt-4 text-left">
            <summary className="cursor-pointer text-[11px] text-faint">
              진단 로그
            </summary>
            <pre className="mt-2 rounded-md border border-border-strong bg-panel-2 px-3 py-2 font-mono text-[10.5px] text-muted">
              {'/tmp/janus-server.log\n/tmp/janus-mlx.log'}
            </pre>
          </details>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      {/* 타이틀바 */}
      <header className="flex h-[52px] shrink-0 items-center gap-3 border-b border-border bg-panel px-4 pl-20">
        <span className="text-[15px] font-semibold tracking-tight">Janus</span>
        <span className="text-[11px] text-faint">Agent Development Environment</span>
        <ProjectButton />
        <div className="mx-auto flex items-center gap-2">
          <span className="text-[13px] font-medium">{spec?.name ?? '—'}</span>
          <span className="text-[11px]" style={{ color: dirty ? 'var(--color-warn)' : 'var(--color-ok)' }}>
            {dirty ? '● Unsaved' : '● Saved'}
          </span>
        </div>
        <button
          onClick={save}
          disabled={!dirty}
          className="flex items-center gap-1.5 rounded-md border border-border-strong px-2.5 py-1.5 text-[12px] text-muted disabled:opacity-40"
        >
          <Save size={13} /> Save
        </button>
        <button
          disabled={running || !spec || errors.length > 0}
          onClick={() => {
            setBottom('Traces')
            runFn(useStore.getState().runInputs)
          }}
          className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-50"
          style={{ background: 'var(--color-accent)' }}
        >
          {running ? <Loader2 size={13} className="animate-spin" /> : <Play size={13} />} Run
        </button>
        {running && (
          <button
            onClick={cancel}
            title="실행 중단"
            className="flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[12px]"
            style={{ borderColor: 'var(--color-danger)', color: 'var(--color-danger)' }}
          >
            <Square size={11} /> Stop
          </button>
        )}
        {[
          ['Eval', FlaskConical],
          ['Deploy', Rocket]
        ].map(([label, Icon]: any) => (
          <button
            key={label}
            disabled
            title={`${label} — 아직 구현되지 않음`}
            className="flex items-center gap-1.5 rounded-md border border-border-strong px-2.5 py-1.5 text-[12px] text-muted opacity-40"
          >
            <Icon size={13} /> {label}
          </button>
        ))}
      </header>

      <div className="flex min-h-0 flex-1">
        <NavRail active={nav} onSelect={setNav} />
        <AgentList />

        {nav !== 'agents' ? (
          <div className="grid flex-1 place-items-center text-[12px] text-faint">
            이 화면은 아직 구현되지 않았습니다
          </div>
        ) : (
          <>
            {/* 가운데 */}
            <main className="flex min-w-0 flex-1 flex-col">
              <div className="flex h-[38px] shrink-0 items-center gap-1 border-b border-border px-3">
                {DESIGN_TABS.map((t) => (
                  <button
                    key={t}
                    onClick={() => setTab(t)}
                    title={t === 'Design' ? undefined : `${t} — 아직 구현되지 않음`}
                    className="border-b-2 px-2.5 py-2 text-[12.5px]"
                    style={{
                      borderColor: tab === t ? 'var(--color-accent)' : 'transparent',
                      color: tab === t ? 'var(--color-fg)' : 'var(--color-muted)',
                      opacity: t === 'Design' ? 1 : 0.45
                    }}
                  >
                    {t}
                  </button>
                ))}
                <div className="ml-auto flex gap-0.5 rounded-md border border-border-strong p-0.5">
                  {(['graph', 'yaml'] as const).map((v) => (
                    <button
                      key={v}
                      onClick={() => setView(v)}
                      className="rounded px-2 py-0.5 text-[11px] uppercase"
                      style={{
                        background: view === v ? 'var(--color-accent-soft)' : 'transparent',
                        color: view === v ? 'var(--color-accent-fg)' : 'var(--color-muted)'
                      }}
                    >
                      {v}
                    </button>
                  ))}
                </div>
              </div>

              {errors.length > 0 && (
                <div className="shrink-0 border-b border-border bg-[#f8717118] px-3 py-1.5 text-[11px] text-danger">
                  {errors.slice(0, 3).join(' · ')}
                </div>
              )}

              <div className="min-h-0 flex-1">
                {tab !== 'Design' ? (
                  <div className="grid h-full place-items-center text-[12px] text-faint">
                    {tab} 탭은 아직 구현되지 않았습니다
                  </div>
                ) : view === 'file' ? (
                  <Suspense
                    fallback={
                      <div className="grid h-full place-items-center text-[12px] text-faint">
                        파일 로딩 중…
                      </div>
                    }
                  >
                    <FileView />
                  </Suspense>
                ) : view === 'graph' ? (
                  <ReactFlowProvider>
                    <Canvas />
                  </ReactFlowProvider>
                ) : (
                  <Suspense
                    fallback={
                      <div className="grid h-full place-items-center text-[12px] text-faint">
                        에디터 로딩 중…
                      </div>
                    }
                  >
                    <YamlView />
                  </Suspense>
                )}
              </div>

              {/* 하단 패널 */}
              <div className="divider-h" onMouseDown={onMouseDown} />
              <section
                className="flex shrink-0 flex-col border-t border-border bg-panel"
                style={{ height: h }}
              >
                <div className="flex h-[32px] shrink-0 items-center gap-1 border-b border-border px-3">
                  {BOTTOM_TABS.map((t) => (
                    <button
                      key={t}
                      onClick={() => setBottom(t)}
                      title={WIRED_BOTTOM.has(t) ? undefined : `${t} — 아직 구현되지 않음`}
                      className="rounded px-2.5 py-1 text-[12px]"
                      style={{
                        background: bottom === t ? 'var(--color-raised)' : 'transparent',
                        color: bottom === t ? 'var(--color-fg)' : 'var(--color-muted)',
                        opacity: WIRED_BOTTOM.has(t) ? 1 : 0.45
                      }}
                    >
                      {t}
                    </button>
                  ))}
                </div>
                <ApprovalCard />
                <div className="min-h-0 flex-1">
                  {bottom === 'Run' ? (
                    <RunBar />
                  ) : bottom === 'Traces' ? (
                    <TracePanel />
                  ) : (
                    <div className="grid h-full place-items-center text-[12px] text-faint">
                      {bottom} 탭은 아직 구현되지 않았습니다
                    </div>
                  )}
                </div>
              </section>
            </main>

            <aside className="flex w-[300px] shrink-0 flex-col border-l border-border bg-panel">
              <div className="flex shrink-0 gap-1 border-b border-border px-2 py-1.5">
                {(
                  [
                    ['node', 'Node'],
                    ['files', 'Files']
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    onClick={() => setSidebarTab(id)}
                    className="rounded px-2.5 py-1 text-[12px]"
                    style={{
                      background: sidebarTab === id ? 'var(--color-accent-soft)' : 'transparent',
                      color: sidebarTab === id ? 'var(--color-accent-fg)' : 'var(--color-muted)'
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="min-h-0 flex-1">
                {sidebarTab === 'files' ? <FileTree /> : <Inspector />}
              </div>
            </aside>
          </>
        )}
      </div>

      <StatusBar />
    </div>
  )
}
