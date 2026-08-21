import { useEffect, useState } from 'react'
import { Box, Info, Trash2, Waypoints } from 'lucide-react'
import { useStore } from '../store'
import type { SpecNode } from '../types'

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

/** 엣지를 고를 때 뜨는 패널 — 분기 조건이 여기서 정해진다. */
function EdgeInspector({ idx }: { idx: number }) {
  const spec = useStore((s) => s.spec)
  const patchEdge = useStore((s) => s.patchEdge)
  const deleteEdge = useStore((s) => s.deleteEdge)
  const edge = spec?.edges[idx]
  const [draft, setDraft] = useState(edge?.when ?? '')

  useEffect(() => setDraft(edge?.when ?? ''), [idx, edge?.when])
  if (!edge) return null

  const conditional = edge.when !== undefined

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="border-b border-border px-4 py-3">
        <div className="mb-2 text-[10px] font-semibold tracking-wider text-faint">EDGE</div>
        <div className="flex items-center gap-2">
          <Waypoints size={14} className="text-accent-fg" />
          <span className="truncate font-mono text-[12px]">
            {edge.from} → {edge.to}
          </span>
          <button
            onClick={() => deleteEdge(idx)}
            title="엣지 삭제"
            className="ml-auto rounded p-1 text-muted hover:text-danger"
          >
            <Trash2 size={13} />
          </button>
        </div>
      </div>

      <Section label="CONDITION">
        <label className="mb-2 flex items-center gap-2 text-[12px] text-muted">
          <input
            type="checkbox"
            checked={conditional}
            onChange={(e) => patchEdge(idx, { when: e.target.checked ? 'default' : null })}
            className="accent-[var(--color-accent)]"
          />
          조건부 엣지
        </label>
        {conditional && (
          <>
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={() => patchEdge(idx, { when: draft })}
              onKeyDown={(e) => e.key === 'Enter' && patchEdge(idx, { when: draft })}
              placeholder="intent == 'order_status'"
              className={inputCls + ' font-mono text-[11px]'}
            />
            <p className="mt-1.5 text-[11px] leading-snug text-faint">
              출발 노드의 출력 필드를 비교합니다. <code>field == 'value'</code>,{' '}
              <code>field != 'value'</code>, <code>default</code>만 됩니다.
            </p>
          </>
        )}
        {!conditional && (
          <p className="text-[11px] leading-snug text-faint">
            조건 없이 항상 진행합니다. 한 노드의 나가는 엣지는 전부 조건부거나 전부 무조건이어야
            합니다.
          </p>
        )}
      </Section>
    </div>
  )
}

