import type { ModelProfile } from './types'

/** 구독형 실행기가 고를 수 있는 모델·사고 강도.
 *
 *  effort 어휘는 CLI마다 다르고 서버도 provider별로만 받는다
 *  (cli_runner.CLI_EFFORTS) — 이 파일이 그 계약의 화면 쪽 절반이다.
 *  설정 화면과 채팅 컴포저가 같은 목록을 써야 두 곳이 어긋나지 않는다.
 */
export const SUBSCRIPTION_CHOICES = {
  claude_code: {
    models: [
      { value: '', label: '기본' },
      { value: 'fable', label: 'Fable' },
      { value: 'opus', label: 'Opus' },
      { value: 'sonnet', label: 'Sonnet' },
      { value: 'haiku', label: 'Haiku' }
    ],
    efforts: ['low', 'medium', 'high', 'xhigh', 'max']
  },
  codex: {
    models: [
      { value: '', label: '기본' },
      { value: 'gpt-5.6-sol', label: 'GPT-5.6 Sol' },
      { value: 'gpt-5.6-codex', label: 'GPT-5.6 Codex' },
      { value: 'gpt-5.6', label: 'GPT-5.6' }
    ],
    efforts: ['minimal', 'low', 'medium', 'high']
  }
} as const

export type SubscriptionProvider = keyof typeof SUBSCRIPTION_CHOICES

export function subscriptionChoices(provider: string | undefined) {
  return SUBSCRIPTION_CHOICES[provider as SubscriptionProvider] ?? null
}

/** config는 서버가 항상 채우지만, 낡은 스냅샷 하나로 화면이 죽으면 안 된다. */
export function modelSelection(profile: ModelProfile | null | undefined) {
  const config = profile?.config ?? {}
  return {
    model: String(config.model ?? ''),
    effort: String(config.effort ?? '')
  }
}

/** 목록에 없는 값(설정 파일로 직접 넣은 모델)도 현재 선택으로 보이게 한다. */
export function modelOptions(provider: string | undefined, current: string) {
  const choices = subscriptionChoices(provider)
  if (!choices) return []
  const options = choices.models.map((item) => ({ value: item.value, label: item.label }))
  return options.some((item) => item.value === current)
    ? options
    : [...options, { value: current, label: current }]
}
