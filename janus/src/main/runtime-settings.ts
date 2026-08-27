import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import type { MtpPolicy } from './model-runtime'

/** 앱 설정 다이얼로그가 관리하는 모델 런타임 손잡이.
 *  환경변수(JANUS_MTP_POLICY/JANUS_MODEL_SLOTS/JANUS_APC)가 있으면 그것이 이기고,
 *  없으면 이 파일 값, 그것도 없으면 기본값이다. */
export interface RuntimeSettings {
  /** 로컬 MLX 서버 자체를 띄울지 — 구독형 위주로 쓸 때 꺼서 메모리를 아낀다 */
  localServer: boolean
  mtpPolicy: MtpPolicy
  modelSlots: number
  apc: boolean
}

export const RUNTIME_SETTINGS_DEFAULTS: RuntimeSettings = {
  localServer: true,
  mtpPolicy: 'required',
  modelSlots: 3,
  apc: true
}

function clamp(settings: Partial<RuntimeSettings> | null | undefined): RuntimeSettings {
  const policy = settings?.mtpPolicy
  const slots = Number(settings?.modelSlots)
  return {
    localServer: settings?.localServer !== false,
    mtpPolicy: policy === 'preferred' || policy === 'off' ? policy : 'required',
    modelSlots: Number.isFinite(slots) ? Math.min(8, Math.max(1, Math.round(slots))) : RUNTIME_SETTINGS_DEFAULTS.modelSlots,
    apc: settings?.apc !== false
  }
}

export function loadRuntimeSettings(file: string): RuntimeSettings {
  try {
    return clamp(JSON.parse(readFileSync(file, 'utf-8')) as Partial<RuntimeSettings>)
  } catch {
    return { ...RUNTIME_SETTINGS_DEFAULTS }
  }
}

export function saveRuntimeSettings(file: string, settings: Partial<RuntimeSettings>): RuntimeSettings {
  const next = clamp(settings)
  mkdirSync(dirname(file), { recursive: true })
  writeFileSync(file, JSON.stringify(next, null, 2), { mode: 0o600 })
  return next
}
