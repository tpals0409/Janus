import assert from 'node:assert/strict'
import { createHash, generateKeyPairSync, sign } from 'node:crypto'
import { EventEmitter } from 'node:events'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { canonicalManifest, type SignedUpdateFeed, type UpdateManifest } from './update-policy.ts'
import { createLocalUpdateFeed, SecureUpdater, type AutoUpdaterPort } from './secure-updater.ts'

class FakeAutoUpdater extends EventEmitter implements AutoUpdaterPort {
  feedUrl = ''
  checks = 0
  installs = 0
  order: string[] = []

  setFeedURL(options: { url: string }): void {
    this.feedUrl = options.url
  }
  checkForUpdates(): void {
    this.checks += 1
    this.order.push('check')
    queueMicrotask(() => this.emit('update-downloaded'))
  }
  quitAndInstall(): void {
    this.installs += 1
  }
}

const keys = generateKeyPairSync('ed25519')
const publicKey = keys.publicKey.export({ type: 'spki', format: 'pem' }).toString()

function feedFor(content: Buffer): SignedUpdateFeed {
  const manifest: UpdateManifest = {
    version: '1.1.0', minSchemaVersion: 10, maxSchemaVersion: 13,
    targetSchemaVersion: 14, url: 'https://updates.example/Janus.zip',
    sha256: createHash('sha256').update(content).digest('hex'), size: content.length,
    publishedAt: '2026-08-23T00:00:00Z'
  }
  return {
    manifest,
    signature: sign(null, Buffer.from(canonicalManifest(manifest)), keys.privateKey).toString('base64')
  }
}

test('verified update downloads, backs up, then hands exact package to autoUpdater', async () => {
  const content = Buffer.from('signed-janus-zip')
  const signed = feedFor(content)
  const root = await mkdtemp(join(tmpdir(), 'janus-updater-'))
  const autoUpdater = new FakeAutoUpdater()
  const order: string[] = []
  autoUpdater.order = order
  const fetchUpdate = async (input: string | URL | Request) => {
    const url = String(input)
    if (url.endsWith('/feed.json')) return Response.json(signed)
    if (url.endsWith('/Janus.zip')) return new Response(content)
    throw new Error(`unexpected URL: ${url}`)
  }
  const updater = new SecureUpdater({
    enabled: true,
    feedUrl: 'https://updates.example/feed.json',
    publicKey,
    currentVersion: '1.0.0',
    downloadRoot: root,
    autoUpdater,
    currentSchemaVersion: async () => 13,
    createBackup: async () => { order.push('backup') },
    fetchUpdate: fetchUpdate as typeof fetch,
    createLocalFeed: async (_manifest, artifactPath) => {
      assert.deepEqual(await readFile(artifactPath), content)
      order.push('local-feed')
      return { url: 'http://127.0.0.1:41000/feed', close: async () => undefined }
    }
  })

  const status = await updater.check()
  assert.equal(status.phase, 'install-ready')
  assert.deepEqual(order, ['backup', 'local-feed', 'check'])
  updater.install()
  assert.equal(autoUpdater.installs, 1)
})

test('backup failure prevents autoUpdater handoff', async () => {
  const content = Buffer.from('signed-janus-zip')
  const signed = feedFor(content)
  const root = await mkdtemp(join(tmpdir(), 'janus-updater-'))
  const autoUpdater = new FakeAutoUpdater()
  const updater = new SecureUpdater({
    enabled: true,
    feedUrl: 'https://updates.example/feed.json',
    publicKey,
    currentVersion: '1.0.0',
    downloadRoot: root,
    autoUpdater,
    currentSchemaVersion: async () => 13,
    createBackup: async () => { throw new Error('backup failed') },
    fetchUpdate: (async (input: string | URL | Request) =>
      String(input).endsWith('/feed.json') ? Response.json(signed) : new Response(content)
    ) as typeof fetch
  })
  assert.equal((await updater.check()).phase, 'error')
  assert.equal(autoUpdater.checks, 0)
})

test('local feed serves only the already verified package', async () => {
  const content = Buffer.from('verified-local-package')
  const root = await mkdtemp(join(tmpdir(), 'janus-feed-'))
  const path = join(root, 'Janus.zip')
  await import('node:fs/promises').then(({ writeFile }) => writeFile(path, content))
  const manifest = feedFor(content).manifest
  const local = await createLocalUpdateFeed(manifest, path)
  try {
    const payload = await (await fetch(local.url)).json() as { url: string; name: string }
    assert.equal(payload.name, '1.1.0')
    assert.deepEqual(Buffer.from(await (await fetch(payload.url)).arrayBuffer()), content)
  } finally {
    await local.close()
  }
})
