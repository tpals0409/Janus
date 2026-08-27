import assert from 'node:assert/strict'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import { RUNTIME_SETTINGS_DEFAULTS, loadRuntimeSettings, saveRuntimeSettings } from './runtime-settings.ts'

test('missing or corrupt settings fall back to defaults', async () => {
  const root = await mkdtemp(join(tmpdir(), 'janus-settings-'))
  try {
    assert.deepEqual(loadRuntimeSettings(join(root, 'nope.json')), RUNTIME_SETTINGS_DEFAULTS)
  } finally {
    await rm(root, { recursive: true })
  }
})

test('save clamps values and load round-trips them', async () => {
  const root = await mkdtemp(join(tmpdir(), 'janus-settings-'))
  const file = join(root, 'runtime-settings.json')
  try {
    const saved = saveRuntimeSettings(file, {
      mtpPolicy: 'weird' as never, modelSlots: 99, apc: false
    })
    assert.deepEqual(saved, { localServer: true, mtpPolicy: 'required', modelSlots: 8, apc: false })
    assert.deepEqual(loadRuntimeSettings(file), saved)
    assert.deepEqual(
      saveRuntimeSettings(file, { localServer: false, mtpPolicy: 'off', modelSlots: 0, apc: true }),
      { localServer: false, mtpPolicy: 'off', modelSlots: 1, apc: true }
    )
  } finally {
    await rm(root, { recursive: true })
  }
})
