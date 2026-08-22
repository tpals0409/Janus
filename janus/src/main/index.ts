import { app, BrowserWindow, dialog, ipcMain, session as electronSession, shell } from 'electron'
import { spawn, type ChildProcess } from 'child_process'
import { randomBytes } from 'crypto'
import { createWriteStream, mkdirSync } from 'fs'
import net from 'net'
import { join } from 'path'
import {
  appendBoundedText,
  classifyServiceFailure,
  classifyEndpoint,
  createServiceRuntime,
  markBlocked,
  markExternal,
  markOwned,
  processAlive,
  scheduleRestart,
  stopOwnedService,
  type ServiceRuntime
} from './service-lifecycle'
import {
  BoundedCapture, normalizePreviewUrl, taskBrowserPartition,
  type CapturedConsole, type CapturedNetwork
} from './task-browser'
import { resolveRuntimePaths } from './runtime-paths'

// ─────────────────────────── 백엔드 소유 ───────────────────────────
// "하나의 앱": Janus를 켜면 janus-server와 MLX 모델 서버가 함께 뜨고, 끄면 함께
// 내려간다. 이미 떠 있는 서버(포트 사용 중)는 우리가 띄운 게 아니므로 건드리지
// 않는다 — 터미널에서 수동으로 돌리는 개발 워크플로가 그대로 살아 있다.

const runtimePaths = resolveRuntimePaths({
  isPackaged: app.isPackaged,
  appPath: app.getAppPath(),
  resourcesPath: process.resourcesPath,
  userDataPath: app.getPath('userData')
})
mkdirSync(runtimePaths.logRoot, { recursive: true })
const devUrl = process.env['ELECTRON_RENDERER_URL']
const authToken = process.env.JANUS_AUTH_TOKEN ?? randomBytes(32).toString('hex')
const allowedOrigins =
  process.env.JANUS_ALLOWED_ORIGINS ?? (devUrl ? new URL(devUrl).origin : 'file://,null')

// preload 렌더러와 Python 자식 프로세스가 같은 기동별 비밀값을 받는다.
process.env.JANUS_AUTH_TOKEN = authToken
process.env.JANUS_ALLOWED_ORIGINS = allowedOrigins

// GUI로 실행되면 PATH에 uv(~/.local/bin)와 homebrew가 없을 수 있다.
const env: NodeJS.ProcessEnv = {
  ...process.env,
  PATH: `${process.env.HOME}/.local/bin:/opt/homebrew/bin:${process.env.PATH ?? ''}`,
  JANUS_AUTH_TOKEN: authToken,
  JANUS_ALLOWED_ORIGINS: allowedOrigins,
  JANUS_LOG_DIR: runtimePaths.logRoot
}

type ServiceLabel = 'server' | 'mlx'

const serviceSpecs: Record<ServiceLabel, {
  port: number; command: string; cwd: string; environment: string; logPath: string
}> = {
  server: {
    port: 8765,
    command: 'uv run --frozen python -m janus_server.server',
    cwd: runtimePaths.backendRoot,
    environment: runtimePaths.backendEnvironment,
    logPath: join(runtimePaths.logRoot, 'janus-server.log')
  },
  mlx: {
    port: 8080,
    command:
      'uv run --frozen mlx_vlm.server --model "$(ls -d ~/.cache/huggingface/hub/' +
      'models--orcarouter--Qwen3.8-27B-Uncensored-MLX/snapshots/*/4-bit)" --port 8080',
    cwd: runtimePaths.modelRuntimeRoot,
    environment: runtimePaths.modelEnvironment,
    logPath: join(runtimePaths.logRoot, 'janus-mlx.log')
  }
}

const services: Record<ServiceLabel, ServiceRuntime> = {
  server: createServiceRuntime(),
  mlx: createServiceRuntime()
}

let quitting = false
let supervising = false
let supervisorTimer: ReturnType<typeof setInterval> | null = null
let mainWindow: BrowserWindow | null = null

interface TaskBrowserRuntime {
  taskId: string
  partition: string
  url: string
  window: BrowserWindow | null
  console: BoundedCapture<CapturedConsole>
  network: BoundedCapture<CapturedNetwork>
  networkAttached: boolean
}

const taskBrowsers = new Map<string, TaskBrowserRuntime>()

