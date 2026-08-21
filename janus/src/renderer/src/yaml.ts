import type { Spec } from './types'

/** 스펙을 YAML로 직렬화한다. 서버가 주는 형식과 같은 모양을 목표로 하는 표시 전용 함수다. */
function emit(v: unknown, indent = 0): string {
  const pad = '  '.repeat(indent)
  if (v === null || v === undefined) return 'null'
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (typeof v === 'string') {
    if (v.includes('\n')) {
      return '|-\n' + v.split('\n').map((l) => pad + '  ' + l).join('\n')
    }
    // 특수문자가 있으면 인용한다
    return /^[\w./#@-]+$/.test(v) ? v : `'${v.replace(/'/g, "''")}'`
  }
  if (Array.isArray(v)) {
    if (!v.length) return '[]'
    return v
      .map((item) =>
        typeof item === 'object' && item !== null
          ? `\n${pad}- ` + emit(item, indent + 1).replace(/^\n/, '').trimStart()
          : `\n${pad}- ${emit(item, indent)}`
      )
      .join('')
  }
  return Object.entries(v as Record<string, unknown>)
    .filter(([, val]) => val !== undefined)
    .map(([k, val]) => {
      const rendered = emit(val, indent + 1)
      const nested = typeof val === 'object' && val !== null
      return `\n${pad}${k}:` + (nested ? rendered : ` ${rendered}`)
    })
    .join('')
}

export function toYaml(spec: Spec): string {
  return emit(spec, 0).replace(/^\n/, '') + '\n'
}