/** agent 노드 전용 — 도구 선택과 승인 정책이 여기서 정해진다. */
function AgentConfig({ node }: { node: SpecNode }) {
  const tools = useStore((s) => s.tools)
  const patchNode = useStore((s) => s.patchNode)
  const picked = node.tools ?? []
  const risky = picked.filter((t) => tools.find((x) => x.name === t)?.needs_approval)
  const mustAsk = risky.length > 0

  const toggle = (name: string) => {
    const next = picked.includes(name) ? picked.filter((t) => t !== name) : [...picked, name]
    const nextRisky = next.some((t) => tools.find((x) => x.name === t)?.needs_approval)
    // 위험 도구를 켜면 승인도 같이 켠다 — 안 그러면 저장이 거부된다
    patchNode(node.id, { tools: next, ...(nextRisky ? { approval: 'ask' } : {}) })
  }

  return (
    <>
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
      </Section>

      <Section label="APPROVAL">
        <div className="flex gap-1">
          {(['auto', 'ask'] as const).map((a) => (
            <button
              key={a}
              disabled={mustAsk && a === 'auto'}
              onClick={() => patchNode(node.id, { approval: a })}
              title={mustAsk && a === 'auto' ? '위험 도구를 가진 에이전트는 auto를 쓸 수 없습니다' : ''}
              className="flex-1 rounded-md border px-2 py-1 text-[12px] disabled:opacity-35"
              style={{
                borderColor:
                  (node.approval ?? 'auto') === a ? 'var(--color-accent)' : 'var(--color-border-strong)',
                color: (node.approval ?? 'auto') === a ? 'var(--color-accent-fg)' : 'var(--color-muted)'
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
          value={node.max_steps ?? 10}
          onChange={(e) => patchNode(node.id, { max_steps: Number(e.target.value) })}
          className={inputCls}
        />
        <p className="mt-1.5 text-[11px] leading-snug text-faint">
          이 횟수만큼 돌고도 안 끝나면 중단합니다.
        </p>
      </Section>
    </>
  )
}

export default function Inspector() {
  const spec = useStore((s) => s.spec)
  const id = useStore((s) => s.selectedNodeId)
  const edgeIdx = useStore((s) => s.selectedEdgeIdx)
  const patchNode = useStore((s) => s.patchNode)
  const renameNode = useStore((s) => s.renameNode)
  const deleteNode = useStore((s) => s.deleteNode)
  const tools = useStore((s) => s.tools)
  const models = useStore((s) => s.models)
  const [tab, setTab] = useState<'config' | 'test'>('config')
  const [nameDraft, setNameDraft] = useState('')
  const [nameErr, setNameErr] = useState<string | null>(null)

  const node = spec?.nodes.find((n) => n.id === id)
  useEffect(() => {
    setNameDraft(node?.id ?? '')
    setNameErr(null)
  }, [node?.id])

  if (edgeIdx !== null) return <EdgeInspector idx={edgeIdx} />

  if (!node) {
    return (
      <div className="grid h-full place-items-center px-6 text-center text-[12px] text-faint">
        노드나 엣지를 선택하면
        <br />
        설정이 여기 표시됩니다
      </div>
    )
  }

  const commitRename = () => {
    if (nameDraft === node.id) return
    setNameErr(renameNode(node.id, nameDraft))
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="border-b border-border px-4 py-3">
        <div className="mb-2 text-[10px] font-semibold tracking-wider text-faint">NODE</div>
        <div className="flex items-center gap-2">
          <Box size={14} className="shrink-0 text-accent-fg" />
          <input
            value={nameDraft}
            onChange={(e) => setNameDraft(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => e.key === 'Enter' && commitRename()}
            disabled={node.type === 'start'}
            className="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 font-medium outline-none hover:border-border-strong focus:border-accent disabled:opacity-60"
          />
          <span className="shrink-0 rounded bg-raised px-1.5 py-0.5 text-[10px] text-muted">
            {node.type}
          </span>
          {node.type !== 'start' && (
            <button
              onClick={() => deleteNode(node.id)}
              title="노드 삭제 (연결된 엣지도 함께)"
              className="shrink-0 rounded p-1 text-muted hover:text-danger"
            >
              <Trash2 size={13} />
            </button>
          )}
        </div>
        {nameErr && <p className="mt-1 text-[11px] text-danger">{nameErr}</p>}

        <div className="mt-3 flex gap-1">
          {(['config', 'test'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className="rounded-md px-2.5 py-1 text-[12px] capitalize"
              style={{
                background: tab === t ? 'var(--color-accent-soft)' : 'transparent',
                color: tab === t ? 'var(--color-accent-fg)' : 'var(--color-muted)'
              }}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {tab === 'test' ? (
        <div className="flex items-start gap-2 px-4 py-4 text-[12px] text-faint">
          <Info size={13} className="mt-0.5 shrink-0" />
          <span>
            노드 단독 테스트는 아직 없습니다. 지금은 상단 <b className="text-muted">Run</b>으로 그래프
            전체를 실행하고 아래 Traces에서 이 노드의 입출력을 확인하세요.
          </span>
        </div>
      ) : (
        <>
          {(node.type === 'llm' || node.type === 'agent') && (
            <>
              <Section label="MODEL">
                <select
                  value={node.model?.name ?? ''}
                  onChange={(e) => patchNode(node.id, { model: { name: e.target.value } })}
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
                  value={node.system_prompt ?? ''}
                  onChange={(e) => patchNode(node.id, { system_prompt: e.target.value })}
                  rows={5}
                  className={inputCls + ' resize-y leading-relaxed'}
                />
              </Section>

              {node.type === 'llm' && <Section label="PROMPT (선택)">
                <input
                  value={node.prompt ?? ''}
                  onChange={(e) => patchNode(node.id, { prompt: e.target.value || undefined })}
                  placeholder="{{ start.user_message }}"
                  className={inputCls + ' font-mono text-[11px]'}
                />
                <p className="mt-1.5 text-[11px] leading-snug text-faint">
                  비우면 아래 INPUTS가 <code>키: 값</code> 줄로 전달됩니다.
                </p>
              </Section>}
            </>
          )}

          {node.type === 'agent' && <AgentConfig node={node} />}

          {node.type === 'tool' && (
            <Section label="TOOL">
              <select
                value={node.tool ?? ''}
                onChange={(e) => patchNode(node.id, { tool: e.target.value })}
                className={inputCls}
              >
                {tools.map((t) => (
                  <option key={t.name} value={t.name}>
                    {t.name}
                  </option>
                ))}
              </select>
              <p className="mt-1.5 text-[11px] leading-snug text-faint">
                {tools.find((t) => t.name === node.tool)?.description}
              </p>
              <p className="mt-1 text-[11px] text-faint">
                인자: {Object.keys(tools.find((t) => t.name === node.tool)?.params ?? {}).join(', ')}
              </p>
            </Section>
          )}

          {(node.type === 'llm' || node.type === 'tool' || node.type === 'end') && (
            <Section label="INPUTS">
              <div className="space-y-1.5">
                {Object.entries(node.inputs ?? {}).map(([k, v]) => (
                  <div key={k} className="flex items-center gap-1.5">
                    <span className="w-24 shrink-0 truncate rounded bg-raised px-1.5 py-1 font-mono text-[11px] text-accent-fg">
                      {k}
                    </span>
                    <input
                      value={v}
                      onChange={(e) =>
                        patchNode(node.id, { inputs: { ...node.inputs, [k]: e.target.value } })
                      }
                      className={inputCls + ' font-mono text-[11px]'}
                    />
                    <button
                      onClick={() => {
                        const next = { ...node.inputs }
                        delete next[k]
                        patchNode(node.id, { inputs: next })
                      }}
                      className="shrink-0 rounded p-1 text-faint hover:text-danger"
                    >
                      <Trash2 size={11} />
                    </button>
                  </div>
                ))}
                <button
                  onClick={() => {
                    let name = 'input'
                    let i = 2
                    while (name in (node.inputs ?? {})) name = `input${i++}`
                    patchNode(node.id, { inputs: { ...node.inputs, [name]: '' } })
                  }}
                  className="w-full rounded-md border border-dashed border-border-strong py-1 text-[11px] text-muted hover:text-fg"
                >
                  + Add Input
                </button>
              </div>
            </Section>
          )}

          {node.type === 'start' && (
            <Section label="OUTPUTS">
              <div className="space-y-1.5">
                {(node.outputs ?? []).map((o, i) => (
                  <div key={i} className="flex items-center gap-1.5">
                    <input
                      value={o}
                      onChange={(e) => {
                        const next = [...(node.outputs ?? [])]
                        next[i] = e.target.value
                        patchNode(node.id, { outputs: next })
                      }}
                      className={inputCls + ' font-mono text-[11px]'}
                    />
                    <button
                      onClick={() =>
                        patchNode(node.id, { outputs: (node.outputs ?? []).filter((_, j) => j !== i) })
                      }
                      className="shrink-0 rounded p-1 text-faint hover:text-danger"
                    >
                      <Trash2 size={11} />
                    </button>
                  </div>
                ))}
                <button
                  onClick={() =>
                    patchNode(node.id, { outputs: [...(node.outputs ?? []), 'field'] })
                  }
                  className="w-full rounded-md border border-dashed border-border-strong py-1 text-[11px] text-muted hover:text-fg"
                >
                  + Add Output
                </button>
              </div>
            </Section>
          )}

          {node.output && (
            <Section label="OUTPUT">
              <div className="flex items-center gap-2">
                <input
                  value={node.output.name}
                  onChange={(e) =>
                    patchNode(node.id, { output: { ...node.output, name: e.target.value } })
                  }
                  className={inputCls + ' font-mono text-[11px]'}
                />
                <span className="shrink-0 text-[11px] text-faint">
                  {node.output.type ?? 'string'}
                </span>
              </div>
            </Section>
          )}

          {node.type === 'llm' && (
            <Section label="OPTIONS">
              <label className="mb-3 block">
                <div className="mb-1 flex justify-between text-[11px] text-muted">
                  <span>Temperature</span>
                  <span className="font-mono text-fg">{node.model?.temperature ?? 0}</span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={2}
                  step={0.1}
                  value={node.model?.temperature ?? 0}
                  onChange={(e) =>
                    patchNode(node.id, { model: { temperature: Number(e.target.value) } })
                  }
                  className="w-full accent-[var(--color-accent)]"
                />
              </label>
              <label className="block">
                <div className="mb-1 text-[11px] text-muted">Max Tokens</div>
                <input
                  type="number"
                  min={1}
                  value={node.model?.max_tokens ?? 200}
                  onChange={(e) =>
                    patchNode(node.id, { model: { max_tokens: Number(e.target.value) } })
                  }
                  className={inputCls}
                />
              </label>
            </Section>
          )}
        </>
      )}
    </div>
  )
}