function taskBrowser(taskId: string): TaskBrowserRuntime {
  const partition = taskBrowserPartition(taskId)
  const current = taskBrowsers.get(taskId)
  if (current) return current
  const runtime: TaskBrowserRuntime = {
    taskId, partition, url: '', window: null,
    console: new BoundedCapture(500), network: new BoundedCapture(500),
    networkAttached: false
  }
  taskBrowsers.set(taskId, runtime)
  return runtime
}

function attachNetworkCapture(runtime: TaskBrowserRuntime): void {
  if (runtime.networkAttached) return
  runtime.networkAttached = true
  const isolated = electronSession.fromPartition(runtime.partition)
  isolated.webRequest.onCompleted({ urls: ['http://*/*', 'https://*/*'] }, (details) => {
    runtime.network.add({
      at: new Date().toISOString(), method: details.method, url: details.url,
      status: details.statusCode
    })
  })
  isolated.webRequest.onErrorOccurred({ urls: ['http://*/*', 'https://*/*'] }, (details) => {
    runtime.network.add({
      at: new Date().toISOString(), method: details.method, url: details.url,
      error: details.error
    })
  })
}

function openTaskBrowser(taskId: string, requestedUrl: string): TaskBrowserRuntime {
  const runtime = taskBrowser(taskId)
  const url = normalizePreviewUrl(requestedUrl)
  runtime.url = url
  attachNetworkCapture(runtime)
  if (!runtime.window || runtime.window.isDestroyed()) {
    const preview = new BrowserWindow({
      width: 1180, height: 820, minWidth: 720, minHeight: 480,
      title: `Janus Preview · ${taskId}`,
      parent: mainWindow ?? undefined,
      webPreferences: {
        partition: runtime.partition, sandbox: true, contextIsolation: true,
        nodeIntegration: false
      }
    })
    runtime.window = preview
    preview.webContents.on('console-message', (...args: unknown[]) => {
      const details = args[1]
      if (typeof details === 'object' && details !== null) {
        const value = details as Record<string, unknown>
        runtime.console.add({
          at: new Date().toISOString(), level: String(value.level ?? 'info'),
          message: String(value.message ?? ''), line: Number(value.lineNumber ?? 0),
          source: String(value.sourceId ?? '')
        })
      } else {
        runtime.console.add({
          at: new Date().toISOString(), level: String(args[1] ?? 'info'),
          message: String(args[2] ?? ''), line: Number(args[3] ?? 0),
          source: String(args[4] ?? '')
        })
      }
    })
    preview.webContents.on('did-navigate', (_event, navigated) => {
      runtime.url = navigated
    })
    preview.webContents.setWindowOpenHandler(({ url: target }) => {
      try {
        void preview.loadURL(normalizePreviewUrl(target))
      } catch {
        void shell.openExternal(target)
      }
      return { action: 'deny' }
    })
    preview.webContents.on('will-navigate', (event, target) => {
      try {
        normalizePreviewUrl(target)
      } catch {
        event.preventDefault()
      }
    })
    preview.on('closed', () => { runtime.window = null })
  }
  void runtime.window.loadURL(url)
  runtime.window.show()
  runtime.window.focus()
  return runtime
}

function taskBrowserStatus(taskId: string) {
  const runtime = taskBrowser(taskId)
  return {
    taskId, partition: runtime.partition, url: runtime.url,
    open: Boolean(runtime.window && !runtime.window.isDestroyed()),
    console: runtime.console.snapshot(), network: runtime.network.snapshot()
  }
}

function portInUse(port: number): Promise<boolean> {
  return new Promise((res) => {
    const s = net.connect({ port, host: '127.0.0.1' })
    let settled = false
    const finish = (used: boolean): void => {
      if (settled) return
      settled = true
      s.destroy()
      res(used)
    }
    s.once('connect', () => {
      finish(true)
    })
    s.once('error', () => finish(false))
    s.setTimeout(500, () => finish(false))
  })
}

