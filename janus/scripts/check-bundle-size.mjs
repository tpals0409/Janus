import { readdirSync, statSync } from 'node:fs'
import { resolve } from 'node:path'

const assets = resolve('out/renderer/assets')
const files = readdirSync(assets)
const sizes = Object.fromEntries(files.map((name) => [name, statSync(resolve(assets, name)).size]))

const largest = (prefix) => Object.entries(sizes)
  .filter(([name]) => name.startsWith(prefix) && name.endsWith('.js'))
  .sort((left, right) => right[1] - left[1])[0]

const checks = [
  ['initial renderer', largest('index-'), 1_300_000],
  ['development surface', largest('TaskDevelopmentSurface-'), 5_500_000],
]
const failures = []
for (const [label, entry, limit] of checks) {
  if (!entry) failures.push(`${label}: chunk missing`)
  else if (entry[1] > limit) failures.push(`${label}: ${entry[1]} > ${limit} bytes (${entry[0]})`)
  else console.log(`${label}: ${(entry[1] / 1_000_000).toFixed(2)} MB / ${(limit / 1_000_000).toFixed(2)} MB`)
}

const workerBytes = Object.entries(sizes)
  .filter(([name]) => name.includes('.worker-') && name.endsWith('.js'))
  .reduce((total, [, size]) => total + size, 0)
if (workerBytes > 0) failures.push(`unexpected Monaco workers: ${workerBytes} bytes`)

if (failures.length) {
  for (const failure of failures) console.error(failure)
  process.exit(1)
}
