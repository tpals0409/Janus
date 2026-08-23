import { useEffect, useState } from 'react'
import { ReactFlowProvider } from '@xyflow/react'
import { Loader2, ShieldAlert } from 'lucide-react'
import { useStore } from './store'
import Canvas from './components/Canvas'
import { AgentProfileList, NavRail, StatusBar } from './components/Shell'
import TaskWorkspace from './components/tasks/TaskWorkspace'
import EvaluationLab from './components/evaluations/EvaluationLab'
import OperationsDashboard from './components/operations/OperationsDashboard'
import PromptEditor from './components/PromptEditor'
import SkillLibrary from './components/SkillLibrary'
import ContextPolicyEditor from './components/ContextPolicyEditor'
import { BrandMark, Status, Tabs } from './components/ui'
import CommandPalette from './components/CommandPalette'

const DESIGN_TABS = ['프롬프트', '스킬', '컨텍스트 정책', '그래프'] as const


export default function App() {
  const visualFixture =
    import.meta.env.DEV && new URLSearchParams(window.location.search).get('fixture') === 'task-runtime'
  const boot = useStore((s) => s.boot)
  const serverUp = useStore((s) => s.serverUp)
  const backendStatus = useStore((s) => s.backendStatus)
  const mlxUp = useStore((s) => s.mlxUp)
  const authFailed = useStore((s) => s.authFailed)
  const task = useStore((s) => s.task)
  const projects = useStore((s) => s.projects)
  const projectId = useStore((s) => s.projectId)
  const agentProfiles = useStore((s) => s.agentProfiles)
  const selectedAgentProfileId = useStore((s) => s.selectedAgentProfileId)

  const [nav, setNav] = useState('tasks')
  const [tab, setTab] = useState<(typeof DESIGN_TABS)[number]>('프롬프트')
  const selectedProfile = agentProfiles.find((profile) => profile.id === selectedAgentProfileId)

  const pollHealth = useStore((s) => s.pollHealth)

  useEffect(() => {
    if (!visualFixture) boot()
  }, [boot, visualFixture])

  // 앱이 백엔드를 직접 띄우므로(main process) 처음 몇 초는 연결이 안 되는 게 정상.
  // 붙을 때까지 자동 재시도하고, 붙은 뒤엔 모델 서버 상태를 따라간다.
  useEffect(() => {
    if (visualFixture) return
    if (serverUp === false) {
      const t = setTimeout(boot, authFailed ? 15000 : 2000)
      return () => clearTimeout(t)
    }
    const t = setInterval(pollHealth, 5000)
    return () => clearInterval(t)
  }, [serverUp, authFailed, boot, pollHealth, visualFixture])

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
              {`${backendStatus?.server.logPath ?? 'Janus user data/logs/janus-server.log'}\n${backendStatus?.mlx.logPath ?? 'Janus user data/logs/janus-mlx.log'}`}
            </pre>
          </details>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <header className="app-titlebar">
        <BrandMark />
        <div className="app-titlebar__context">
          {nav === 'tasks'
            ? task?.title ?? projects.find((project) => project.id === projectId)?.name ?? '작업'
            : nav === 'evals' ? '평가 실험실'
            : nav === 'monitor' ? '운영 모니터'
            : selectedProfile?.name ?? '실행 프로필'}
        </div>
        <div className="app-titlebar__status">
          <span className="font-mono text-[10px] text-faint">local</span>
          <Status tone={mlxUp ? 'success' : 'warning'} pulse={!mlxUp}>
            {mlxUp ? '모델 준비' : '모델 로딩'}
          </Status>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {nav === 'tasks' ? (
          <TaskWorkspace onNavigate={setNav} />
        ) : nav === 'evals' ? (
          <><NavRail active={nav} onSelect={setNav} /><EvaluationLab /></>
        ) : nav === 'monitor' ? (
          <><NavRail active={nav} onSelect={setNav} /><OperationsDashboard onOpenTask={() => setNav('tasks')} /></>
        ) : nav !== 'agents' ? (
          <><NavRail active={nav} onSelect={setNav} /><div className="grid flex-1 place-items-center text-[12px] text-faint">
            이 화면은 아직 구현되지 않았습니다
          </div></>
        ) : (
          <>
            <NavRail active={nav} onSelect={setNav} />
            <AgentProfileList />
            <main className="flex min-w-0 flex-1 flex-col">
              <Tabs items={DESIGN_TABS} value={tab} onChange={setTab} label="에이전트 프로필" />

              <div className="min-h-0 flex-1">
                {tab === '프롬프트' ? (
                  <PromptEditor />
                ) : tab === '스킬' ? (
                  <SkillLibrary />
                ) : tab === '컨텍스트 정책' ? (
                  <ContextPolicyEditor />
                ) : (
                  <ReactFlowProvider>
                    <Canvas />
                  </ReactFlowProvider>
                )}
              </div>
            </main>
          </>
        )}
      </div>

      <StatusBar mode={nav} />
      <CommandPalette onNavigate={setNav} />
    </div>
  )
}