function spawnLogged(label: ServiceLabel): void {
  const spec = serviceSpecs[label]
  const service = services[label]
  if (quitting || processAlive(service.process)) return

  const log = createWriteStream(spec.logPath, { flags: 'a', mode: 0o600 })
  let p: ChildProcess
  try {
    // detached: 프로세스 그룹을 따로 만들어 uv가 낳는 python 자식까지 한 번에 죽인다.
    p = spawn('/bin/zsh', ['-c', spec.command], {
      cwd: spec.cwd,
      env: { ...env, UV_PROJECT_ENVIRONMENT: spec.environment },
      detached: true
    })
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    log.end(`\n[janus] ${label} spawn failed: ${message}\n`)
    scheduleRestart(service, `spawn failed: ${message}`)
    return
  }

  markOwned(service, p)
  p.stdout?.pipe(log)
  p.stderr?.pipe(log)
  let recentError = ''
  p.stderr?.on('data', (chunk) => { recentError = appendBoundedText(recentError, chunk) })
  let handled = false
  const stopped = (reason: string): void => {
    if (handled) return
    handled = true
    const failure = classifyServiceFailure(reason, recentError)
    log.end(`\n[janus] ${label} stopped: ${failure.message}\n[janus] recovery: ${failure.action}\n`)
    if (!quitting) scheduleRestart(service, failure.message)
  }
  p.once('error', (error) => stopped(`spawn error: ${error.message}`))
  p.once('exit', (code, signal) => stopped(`exit=${code ?? '—'} signal=${signal ?? '—'}`))
}

async function endpointHealthy(label: ServiceLabel): Promise<boolean> {
  const url = label === 'server' ? 'http://127.0.0.1:8765/health' : 'http://127.0.0.1:8080/v1/models'
  const headers = label === 'server' ? { 'x-janus-token': authToken } : undefined
  try {
    const response = await fetch(url, { headers, signal: AbortSignal.timeout(800) })
    if (!response.ok) return false
    const body = (await response.json()) as Record<string, unknown>
    return label === 'server' ? body.ok === true : Array.isArray(body.data)
  } catch {
    return false
  }
}

async function ensureService(label: ServiceLabel): Promise<void> {
  const service = services[label]
  const portUp = await portInUse(serviceSpecs[label].port)
  const endpoint = classifyEndpoint(portUp, portUp && (await endpointHealthy(label)))

  if (endpoint === 'healthy') {
    if (processAlive(service.process)) {
      service.phase = 'up'
      // 30초 이상 안정적으로 살아야 이전 크래시 카운트를 지운다.
      if (service.startedAt && Date.now() - service.startedAt >= 30_000) {
        service.attempts = 0
        service.lastError = null
      }
    } else {
      // endpoint까지 확인된 수동 서버만 external이다.
      markExternal(service)
    }
    return
  }

  if (endpoint === 'foreign') {
    if (processAlive(service.process)) {
      // 우리가 막 시작한 서비스는 port bind 뒤 endpoint 준비까지 시간이 필요하다.
      service.phase = 'starting'
      return
    }
    markBlocked(
      service,
      `port ${serviceSpecs[label].port} is occupied by an unexpected or stale service`
    )
    return
  }

  if (processAlive(service.process)) return // 시작/모델 로딩 중
  if (Date.now() < service.nextRetryAt) return
  spawnLogged(label)
}

async function superviseBackend(): Promise<void> {
  if (quitting || supervising) return
  supervising = true
  try {
    await Promise.all([ensureService('server'), ensureService('mlx')])
  } finally {
    supervising = false
  }
}

function startBackendSupervisor(): void {
  void superviseBackend()
  supervisorTimer = setInterval(() => void superviseBackend(), 2000)
}

function backendStatus() {
  const now = Date.now()
  const publicState = (label: ServiceLabel, service: ServiceRuntime) => ({
    phase: service.phase,
    ownership: service.ownership,
    pid: service.pid,
    lastPid: service.lastPid,
    attempts: service.attempts,
    retryInMs: Math.max(0, service.nextRetryAt - now),
    lastError: service.lastError,
    logPath: serviceSpecs[label].logPath
  })
  return { server: publicState('server', services.server), mlx: publicState('mlx', services.mlx) }
}

async function killBackend(): Promise<void> {
  quitting = true
  for (const runtime of taskBrowsers.values()) runtime.window?.destroy()
  if (supervisorTimer) clearInterval(supervisorTimer)
  supervisorTimer = null
  const results = await Promise.all(Object.values(services).map((service) => stopOwnedService(service)))
  for (const result of results) {
    if (result.orphan) {
      console.error(`[janus] orphan process survived shutdown: pid=${result.pid ?? 'unknown'}`)
    }
  }
}

// ─────────────────────────── 창 ───────────────────────────

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1536,
    height: 1024,
    minWidth: 1100,
    minHeight: 700,
    show: false,
    backgroundColor: '#0b0b10',
    titleBarStyle: 'hiddenInset',
    webPreferences: { preload: join(__dirname, '../preload/index.mjs'), sandbox: false }
  })
  mainWindow = win

  win.on('ready-to-show', () => win.show())
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  if (devUrl) win.loadURL(devUrl)
  else win.loadFile(join(__dirname, '../renderer/index.html'))
}

