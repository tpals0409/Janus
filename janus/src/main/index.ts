import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import { spawn, type ChildProcess } from 'child_process'
import { randomBytes } from 'crypto'
import { createWriteStream } from 'fs'
import net from 'net'
import { join, resolve } from 'path'
import {
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

// ─────────────────────────── 백엔드 소유 ───────────────────────────
// "하나의 앱": Janus를 켜면 janus-server와 MLX 모델 서버가 함께 뜨고, 끄면 함께
// 내려간다. 이미 떠 있는 서버(포트 사용 중)는 우리가 띄운 게 아니므로 건드리지
// 않는다 — 터미널에서 수동으로 돌리는 개발 워크플로가 그대로 살아 있다.

// dev에선 app.getAppPath() == janus/ 이므로 리포 루트는 한 단계 위다.
const repoRoot = resolve(app.getAppPath(), '..')
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
  JANUS_ALLOWED_ORIGINS: allowedOrigins
}

type ServiceLabel = 'server' | 'mlx'

const serviceSpecs: Record<ServiceLabel, { port: number; command: string; cwd: string }> = {
  server: {
    port: 8765,
    command: 'uv run python -m janus_server.server',
    cwd: join(repoRoot, 'janus_server')
  },
  mlx: {
    port: 8080,
    command:
      'uv run mlx_vlm.server --model "$(ls -d ~/.cache/huggingface/hub/' +
      'models--orcarouter--Qwen3.8-27B-Uncensored-MLX/snapshots/*/4-bit)" --port 8080',
    cwd: join(repoRoot, 'qwen3.8mlx')
  }
}

const services: Record<ServiceLabel, ServiceRuntime> = {
  server: createServiceRuntime(),
  mlx: createServiceRuntime()
}

let quitting = false
let supervising = false
let supervisorTimer: ReturnType<typeof setInterval> | null = null

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

  const log = createWriteStream(`/tmp/janus-${label}.log`, { flags: 'a' })
  let p: ChildProcess
  try {
    // detached: 프로세스 그룹을 따로 만들어 uv가 낳는 python 자식까지 한 번에 죽인다.
    p = spawn('/bin/zsh', ['-c', spec.command], { cwd: spec.cwd, env, detached: true })
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    log.end(`\n[janus] ${label} spawn failed: ${message}\n`)
    scheduleRestart(service, `spawn failed: ${message}`)
    return
  }

  markOwned(service, p)
  p.stdout?.pipe(log)
  p.stderr?.pipe(log)
  let handled = false
  const stopped = (reason: string): void => {
    if (handled) return
    handled = true
    log.end(`\n[janus] ${label} stopped: ${reason}\n`)
    if (!quitting) scheduleRestart(service, reason)
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
  const publicState = (service: ServiceRuntime) => ({
    phase: service.phase,
    ownership: service.ownership,
    pid: service.pid,
    lastPid: service.lastPid,
    attempts: service.attempts,
    retryInMs: Math.max(0, service.nextRetryAt - now),
    lastError: service.lastError
  })
  return { server: publicState(services.server), mlx: publicState(services.mlx) }
}

async function killBackend(): Promise<void> {
  quitting = true
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

app.whenReady().then(() => {
  env.JANUS_STATE_FILE =
    process.env.JANUS_STATE_FILE ?? join(app.getPath('userData'), 'state.json')
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
