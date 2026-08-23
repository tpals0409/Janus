import { createHash } from 'node:crypto'
import { createReadStream, existsSync, readdirSync } from 'node:fs'
import { writeFile } from 'node:fs/promises'
import { basename, join, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { pathToFileURL } from 'node:url'

function findArtifact(root, suffix) {
  if (!existsSync(root)) throw new Error(`release output directory not found: ${root}`)
  const pending = [root]
  while (pending.length) {
    const directory = pending.pop()
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name)
      if (entry.name.endsWith(suffix)) return path
      if (entry.isDirectory()) pending.push(path)
    }
  }
  throw new Error(`release artifact not found: *${suffix}`)
}

function systemRun(command, args) {
  const result = spawnSync(command, args, { encoding: 'utf8', stdio: 'pipe' })
  if (result.status !== 0) {
    const detail = `${result.stdout ?? ''}${result.stderr ?? ''}`.trim()
    throw new Error(`${command} ${args.join(' ')} failed${detail ? `: ${detail}` : ''}`)
  }
}

async function digest(path) {
  const hash = createHash('sha256')
  for await (const chunk of createReadStream(path)) hash.update(chunk)
  return hash.digest('hex')
}

export async function verifyMacRelease({ appPath, dmgPath, zipPath, outputPath, run = systemRun }) {
  run('codesign', ['--verify', '--deep', '--strict', '--verbose=2', appPath])
  run('spctl', ['--assess', '--type', 'execute', '--verbose=2', appPath])
  run('xcrun', ['stapler', 'validate', appPath])
  run('spctl', ['--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=2', dmgPath])
  run('xcrun', ['stapler', 'validate', dmgPath])

  const lines = []
  for (const path of [dmgPath, zipPath]) lines.push(`${await digest(path)}  ${basename(path)}`)
  await writeFile(outputPath, `${lines.join('\n')}\n`, { encoding: 'utf8', mode: 0o600 })
  return { appPath, dmgPath, zipPath, outputPath, checksums: lines }
}

export async function verifyMacReleaseDirectory(directory, run = systemRun) {
  const root = resolve(directory)
  return verifyMacRelease({
    appPath: findArtifact(root, '.app'),
    dmgPath: findArtifact(root, '.dmg'),
    zipPath: findArtifact(root, '.zip'),
    outputPath: join(root, 'SHA256SUMS'),
    run
  })
}

if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = await verifyMacReleaseDirectory(process.argv[2] ?? 'dist')
  process.stdout.write(`verified ${result.appPath}\nchecksums ${result.outputPath}\n`)
}