// 렌더러가 못 하는 유일한 일 — 네이티브 폴더 선택. 나머지는 전부 Python 서버가 한다.
ipcMain.handle('pick-folder', async () => {
  const r = await dialog.showOpenDialog({ properties: ['openDirectory'] })
  return r.canceled ? null : r.filePaths[0]
})
ipcMain.handle('backend-status', () => backendStatus())
ipcMain.handle('task-browser-open', (_event, input: { taskId: string; url: string }) => {
  openTaskBrowser(input.taskId, input.url)
  return taskBrowserStatus(input.taskId)
})
ipcMain.handle('task-browser-status', (_event, taskId: string) => taskBrowserStatus(taskId))
ipcMain.handle('task-browser-screenshot', async (_event, taskId: string) => {
  const runtime = taskBrowser(taskId)
  if (!runtime.window || runtime.window.isDestroyed()) throw new Error('Task preview가 열려 있지 않습니다')
  const image = await runtime.window.webContents.capturePage()
  return { dataUrl: `data:image/png;base64,${image.toPNG().toString('base64')}`, url: runtime.url }
})
ipcMain.handle('task-browser-inspect', async (_event, taskId: string) => {
  const runtime = taskBrowser(taskId)
  if (!runtime.window || runtime.window.isDestroyed()) throw new Error('Task preview가 열려 있지 않습니다')
  const element = await runtime.window.webContents.executeJavaScript(`
    new Promise((resolve) => {
      const prior = document.getElementById('__janus_inspector_hint__');
      if (prior) prior.remove();
      const hint = document.createElement('div');
      hint.id = '__janus_inspector_hint__'; hint.textContent = 'Janus: select an element';
      Object.assign(hint.style, { position:'fixed', top:'8px', left:'50%', transform:'translateX(-50%)', zIndex:'2147483647', background:'#171723', color:'#fff', padding:'6px 10px', border:'1px solid #738cff', borderRadius:'4px', font:'12px monospace' });
      document.documentElement.appendChild(hint);
      const select = (event) => {
        event.preventDefault(); event.stopPropagation();
        document.removeEventListener('click', select, true); hint.remove();
        const node = event.target; const style = getComputedStyle(node); const rect = node.getBoundingClientRect();
        resolve({
          tag: node.tagName.toLowerCase(), id: node.id || null, classes: Array.from(node.classList || []),
          html: node.outerHTML.slice(0, 12000), text: (node.textContent || '').trim().slice(0, 2000),
          css: { display:style.display, position:style.position, color:style.color, background:style.background, font:style.font, margin:style.margin, padding:style.padding, width:style.width, height:style.height },
          rect: { x:rect.x, y:rect.y, width:rect.width, height:rect.height },
          sourceContext: node.getAttribute('data-source') || node.getAttribute('data-testid') || node.getAttribute('src') || node.getAttribute('href') || null,
          url: location.href
        });
      };
      document.addEventListener('click', select, true);
    })
  `, true)
  const image = await runtime.window.webContents.capturePage()
  return { element, screenshotDataUrl: `data:image/png;base64,${image.toPNG().toString('base64')}` }
})

app.whenReady().then(() => {
  env.JANUS_STATE_FILE =
    process.env.JANUS_STATE_FILE ?? join(app.getPath('userData'), 'state.json')
  env.JANUS_DB_FILE =
    process.env.JANUS_DB_FILE ?? join(app.getPath('userData'), 'janus.sqlite3')
  env.JANUS_WORKTREES_DIR =
    process.env.JANUS_WORKTREES_DIR ?? join(app.getPath('userData'), 'workspaces')
  startBackendSupervisor() // 창보다 먼저 시작 — 모델 로드가 제일 오래 걸린다
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

let shutdownComplete = false
let shutdownPromise: Promise<void> | null = null

app.on('before-quit', (event) => {
  if (shutdownComplete) return
  event.preventDefault()
  if (shutdownPromise === null) {
    shutdownPromise = killBackend().finally(() => {
      shutdownComplete = true
      app.quit()
    })
  }
})
process.on('SIGTERM', () => {
  app.quit()
})
process.on('SIGINT', () => {
  app.quit()
})

app.on('window-all-closed', () => {
  // 백엔드가 앱 수명에 묶여 있으므로 창을 닫으면 (macOS 포함) 전부 종료한다.
  app.quit()
})
