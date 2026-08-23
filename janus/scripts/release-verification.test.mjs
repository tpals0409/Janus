import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { verifyMacRelease, verifyMacReleaseDirectory } from './verify-macos-release.mjs'

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'janus-release-'))
  const appPath = join(root, 'mac-arm64', 'Janus.app')
  const dmgPath = join(root, 'Janus-1.0.0-arm64.dmg')
  const zipPath = join(root, 'Janus-1.0.0-arm64-mac.zip')
  await mkdir(appPath, { recursive: true })
  await writeFile(dmgPath, 'dmg-content')
  await writeFile(zipPath, 'zip-content')
  return { root, appPath, dmgPath, zipPath, outputPath: join(root, 'SHA256SUMS') }
}

test('signed release gate verifies app, Gatekeeper, staples, and emits stable checksums', async () => {
  const paths = await fixture()
  const calls = []
  const result = await verifyMacRelease({ ...paths, run: (command, args) => calls.push([command, args]) })
  assert.deepEqual(calls, [
    ['codesign', ['--verify', '--deep', '--strict', '--verbose=2', paths.appPath]],
    ['spctl', ['--assess', '--type', 'execute', '--verbose=2', paths.appPath]],
    ['xcrun', ['stapler', 'validate', paths.appPath]],
    ['spctl', ['--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=2', paths.dmgPath]],
    ['xcrun', ['stapler', 'validate', paths.dmgPath]]
  ])
  const expected = [
    `${createHash('sha256').update('dmg-content').digest('hex')}  Janus-1.0.0-arm64.dmg`,
    `${createHash('sha256').update('zip-content').digest('hex')}  Janus-1.0.0-arm64-mac.zip`
  ]
  assert.deepEqual(result.checksums, expected)
  assert.equal(await readFile(paths.outputPath, 'utf8'), `${expected.join('\n')}\n`)
})

test('directory verifier refuses an incomplete release set', async () => {
  const paths = await fixture()
  await import('node:fs/promises').then(({ rm }) => rm(paths.zipPath))
  await assert.rejects(verifyMacReleaseDirectory(paths.root, () => undefined), /\.zip/)
})
