import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import { spawn, type ChildProcess } from 'child_process'
import { randomBytes } from 'crypto'
import { createWriteStream } from 'fs'
import net from 'net'
import { join, resolve } from 'path'

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
const env = {
  ...process.env,
  PATH: `${process.env.HOME}/.local/bin:/opt/homebrew/bin:${process.env.PATH ?? ''}`,
  JANUS_AUTH_TOKEN: authToken,
  JANUS_ALLOWED_ORIGINS: allowedOrigins
}

type ServiceLabel = 'server' | 'mlx'
type ServicePhase = 'starting' | 'up' | 'restarting' | 'failed' | 'external' | 'stopped'

interface ServiceRuntime {
  process: ChildProcess | null
  phase: ServicePhase
  attempts: number
  nextRetryAt: number
  startedAt: number | null
  lastError: string | null
}

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
  server: { process: null, phase: 'starting', attempts: 0, nextRetryAt: 0, startedAt: null, lastError: null },
  mlx: { process: null, phase: 'starting', attempts: 0, nextRetryAt: 0, startedAt: null, lastError: null }
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

function processAlive(p: ChildProcess | null): p is ChildProcess {
  return Boolean(p && p.exitCode === null && p.signalCode === null)
}

function retryDelay(attempt: number): number {
  return Math.min(30_000, 1000 * 2 ** Math.min(Math.max(attempt - 1, 0), 5))
}

function scheduleRestart(label: ServiceLabel, reason: string): void {
  const service = services[label]
  service.process = null
  service.startedAt = null
  service.attempts += 1
  service.nextRetryAt = Date.now() + retryDelay(service.attempts)
  service.lastError = reason
  service.phase = service.attempts >= 3 ? 'failed' : 'restarting'
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
    scheduleRestart(label, `spawn failed: ${message}`)
    return
  }

  service.process = p
  service.startedAt = Date.now()
  service.phase = service.attempts ? 'restarting' : 'starting'
  service.nextRetryAt = 0
  p.stdout?.pipe(log)
  p.stderr?.pipe(log)
  let handled = false
  const stopped = (reason: string): void => {
    if (handled) return
    handled = true
    log.end(`\n[janus] ${label} stopped: ${reason}\n`)
    if (!quitting) scheduleRestart(label, reason)
  }
  p.once('error', (error) => stopped(`spawn error: ${error.message}`))
  p.once('exit', (code, signal) => stopped(`exit=${code ?? '—'} signal=${signal ?? '—'}`))
}

async function ensureService(label: ServiceLabel): Promise<void> {
  const service = services[label]
  const portUp = await portInUse(serviceSpecs[label].port)

  if (portUp) {
    if (processAlive(service.process)) {
      service.phase = 'up'
      // 30초 이상 안정적으로 살아야 이전 크래시 카운트를 지운다.
      if (service.startedAt && Date.now() - service.startedAt >= 30_000) {
        service.attempts = 0
        service.lastError = null
      }
    } else {
      // 수동으로 띄운 서버는 종료/재시작 대상이 아니다.
      service.phase = 'external'
      service.nextRetryAt = 0
    }
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
    attempts: service.attempts,
    retryInMs: Math.max(0, service.nextRetryAt - now),
    lastError: service.lastError
  })
  return { server: publicState(services.server), mlx: publicState(services.mlx) }
}

function killBackend(): void {
  quitting = true
  if (supervisorTimer) clearInterval(supervisorTimer)
  supervisorTimer = null
  for (const service of Object.values(services)) {
    const p = service.process
    service.phase = 'stopped'
    if (!processAlive(p) || p.pid == null) continue
    try {
      process.kill(-p.pid, 'SIGTERM') // 프로세스 그룹 전체
    } catch {
      try {
        p.kill('SIGTERM')
      } catch {
        /* 이미 죽었으면 무시 */
      }
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
  startBackendSupervisor() // 창보다 먼저 시작 — 모델 로드가 제일 오래 걸린다
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('before-quit', killBackend)
process.on('SIGTERM', () => {
  killBackend()
  app.quit()
})
process.on('SIGINT', () => {
  killBackend()
  app.quit()
})

app.on('window-all-closed', () => {
  // 백엔드가 앱 수명에 묶여 있으므로 창을 닫으면 (macOS 포함) 전부 종료한다.
  app.quit()
})
