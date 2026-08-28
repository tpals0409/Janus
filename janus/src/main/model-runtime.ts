import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

export type MtpPolicy = 'required' | 'preferred' | 'off'

/** 로컬에 둘 수 있는 모델. repo마다 파일 배치가 달라 하위 경로를 쌍으로 들고 있어야 한다 —
 *  mlx-community는 safetensors가 repo 루트에, orcarouter는 `4-bit/` 아래에 있다. */
export interface LocalModelEntry {
  id: string
  repo: string
  /** 스냅샷 안에서 config.json이 있는 하위 경로. 루트면 '' */
  subpath: string
  /** hf download --include 패턴. 전체를 받으면 null */
  include: string | null
  label: string
  /** 모델 카드가 사용 범위를 제한하면 그 경고를 여기 둔다 */
  advisory: string | null
}

export const LOCAL_MODELS: readonly LocalModelEntry[] = [
  {
    id: 'qwen3.8-27b',
    repo: 'mlx-community/Qwen3.8-27B-4bit',
    subpath: '',
    include: null,
    label: 'Qwen3.8 27B (4-bit MLX)',
    advisory: null
  },
  {
    id: 'qwen3.8-27b-uncensored',
    repo: 'orcarouter/Qwen3.8-27B-Uncensored-MLX',
    subpath: '4-bit',
    include: '4-bit/*',
    label: 'Qwen3.8 27B Uncensored (4-bit MLX)',
    advisory: '모델 카드가 연구 목적으로 한정하고, 자체 모더레이션 계층 없는 최종 사용자 '
      + '배포를 범위 밖으로 명시합니다. 안전 튜닝이 제거된 가중치입니다.'
  }
]

export const DEFAULT_MODEL_ID = LOCAL_MODELS[0].id

export const DRAFT_MODEL: LocalModelEntry = {
  id: 'qwen3.8-27b-mtp',
  repo: 'mlx-community/Qwen3.8-27B-MTP-4bit',
  subpath: '',
  include: null,
  label: 'MTP 드래프터',
  advisory: null
}

export function findModel(modelId: string): LocalModelEntry {
  return LOCAL_MODELS.find((entry) => entry.id === modelId) ?? LOCAL_MODELS[0]
}

/** HF 캐시 루트. hf CLI와 같은 우선순위를 따른다 — 여기서 어긋나면 다운로드는 성공하는데
 *  Janus만 "없음"이라고 보고하게 된다. */
export function hubRoot(homeDir: string, env: NodeJS.ProcessEnv = process.env): string {
  if (env.HF_HUB_CACHE) return env.HF_HUB_CACHE
  if (env.HF_HOME) return join(env.HF_HOME, 'hub')
  return join(homeDir, '.cache', 'huggingface', 'hub')
}

function repoDir(root: string, repo: string): string {
  return join(root, `models--${repo.replaceAll('/', '--')}`, 'snapshots')
}

export interface ModelPresence {
  id: string
  repo: string
  label: string
  present: boolean
  path: string | null
  /** 있지만 샤드가 빠진 경우 — 재개 다운로드로 고칠 수 있다 */
  incomplete: boolean
}

export interface ModelProbe {
  hubRoot: string
  model: ModelPresence
  draft: ModelPresence
}

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

/** config.json만 보면 반쯤 받다 만 스냅샷도 통과한다. index가 있으면 거기 나열된
 *  샤드가 전부 있는지까지 본다 — 이게 없으면 사용자는 로딩 실패로만 알게 된다. */
function shardsComplete(path: string): boolean {
  const index = join(path, 'model.safetensors.index.json')
  if (!existsSync(index)) return true  // 단일 파일 모델
  try {
    const map = JSON.parse(readFileSync(index, 'utf-8')) as { weight_map?: Record<string, string> }
    const shards = new Set(Object.values(map.weight_map ?? {}))
    return [...shards].every((shard) => existsSync(join(path, shard)))
  } catch {
    return false
  }
}

function presence(root: string, entry: LocalModelEntry): ModelPresence {
  const found = snapshots(repoDir(root, entry.repo), entry.subpath)[0] ?? null
  const complete = found !== null && shardsComplete(found)
  return {
    id: entry.id,
    repo: entry.repo,
    label: entry.label,
    present: complete,
    path: complete ? found : null,
    incomplete: found !== null && !complete
  }
}

/** 모델이 있는지 묻는 유일한 구현. buildMlxLaunchSpec도 이걸 쓴다 —
 *  전에는 프로세스를 띄워 exit 78로 죽여야만 알 수 있었다. */
export function probeModel(
  homeDir: string,
  modelId: string = DEFAULT_MODEL_ID,
  env: NodeJS.ProcessEnv = process.env
): ModelProbe {
  const root = hubRoot(homeDir, env)
  return {
    hubRoot: root,
    model: presence(root, findModel(modelId)),
    draft: presence(root, DRAFT_MODEL)
  }
}

export function buildMlxLaunchSpec(
  homeDir: string,
  policy: MtpPolicy = 'required',
  apc: boolean = process.env.JANUS_APC !== '0',
  modelId: string = DEFAULT_MODEL_ID
): MlxLaunchSpec {
  const probe = probeModel(homeDir, modelId)
  const model = probe.model.path
  const draft = probe.draft.path
  const mtp: MtpRuntimeState = {
    policy,
    configured: policy !== 'off' && draft !== null,
    active: false,
    kind: policy !== 'off' && draft !== null ? 'mtp' : null,
    draftModelPath: policy !== 'off' ? draft : null,
    lastError: null
  }
  if (!model) {
    const reason = probe.model.incomplete
      ? `${probe.model.repo} snapshot is incomplete — resume the download`
      : `${probe.model.repo} snapshot is missing`
    return {
      command: `echo '[janus] ${reason}' >&2; exit 78`,
      mtp: { ...mtp, lastError: reason }
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
  // 요청 간 재사용해 prefill을 줄인다. 설정 다이얼로그 또는 JANUS_APC=0으로 끈다.
  const apcEnv = apc ? 'APC_ENABLED=1 ' : ''
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
