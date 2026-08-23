import { createHash, verify } from 'crypto'
import { createReadStream, createWriteStream } from 'fs'
import { mkdir, readFile, rename, stat } from 'fs/promises'
import { dirname } from 'path'
import { Readable } from 'stream'
import { pipeline } from 'stream/promises'

export interface UpdateManifest {
  version: string
  minSchemaVersion: number
  maxSchemaVersion: number
  targetSchemaVersion: number
  url: string
  sha256: string
  size: number
  publishedAt: string
}

export interface SignedUpdateFeed {
  manifest: UpdateManifest
  signature: string
}

function parseVersion(value: string): [number, number, number] {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(value)
  if (!match) throw new Error(`invalid semantic version: ${value}`)
  return [Number(match[1]), Number(match[2]), Number(match[3])]
}

export function compareVersions(left: string, right: string): number {
  const a = parseVersion(left)
  const b = parseVersion(right)
  for (let index = 0; index < a.length; index += 1) {
    if (a[index] !== b[index]) return a[index] < b[index] ? -1 : 1
  }
  return 0
}

export function canonicalManifest(manifest: UpdateManifest): string {
  return JSON.stringify({
    version: manifest.version,
    minSchemaVersion: manifest.minSchemaVersion,
    maxSchemaVersion: manifest.maxSchemaVersion,
    targetSchemaVersion: manifest.targetSchemaVersion,
    url: manifest.url,
    sha256: manifest.sha256,
    size: manifest.size,
    publishedAt: manifest.publishedAt
  })
}

export function verifyUpdateFeed(
  feed: SignedUpdateFeed,
  publicKey: string,
  currentVersion: string,
  currentSchemaVersion: number
): UpdateManifest {
  const { manifest } = feed
  if (!verify(
    null,
    Buffer.from(canonicalManifest(manifest)),
    publicKey,
    Buffer.from(feed.signature, 'base64')
  )) throw new Error('update manifest signature is invalid')
  if (compareVersions(manifest.version, currentVersion) <= 0) {
    throw new Error('update rollback or same-version install is forbidden')
  }
  if (
    !Number.isInteger(currentSchemaVersion) ||
    currentSchemaVersion < manifest.minSchemaVersion ||
    currentSchemaVersion > manifest.maxSchemaVersion ||
    manifest.targetSchemaVersion < currentSchemaVersion
  ) throw new Error('update is incompatible with the current database schema')
  if (!/^https:\/\//.test(manifest.url)) throw new Error('update artifact must use HTTPS')
  if (!/^[a-f0-9]{64}$/.test(manifest.sha256)) throw new Error('invalid SHA-256 digest')
  if (!Number.isSafeInteger(manifest.size) || manifest.size <= 0) {
    throw new Error('invalid update artifact size')
  }
  if (Number.isNaN(Date.parse(manifest.publishedAt))) throw new Error('invalid publication time')
  return manifest
}

async function sha256(path: string): Promise<string> {
  const hash = createHash('sha256')
  for await (const chunk of createReadStream(path)) hash.update(chunk)
  return hash.digest('hex')
}

export async function downloadVerifiedUpdate(
  manifest: UpdateManifest,
  destination: string,
  fetchUpdate: typeof fetch = fetch
): Promise<string> {
  const partial = `${destination}.part`
  await mkdir(dirname(destination), { recursive: true })
  const existing = await stat(partial).then((value) => value.size, () => 0)
  const headers = existing > 0 ? { Range: `bytes=${existing}-` } : undefined
  const response = await fetchUpdate(manifest.url, { headers })
  if (!response.ok || !response.body) throw new Error(`update download failed: ${response.status}`)

  const resumes = existing > 0 && response.status === 206
  await pipeline(
    Readable.fromWeb(response.body as never),
    createWriteStream(partial, { flags: resumes ? 'a' : 'w', mode: 0o600 })
  )
  const downloaded = (await stat(partial)).size
  if (downloaded !== manifest.size) {
    throw new Error(`update download interrupted: ${downloaded}/${manifest.size}`)
  }
  if (await sha256(partial) !== manifest.sha256) throw new Error('update artifact checksum mismatch')
  await rename(partial, destination)
  return destination
}

export async function readSignedUpdateFeed(path: string): Promise<SignedUpdateFeed> {
  return JSON.parse(await readFile(path, 'utf8')) as SignedUpdateFeed
}
