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

const children: ChildProcess[] = []

function portInUse(port: number): Promise<boolean> {
  return new Promise((res) => {
    const s = net.connect({ port, host: '127.0.0.1' })
    s.once('connect', () => {
      s.destroy()
      res(true)
    })
    s.once('error', () => res(false))
  })
}

function spawnLogged(label: string, cmd: string, cwd: string): void {
  const log = createWriteStream(`/tmp/janus-${label}.log`, { flags: 'a' })
  // detached: 프로세스 그룹을 따로 만들어 uv가 낳는 python 자식까지 한 번에 죽인다
  const p = spawn('/bin/zsh', ['-c', cmd], { cwd, env, detached: true })
  p.stdout?.pipe(log)
  p.stderr?.pipe(log)
  p.on('exit', (code) => log.write(`\n[janus] ${label} exited: ${code}\n`))
  children.push(p)
}

async function spawnBackend(): Promise<void> {
  if (!(await portInUse(8765))) {
    spawnLogged('server', 'uv run python -m janus_server.server', join(repoRoot, 'janus_server'))
  }
  if (!(await portInUse(8080))) {
    spawnLogged(
      'mlx',
      'uv run mlx_vlm.server --model "$(ls -d ~/.cache/huggingface/hub/' +
        'models--orcarouter--Qwen3.8-27B-Uncensored-MLX/snapshots/*/4-bit)" --port 8080',
      join(repoRoot, 'qwen3.8mlx')
    )
  }
}

function killBackend(): void {
  for (const p of children) {
    if (p.pid == null || p.exitCode !== null) continue
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

app.whenReady().then(() => {
  spawnBackend() // 창보다 먼저 시작 — 모델 로드가 제일 오래 걸린다
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
