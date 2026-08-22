import { Network } from 'lucide-react'
import { useStore } from '../store'

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="border-b border-border px-4 py-3">
      <div className="mb-2 text-[10px] font-semibold tracking-wider text-faint">{label}</div>
      {children}
    </div>
  )
}

const inputCls =
  'w-full rounded-md border border-border-strong bg-raised px-2.5 py-1.5 text-[12px] ' +
  'text-fg outline-none focus:border-accent'

/** 오케스트레이터 설정 — 노드 선택이 필요 없다. 에이전트 = 오케스트레이터 1개다. */
export default function Inspector() {
  const spec = useStore((s) => s.spec)
  const tools = useStore((s) => s.tools)
  const models = useStore((s) => s.models)
  const patchSpec = useStore((s) => s.patchSpec)

  if (!spec) {
    return (
      <div className="grid h-full place-items-center px-6 text-center text-[12px] text-faint">
        에이전트를 선택하면
        <br />
        오케스트레이터 설정이 여기 표시됩니다
      </div>
    )
  }

  const picked = spec.tools ?? []
  const risky = picked.filter((t) => tools.find((x) => x.name === t)?.needs_approval)
  const mustAsk = risky.length > 0

  const toggle = (name: string) => {
    const next = picked.includes(name) ? picked.filter((t) => t !== name) : [...picked, name]
    const nextRisky = next.some((t) => tools.find((x) => x.name === t)?.needs_approval)
    // 위험 도구를 켜면 승인도 같이 켠다 — 안 그러면 저장이 거부된다
    patchSpec({ tools: next, ...(nextRisky ? { approval: 'ask' as const } : {}) })
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="border-b border-border px-4 py-3">
        <div className="mb-2 text-[10px] font-semibold tracking-wider text-faint">ORCHESTRATOR</div>
        <div className="flex items-center gap-2">
          <Network size={14} className="shrink-0 text-accent-fg" />
          <input
            value={spec.name}
            onChange={(e) => patchSpec({ name: e.target.value })}
            className="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 font-medium outline-none hover:border-border-strong focus:border-accent"
          />
        </div>
        <p className="mt-2 text-[11px] leading-snug text-faint">
          워커는 실행 중 <code className="text-muted">create_worker</code>로 만들어지고
          캔버스에 나타납니다.
        </p>
      </div>

      <Section label="MODEL">
        <select
          value={spec.model ?? ''}
          onChange={(e) => patchSpec({ model: e.target.value })}
          className={inputCls}
        >
          {models.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name} ({m.provider})
            </option>
          ))}
        </select>
      </Section>

      <Section label="SYSTEM PROMPT">
        <textarea
          value={spec.system_prompt ?? ''}
          onChange={(e) => patchSpec({ system_prompt: e.target.value })}
          rows={7}
          className={inputCls + ' resize-y leading-relaxed'}
        />
      </Section>

      <Section label="TOOLS">
        <div className="space-y-0.5">
          {tools.map((t) => (
            <label
              key={t.name}
              title={t.description}
              className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 hover:bg-raised"
            >
              <input
                type="checkbox"
                checked={picked.includes(t.name)}
                onChange={() => toggle(t.name)}
                className="accent-[var(--color-accent)]"
              />
              <span className="font-mono text-[11.5px]">{t.name}</span>
              {t.needs_approval && (
                <span className="ml-auto text-[10px]" style={{ color: 'var(--color-warn)' }}>
                  승인 필요
                </span>
              )}
            </label>
          ))}
        </div>
        <p className="mt-1.5 text-[11px] leading-snug text-faint">
          워커는 이 목록의 부분집합만 받을 수 있습니다.
        </p>
      </Section>

      <Section label="APPROVAL">
        <div className="flex gap-1">
          {(['auto', 'ask'] as const).map((a) => (
            <button
              key={a}
              disabled={mustAsk && a === 'auto'}
              onClick={() => patchSpec({ approval: a })}
              title={mustAsk && a === 'auto' ? '위험 도구를 가진 에이전트는 auto를 쓸 수 없습니다' : ''}
              className="flex-1 rounded-md border px-2 py-1 text-[12px] disabled:opacity-35"
              style={{
                borderColor:
                  (spec.approval ?? 'auto') === a ? 'var(--color-accent)' : 'var(--color-border-strong)',
                color: (spec.approval ?? 'auto') === a ? 'var(--color-accent-fg)' : 'var(--color-muted)'
              }}
            >
              {a}
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-[11px] leading-snug text-faint">
          {mustAsk
            ? `${risky.join(', ')} 는 실행 전 확인을 받습니다. 끌 수 없습니다.`
            : '읽기 전용 도구만 있으므로 확인 없이 진행합니다.'}
        </p>
      </Section>

      <Section label="MAX STEPS">
        <input
          type="number"
          min={1}
          max={100}
          value={spec.max_steps ?? 15}
          onChange={(e) => patchSpec({ max_steps: Number(e.target.value) })}
          className={inputCls}
        />
        <p className="mt-1.5 text-[11px] leading-snug text-faint">
          한 턴에서 이 횟수만큼 돌고도 안 끝나면 중단합니다.
        </p>
      </Section>
    </div>
  )
}
