import { existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

export type MtpPolicy = 'required' | 'preferred' | 'off'

export interface MtpRuntimeState {
  policy: MtpPolicy
  configured: boolean
  active: boolean
  kind: 'mtp' | null
  draftModelPath: string | null
  lastError: string | null
}

export interface MlxLaunchSpec {
  command: string
  mtp: MtpRuntimeState
}

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`
}

function snapshots(root: string, suffix = ''): string[] {
  if (!existsSync(root)) return []
  return readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => join(root, entry.name, suffix))
    .filter((path) => existsSync(join(path, 'config.json')))
    .sort()
    .reverse()
}

export function buildMlxLaunchSpec(
  homeDir: string,
  policy: MtpPolicy = 'required'
): MlxLaunchSpec {
  const hub = join(homeDir, '.cache', 'huggingface', 'hub')
  const model = snapshots(join(
    hub, 'models--orcarouter--Qwen3.8-27B-Uncensored-MLX', 'snapshots'
  ), '4-bit')[0] ?? null
  const draft = snapshots(join(
    hub, 'models--mlx-community--Qwen3.8-27B-MTP-4bit', 'snapshots'
  ))[0] ?? null
  const mtp: MtpRuntimeState = {
    policy,
    configured: policy !== 'off' && draft !== null,
    active: false,
    kind: policy !== 'off' && draft !== null ? 'mtp' : null,
    draftModelPath: policy !== 'off' ? draft : null,
    lastError: null
  }
  if (!model) {
    return {
      command: "echo '[janus] Qwen 27B model snapshot is missing or incomplete' >&2; exit 78",
      mtp: { ...mtp, lastError: 'Qwen 27B model snapshot is missing or incomplete' }
    }
  }
  if (policy === 'required' && !draft) {
    return {
      command: "echo '[janus] required MTP snapshot is missing or incomplete' >&2; exit 78",
      mtp: { ...mtp, lastError: 'required MTP snapshot is missing or incomplete' }
    }
  }
  const draftArgs = draft && policy !== 'off'
    ? ` --draft-model ${shellQuote(draft)} --draft-kind mtp`
    : ''
  // 서버 프롬프트 캐시(APC): 에이전트 루프의 안정 prefix(system+summary)를
  // 요청 간 재사용해 prefill을 줄인다. JANUS_APC=0으로 끌 수 있다.
  const apcEnv = process.env.JANUS_APC === '0' ? '' : 'APC_ENABLED=1 '
  return {
    command: `${apcEnv}uv run --frozen mlx_vlm.server --model ${shellQuote(model)} --port 8080${draftArgs}`,
    mtp
  }
}

export function observeMtpOutput(state: MtpRuntimeState, chunk: unknown): 'active' | 'failed' | null {
  const text = String(chunk)
  if (text.includes('Drafter ready; speculative decoding enabled.')) {
    state.active = true
    state.kind = 'mtp'
    state.lastError = null
    return 'active'
  }
  if (
    text.includes('falling back to autoregressive generation')
    || text.includes('Speculative drafter is incompatible')
  ) {
    state.active = false
    state.lastError = 'MTP drafter is incompatible; autoregressive fallback was rejected'
    return 'failed'
  }
  return null
}
