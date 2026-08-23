import { createReadStream } from 'fs'
import { stat } from 'fs/promises'
import { createServer, type Server } from 'http'
import { join } from 'path'
import type { AddressInfo } from 'net'
import { EventEmitter } from 'events'
import {
  downloadVerifiedUpdate,
  verifyUpdateFeed,
  type SignedUpdateFeed,
  type UpdateManifest
} from './update-policy.ts'

export type UpdatePhase =
  | 'disabled'
  | 'idle'
  | 'checking'
  | 'downloading'
  | 'backing-up'
  | 'install-ready'
  | 'up-to-date'
  | 'error'

export interface UpdateStatus {
  phase: UpdatePhase
  version: string | null
  message: string
}

export interface AutoUpdaterPort extends EventEmitter {
  setFeedURL(options: { url: string; serverType: 'json' }): void
  checkForUpdates(): void
  quitAndInstall(): void
}

export interface LocalUpdateFeed {
  url: string
  close(): Promise<void>
}

export interface SecureUpdaterOptions {
  enabled: boolean
  feedUrl: string
  publicKey: string
  currentVersion: string
  downloadRoot: string
  autoUpdater: AutoUpdaterPort
  currentSchemaVersion(): Promise<number>
  createBackup(): Promise<void>
  fetchUpdate?: typeof fetch
  createLocalFeed?: (manifest: UpdateManifest, artifactPath: string) => Promise<LocalUpdateFeed>
}

function responseError(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function updaterResult(
  autoUpdater: AutoUpdaterPort,
  close: () => Promise<void>
): Promise<'downloaded' | 'none'> {
  return new Promise((resolve, reject) => {
    const cleanup = (): void => {
      autoUpdater.removeListener('update-downloaded', downloaded)
      autoUpdater.removeListener('update-not-available', unavailable)
      autoUpdater.removeListener('error', failed)
    }
    const finish = (result: 'downloaded' | 'none'): void => {
      cleanup()
      void close().finally(() => resolve(result))
    }
    const downloaded = (): void => finish('downloaded')
    const unavailable = (): void => finish('none')
    const failed = (error: Error): void => {
      cleanup()
      void close().finally(() => reject(error))
    }
    autoUpdater.once('update-downloaded', downloaded)
    autoUpdater.once('update-not-available', unavailable)
    autoUpdater.once('error', failed)
  })
}

export class SecureUpdater {
  private status: UpdateStatus
  private checking: Promise<UpdateStatus> | null = null
  private readonly options: SecureUpdaterOptions

  constructor(options: SecureUpdaterOptions) {
    this.options = options
    this.status = options.enabled
      ? { phase: 'idle', version: null, message: '업데이트 확인 가능' }
      : { phase: 'disabled', version: null, message: '검증된 업데이트 피드가 구성되지 않음' }
  }

  snapshot(): UpdateStatus {
    return { ...this.status }
  }

  check(): Promise<UpdateStatus> {
    if (!this.options.enabled) return Promise.resolve(this.snapshot())
    if (this.checking) return this.checking
    this.checking = this.runCheck().finally(() => { this.checking = null })
    return this.checking
  }

  install(): void {
    if (this.status.phase !== 'install-ready') throw new Error('설치 준비된 업데이트가 없습니다')
    this.options.autoUpdater.quitAndInstall()
  }

  private async runCheck(): Promise<UpdateStatus> {
    let localFeed: LocalUpdateFeed | null = null
    try {
      this.status = { phase: 'checking', version: null, message: '서명된 manifest 확인 중' }
      const response = await (this.options.fetchUpdate ?? fetch)(this.options.feedUrl, {
        headers: { Accept: 'application/json' }
      })
      if (!response.ok) throw new Error(`update feed request failed: ${response.status}`)
      const feed = await response.json() as SignedUpdateFeed
      const schemaVersion = await this.options.currentSchemaVersion()
      const manifest = verifyUpdateFeed(
        feed,
        this.options.publicKey,
        this.options.currentVersion,
        schemaVersion
      )

      this.status = {
        phase: 'downloading', version: manifest.version,
        message: '검증 가능한 update package 다운로드 중'
      }
      const artifactPath = join(this.options.downloadRoot, `Janus-${manifest.version}.zip`)
      await downloadVerifiedUpdate(
        manifest,
        artifactPath,
        this.options.fetchUpdate ?? fetch
      )

      this.status = {
        phase: 'backing-up', version: manifest.version,
        message: '설치 전 데이터베이스 backup 생성 중'
      }
      await this.options.createBackup()

      localFeed = await (this.options.createLocalFeed ?? createLocalUpdateFeed)(
        manifest,
        artifactPath
      )
      this.options.autoUpdater.setFeedURL({ url: localFeed.url, serverType: 'json' })
      const result = updaterResult(this.options.autoUpdater, () => localFeed!.close())
      this.options.autoUpdater.checkForUpdates()
      if (await result === 'none') {
        this.status = {
          phase: 'up-to-date', version: null,
          message: '현재 버전이 최신 상태'
        }
      } else {
        this.status = {
          phase: 'install-ready', version: manifest.version,
          message: '검증·backup 완료 · 재시작하여 설치 가능'
        }
      }
    } catch (error) {
      if (localFeed) await localFeed.close().catch(() => undefined)
      this.status = { phase: 'error', version: null, message: responseError(error) }
    }
    return this.snapshot()
  }
}

function closeServer(server: Server): Promise<void> {
  return new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve())
  })
}

export async function createLocalUpdateFeed(
  manifest: UpdateManifest,
  artifactPath: string
): Promise<LocalUpdateFeed> {
  const artifact = await stat(artifactPath)
  const server = createServer((request, response) => {
    const address = server.address() as AddressInfo
    const base = `http://127.0.0.1:${address.port}`
    if (request.url === '/feed') {
      const payload = JSON.stringify({
        url: `${base}/artifact.zip`,
        name: manifest.version,
        pub_date: manifest.publishedAt
      })
      response.writeHead(200, {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
        'Cache-Control': 'no-store'
      })
      response.end(payload)
      return
    }
    if (request.url === '/artifact.zip') {
      response.writeHead(200, {
        'Content-Type': 'application/zip',
        'Content-Length': artifact.size,
        'Cache-Control': 'no-store'
      })
      if (request.method === 'HEAD') response.end()
      else createReadStream(artifactPath).pipe(response)
      return
    }
    response.writeHead(404).end()
  })
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const address = server.address() as AddressInfo
  let closed = false
  return {
    url: `http://127.0.0.1:${address.port}/feed`,
    async close() {
      if (closed) return
      closed = true
      await closeServer(server)
    }
  }
}
