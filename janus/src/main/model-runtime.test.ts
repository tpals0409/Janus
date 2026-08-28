import assert from 'node:assert/strict'
import { mkdirSync, writeFileSync } from 'node:fs'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

import {
  DEFAULT_MODEL_ID, buildMlxLaunchSpec, hubRoot, observeMtpOutput, probeModel
} from './model-runtime.ts'

const STOCK = 'models--mlx-community--Qwen3.8-27B-4bit'
const UNCENSORED = 'models--orcarouter--Qwen3.8-27B-Uncensored-MLX'
const DRAFT = 'models--mlx-community--Qwen3.8-27B-MTP-4bit'

function config(root: string, repo: string, snapshot: string, suffix = ''): string {
  const path = join(root, '.cache', 'huggingface', 'hub', repo, 'snapshots', snapshot, suffix)
  mkdirSync(path, { recursive: true })
  writeFileSync(join(path, 'config.json'), '{}')
  return path
}

/** 샤드가 있는 모델. index가 가리키는 파일을 실제로 만들지 여부를 고른다. */
function sharded(root: string, repo: string, snapshot: string, complete: boolean, suffix = ''): string {
  const path = config(root, repo, snapshot, suffix)
  writeFileSync(join(path, 'model.safetensors.index.json'), JSON.stringify({
    weight_map: { 'a.weight': 'model-00001-of-00002.safetensors', 'b.weight': 'model-00002-of-00002.safetensors' }
  }))
  writeFileSync(join(path, 'model-00001-of-00002.safetensors'), 'x')
  if (complete) writeFileSync(join(path, 'model-00002-of-00002.safetensors'), 'y')
  return path
}

test('required MTP resolves validated snapshots and quotes explicit paths', async () => {
  const root = await mkdtemp(join(tmpdir(), 'janus-mtp-'))
  try {
    const model = config(root, STOCK, 'base')
    const draft = config(root, DRAFT, 'draft')
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
    config(root, STOCK, 'base')
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
    config(root, STOCK, 'base')
    config(root, DRAFT, 'draft')
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

// ── probeModel — 프로세스를 죽여보지 않고 모델 유무를 안다 ──

test('probe reports presence without launching anything', async () => {
  const root = await mkdtemp(join(tmpdir(), 'janus-probe-'))
  try {
    const missing = probeModel(root)
    assert.equal(missing.model.present, false)
    assert.equal(missing.model.incomplete, false)
    assert.equal(missing.draft.present, false)

    const path = config(root, STOCK, 'base')
    const found = probeModel(root)
    assert.equal(found.model.present, true)
    assert.equal(found.model.path, path)
    assert.equal(found.model.repo, 'mlx-community/Qwen3.8-27B-4bit')
  } finally {
    await rm(root, { recursive: true })
  }
})

test('a half-downloaded snapshot is incomplete, not present', async () => {
  // config.json만 보면 통과한다 — 그러면 사용자는 로딩 실패로만 알게 된다.
  const root = await mkdtemp(join(tmpdir(), 'janus-partial-'))
  try {
    sharded(root, STOCK, 'base', false)
    const probe = probeModel(root)
    assert.equal(probe.model.present, false)
    assert.equal(probe.model.incomplete, true)
    assert.match(buildMlxLaunchSpec(root).command, /snapshot is incomplete/)

    sharded(root, STOCK, 'base', true)
    assert.equal(probeModel(root).model.present, true)
  } finally {
    await rm(root, { recursive: true })
  }
})

test('the launch spec flips from stub to real once the download lands', async () => {
  // supervisor는 앱 시작 때 계산한 spec을 계속 재사용했다. 그래서 17GB를 다 받아도
  // `exit 78` 스텁만 30초마다 다시 돌았고, 설정을 건드려야 풀렸다. 고침은 스폰 직전에
  // 이 함수를 다시 부르는 것뿐이다 — 그 전제가 여기서 성립한다.
  const root = await mkdtemp(join(tmpdir(), 'janus-arrival-'))
  try {
    const before = buildMlxLaunchSpec(root)
    assert.match(before.command, /exit 78/)

    const model = sharded(root, STOCK, 'base', true)
    const draft = config(root, DRAFT, 'draft')

    const after = buildMlxLaunchSpec(root)
    assert.notEqual(after.command, before.command)
    assert.doesNotMatch(after.command, /exit 78/)
    assert.match(after.command, new RegExp(`--model '${model}'`))
    assert.match(after.command, new RegExp(`--draft-model '${draft}'`))
  } finally {
    await rm(root, { recursive: true })
  }
})

test('each model keeps its own snapshot layout', async () => {
  // mlx-community는 스냅샷 루트에, orcarouter는 4-bit/ 아래에 있다.
  const root = await mkdtemp(join(tmpdir(), 'janus-layout-'))
  try {
    config(root, UNCENSORED, 'base', '4-bit')
    assert.equal(probeModel(root, DEFAULT_MODEL_ID).model.present, false)
    const advanced = probeModel(root, 'qwen3.8-27b-uncensored')
    assert.equal(advanced.model.present, true)
    assert.match(advanced.model.path ?? '', /4-bit$/)
  } finally {
    await rm(root, { recursive: true })
  }
})

test('an unknown model id falls back to the default rather than throwing', async () => {
  const root = await mkdtemp(join(tmpdir(), 'janus-unknown-'))
  try {
    const path = config(root, STOCK, 'base')
    assert.equal(probeModel(root, 'no-such-model').model.path, path)
  } finally {
    await rm(root, { recursive: true })
  }
})

test('the hub root honors HF_HOME and HF_HUB_CACHE like the hf CLI', () => {
  assert.equal(hubRoot('/home/u', {}), '/home/u/.cache/huggingface/hub')
  assert.equal(hubRoot('/home/u', { HF_HOME: '/data/hf' }), '/data/hf/hub')
  // HF_HUB_CACHE가 더 구체적이라 이긴다.
  assert.equal(
    hubRoot('/home/u', { HF_HOME: '/data/hf', HF_HUB_CACHE: '/mnt/models' }), '/mnt/models'
  )
})

test('probe follows HF_HOME so a relocated cache is not reported missing', async () => {
  const root = await mkdtemp(join(tmpdir(), 'janus-hfhome-'))
  try {
    const path = join(root, '.cache', 'huggingface', 'hub', STOCK, 'snapshots', 'base')
    mkdirSync(path, { recursive: true })
    writeFileSync(join(path, 'config.json'), '{}')
    const probe = probeModel('/nowhere', DEFAULT_MODEL_ID, {
      HF_HOME: join(root, '.cache', 'huggingface')
    })
    assert.equal(probe.model.present, true)
    assert.equal(probe.model.path, path)
  } finally {
    await rm(root, { recursive: true })
  }
})
