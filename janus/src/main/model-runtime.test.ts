import assert from 'node:assert/strict'
import { mkdirSync, writeFileSync } from 'node:fs'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import { buildMlxLaunchSpec, observeMtpOutput } from './model-runtime.ts'

function config(root: string, repo: string, snapshot: string, suffix = ''): string {
  const path = join(root, '.cache', 'huggingface', 'hub', repo, 'snapshots', snapshot, suffix)
  mkdirSync(path, { recursive: true })
  writeFileSync(join(path, 'config.json'), '{}')
  return path
}

test('required MTP resolves validated snapshots and quotes explicit paths', async () => {
  const root = await mkdtemp(join(tmpdir(), 'janus-mtp-'))
  try {
    const model = config(root, 'models--orcarouter--Qwen3.8-27B-Uncensored-MLX', 'base', '4-bit')
    const draft = config(root, 'models--mlx-community--Qwen3.8-27B-MTP-4bit', 'draft')
    const spec = buildMlxLaunchSpec(root)
    assert.equal(spec.mtp.configured, true)
    assert.equal(spec.mtp.policy, 'required')
    assert.match(spec.command, new RegExp(`--model '${model}'`))
    assert.match(spec.command, new RegExp(`--draft-model '${draft}' --draft-kind mtp`))
  } finally {
    await rm(root, { recursive: true })
  }
})

test('required MTP fails closed when its validated snapshot is absent', async () => {
  const root = await mkdtemp(join(tmpdir(), 'janus-mtp-'))
  try {
    config(root, 'models--orcarouter--Qwen3.8-27B-Uncensored-MLX', 'base', '4-bit')
    const spec = buildMlxLaunchSpec(root)
    assert.equal(spec.mtp.configured, false)
    assert.match(spec.command, /required MTP snapshot is missing/)
    assert.match(spec.command, /exit 78/)
  } finally {
    await rm(root, { recursive: true })
  }
})

test('server launch enables prompt caching unless JANUS_APC=0 opts out', async () => {
  const root = await mkdtemp(join(tmpdir(), 'janus-apc-'))
  try {
    config(root, 'models--orcarouter--Qwen3.8-27B-Uncensored-MLX', 'base', '4-bit')
    config(root, 'models--mlx-community--Qwen3.8-27B-MTP-4bit', 'draft')
    assert.match(buildMlxLaunchSpec(root).command, /^APC_ENABLED=1 uv run/)
    process.env.JANUS_APC = '0'
    try {
      assert.doesNotMatch(buildMlxLaunchSpec(root).command, /APC_ENABLED/)
    } finally {
      delete process.env.JANUS_APC
    }
  } finally {
    await rm(root, { recursive: true })
  }
})

test('MTP activation and incompatible fallback are observable', () => {
  const state = buildMlxLaunchSpec('/missing').mtp
  assert.equal(observeMtpOutput(state, 'Drafter ready; speculative decoding enabled.'), 'active')
  assert.equal(state.active, true)
  assert.equal(observeMtpOutput(
    state, 'Speculative drafter is incompatible; falling back to autoregressive generation'
  ), 'failed')
  assert.equal(state.active, false)
  assert.match(state.lastError ?? '', /fallback was rejected/)
})
