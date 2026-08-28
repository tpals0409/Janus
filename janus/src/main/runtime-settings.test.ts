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
    assert.deepEqual(saved, {
      localServer: true, modelId: 'qwen3.8-27b', mtpPolicy: 'required', modelSlots: 8, apc: false
    })
    assert.deepEqual(loadRuntimeSettings(file), saved)
    assert.deepEqual(
      saveRuntimeSettings(file, { localServer: false, mtpPolicy: 'off', modelSlots: 0, apc: true }),
      { localServer: false, modelId: 'qwen3.8-27b', mtpPolicy: 'off', modelSlots: 1, apc: true }
    )
  } finally {
    await rm(root, { recursive: true })
  }
})

test('modelId falls back to the default when it is unknown or absent', async () => {
  const root = await mkdtemp(join(tmpdir(), 'janus-settings-'))
  try {
    const file = join(root, 'runtime-settings.json')
    assert.equal(loadRuntimeSettings(file).modelId, RUNTIME_SETTINGS_DEFAULTS.modelId)
    // 카탈로그에 없는 값은 조용히 기본값으로 — 지운 모델 id가 저장돼 있어도 앱이 뜬다.
    assert.equal(saveRuntimeSettings(file, { modelId: 'ghost-model' }).modelId,
                 RUNTIME_SETTINGS_DEFAULTS.modelId)
    assert.equal(saveRuntimeSettings(file, { modelId: 'qwen3.8-27b-uncensored' }).modelId,
                 'qwen3.8-27b-uncensored')
    assert.equal(loadRuntimeSettings(file).modelId, 'qwen3.8-27b-uncensored')
  } finally {
    await rm(root, { recursive: true })
  }
})
