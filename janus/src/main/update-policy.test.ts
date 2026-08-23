import assert from 'node:assert/strict'
import { generateKeyPairSync, sign, createHash } from 'node:crypto'
import { mkdtemp, readFile, stat } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import {
  canonicalManifest,
  downloadVerifiedUpdate,
  verifyUpdateFeed,
  type SignedUpdateFeed,
  type UpdateManifest
} from './update-policy.ts'

const keys = generateKeyPairSync('ed25519')
const publicKey = keys.publicKey.export({ type: 'spki', format: 'pem' }).toString()

function signedFeed(changes: Partial<UpdateManifest> = {}): SignedUpdateFeed {
  const bytes = Buffer.from('janus-v1')
  const manifest: UpdateManifest = {
    version: '1.1.0',
    minSchemaVersion: 10,
    maxSchemaVersion: 13,
    targetSchemaVersion: 14,
    url: 'https://releases.example.test/Janus-1.1.0.zip',
    sha256: createHash('sha256').update(bytes).digest('hex'),
    size: bytes.length,
    publishedAt: '2026-08-23T00:00:00Z',
    ...changes
  }
  return {
    manifest,
    signature: sign(null, Buffer.from(canonicalManifest(manifest)), keys.privateKey).toString('base64')
  }
}

test('accepts a signed forward update compatible with the database', () => {
  assert.equal(verifyUpdateFeed(signedFeed(), publicKey, '1.0.0', 13).version, '1.1.0')
})

test('rejects tampering, rollback, and schema incompatibility', () => {
  const tampered = signedFeed()
  tampered.manifest.url = 'https://attacker.example/Janus.zip'
  assert.throws(() => verifyUpdateFeed(tampered, publicKey, '1.0.0', 13), /signature/)
  assert.throws(
    () => verifyUpdateFeed(signedFeed({ version: '0.9.0' }), publicKey, '1.0.0', 13),
    /rollback/
  )
  assert.throws(
    () => verifyUpdateFeed(signedFeed({ maxSchemaVersion: 12 }), publicKey, '1.0.0', 13),
    /schema/
  )
})

test('resumes an interrupted download and verifies the final checksum', async () => {
  const root = await mkdtemp(join(tmpdir(), 'janus-update-'))
  const destination = join(root, 'Janus.zip')
  const content = Buffer.from('janus-v1')
  const feed = signedFeed()
  let attempt = 0
  const interruptedFetch = async (_url: string | URL | Request, init?: RequestInit) => {
    attempt += 1
    if (attempt === 1) return new Response(content.subarray(0, 4), { status: 200 })
    assert.equal((init?.headers as Record<string, string>).Range, 'bytes=4-')
    return new Response(content.subarray(4), { status: 206 })
  }

  await assert.rejects(
    downloadVerifiedUpdate(feed.manifest, destination, interruptedFetch as typeof fetch),
    /interrupted/
  )
  assert.equal((await stat(`${destination}.part`)).size, 4)
  await downloadVerifiedUpdate(feed.manifest, destination, interruptedFetch as typeof fetch)
  assert.deepEqual(await readFile(destination), content)
})
