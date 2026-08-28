import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { Download, Play, Square } from 'lucide-react'
import { JANUS_BASE, apiFetch, errorMessage, janusApi } from '../api'
import { useDomainEvent } from '../domainEvents'
import { useAgentProfileOptions, useStore } from '../store'
import { Button, Dialog, EmptyState, Field, Input, Listbox, SegmentedControl, Status, StatusTone } from './ui'

interface EvaluationRun {
  acceptance_passed: boolean
  wall_time_ms: number
  user_inputs: number
  approval_requests: number
}

interface Experiment {
  id: string
  role: 'baseline' | 'candidate'
  label: string
  source: string
  status: string
  created_at: string
  error: string | null
  report: { runs: EvaluationRun[] } | null
  config: { tasks: string[]; repeats: number } | null
}

interface Comparison {
  id: string
  baseline_experiment_id: string
  candidate_experiment_id: string
  created_at: string
  result: {
    verdict: string
    regressions: { scope: string; metric: string; delta: number }[]
    improvements: { scope: string; metric: string; delta: number }[]
  }
}

const STATUS_META: Record<string, { tone: StatusTone; label: string; pulse?: boolean }> = {
  queued: { tone: 'warning', label: '대기' },
  running: { tone: 'success', label: '실행 중', pulse: true },
  completed: { tone: 'success', label: '완료' },
  failed: { tone: 'danger', label: '실패' },
  cancelled: { tone: 'muted', label: '취소됨' }
}

const VERDICT_META: Record<string, { tone: StatusTone; label: string }> = {
  regression: { tone: 'danger', label: '회귀' },
  improvement: { tone: 'success', label: '개선' },
  neutral: { tone: 'muted', label: '변화 없음' },
  incomparable_conditions: { tone: 'warning', label: '조건 불일치' }
}

function summarize(report: Experiment['report']) {
  const runs = report?.runs ?? []
  if (runs.length === 0) return null
  const successes = runs.filter((run) => run.acceptance_passed).length
  const wallMean = runs.reduce((total, run) => total + run.wall_time_ms, 0) / runs.length
  const interventions = runs.reduce(
    (total, run) => total + run.user_inputs + run.approval_requests, 0
  ) / runs.length
  return {
    runs: runs.length,
    successText: `${successes}/${runs.length}`,
    wallText: `${(wallMean / 1000).toFixed(1)}s`,
    interventionText: interventions.toFixed(1)
  }
}

async function downloadComparison(comparisonId: string, format: 'json' | 'csv' | 'markdown') {
  const response = await apiFetch(
    `${JANUS_BASE}/evaluations/comparisons/${comparisonId}/export?format=${format}`
  )
  if (!response.ok) throw new Error(`내보내기 실패 (${response.status})`)
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `evaluation-${comparisonId}.${format === 'markdown' ? 'md' : format}`
  anchor.click()
  URL.revokeObjectURL(url)
}

export default function EvaluationLab() {
  const profiles = useStore((state) => state.agentProfiles)
  const profileOptions = useAgentProfileOptions()
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [comparisons, setComparisons] = useState<Comparison[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [runOpen, setRunOpen] = useState(false)
  const [role, setRole] = useState<'baseline' | 'candidate'>('candidate')
  const [label, setLabel] = useState('')
  const [profileId, setProfileId] = useState('')
  const [repeats, setRepeats] = useState('')
  const [baselineId, setBaselineId] = useState('')
  const [candidateId, setCandidateId] = useState('')

  const load = useCallback(async () => {
    try {
      const [experimentItems, comparisonItems] = await Promise.all([
        janusApi<Experiment[]>('/evaluations/experiments'),
        janusApi<Comparison[]>('/evaluations/comparisons')
      ])
      setExperiments(experimentItems)
      setComparisons(comparisonItems)
      setError(null)
    } catch (cause) {
      setError(`평가 목록을 불러오지 못했습니다 · ${errorMessage(cause)}`)
    }
  }, [])

  useEffect(() => { void load() }, [load])
  useDomainEvent('evaluation', () => { void load() })

  const completed = useMemo(
    () => experiments.filter((item) => item.status === 'completed'),
    [experiments]
  )
  const baselines = completed.filter((item) => item.role === 'baseline')
  const candidates = completed.filter((item) => item.role === 'candidate')

  const runExperiment = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    try {
      await janusApi('/evaluations/experiments/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          role,
          label: label.trim(),
          agent_profile_id: profileId || profiles[0]?.id,
          ...(repeats.trim() ? { repeats: Number(repeats) } : {})
        })
      })
      setRunOpen(false)
      setLabel('')
      await load()
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy(false)
    }
  }

  const cancelExperiment = async (experimentId: string) => {
    setBusy(true)
    try {
      await janusApi(`/evaluations/experiments/${experimentId}/cancel`, { method: 'POST' })
      await load()
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy(false)
    }
  }

  const compare = async () => {
    setBusy(true)
    try {
      await janusApi('/evaluations/comparisons', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ baseline_id: baselineId, candidate_id: candidateId })
      })
      await load()
    } catch (cause) {
      setError(errorMessage(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="eval-lab">
      <div className="eval-lab__toolbar">
        <h2>Evaluation Lab</h2>
        <span className="eval-lab__hint">지침·프로필 변경이 성공률을 올렸는지 실측으로 판정합니다.</span>
        <Button variant="primary" onClick={() => setRunOpen(true)}>
          <Play size={12} /> 새 실험
        </Button>
      </div>

      {error && <p className="eval-lab__error">{error}</p>}

      {experiments.length === 0 ? (
        <EmptyState
          title="실험이 아직 없습니다"
          description="현재 프로필로 baseline을 먼저 실행하고, 지침을 바꾼 뒤 candidate를 실행해 비교하세요."
          action={<Button variant="primary" onClick={() => setRunOpen(true)}><Play size={12} /> 새 실험</Button>}
        />
      ) : (
        <div className="eval-lab__body">
          <table className="eval-table">
            <thead>
              <tr>
                <th>역할</th><th>라벨</th><th>상태</th><th>성공</th><th>평균 시간</th><th>개입</th><th></th>
              </tr>
            </thead>
            <tbody>
              {experiments.map((item) => {
                const meta = STATUS_META[item.status] ?? { tone: 'muted' as StatusTone, label: item.status }
                const summary = summarize(item.report)
                return (
                  <tr key={item.id}>
                    <td className="font-mono">{item.role}</td>
                    <td>{item.label}</td>
                    <td>
                      <Status tone={meta.tone} pulse={meta.pulse} title={item.error ?? undefined}>
                        {meta.label}
                      </Status>
                    </td>
                    <td className="font-mono">{summary?.successText ?? '—'}</td>
                    <td className="font-mono">{summary?.wallText ?? '—'}</td>
                    <td className="font-mono">{summary?.interventionText ?? '—'}</td>
                    <td>
                      {(item.status === 'queued' || item.status === 'running') && (
                        <button
                          type="button"
                          onClick={() => void cancelExperiment(item.id)}
                          disabled={busy}
                          className="task-quiet-action"
                        >
                          <Square size={10} /> 취소
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <div className="eval-compare">
            <h3>비교</h3>
            <div className="eval-compare__controls">
              <Listbox
                label="baseline 선택"
                placeholder="baseline 선택"
                value={baselineId}
                options={baselines.map((item) => ({ value: item.id, label: item.label }))}
                onChange={setBaselineId}
              />
              <Listbox
                label="candidate 선택"
                placeholder="candidate 선택"
                value={candidateId}
                options={candidates.map((item) => ({ value: item.id, label: item.label }))}
                onChange={setCandidateId}
              />
              <Button variant="secondary" disabled={!baselineId || !candidateId || busy} onClick={() => void compare()}>
                비교 실행
              </Button>
            </div>
            {comparisons.length === 0 ? (
              <p className="eval-compare__empty">완료된 baseline과 candidate를 골라 회귀 여부를 판정하세요.</p>
            ) : (
              comparisons.slice(0, 5).map((item) => {
                const verdict = VERDICT_META[item.result.verdict]
                  ?? { tone: 'muted' as StatusTone, label: item.result.verdict }
                const baseline = experiments.find((exp) => exp.id === item.baseline_experiment_id)
                const candidate = experiments.find((exp) => exp.id === item.candidate_experiment_id)
                return (
                  <div key={item.id} className="eval-comparison">
                    <div className="eval-comparison__head">
                      <Status tone={verdict.tone}>{verdict.label}</Status>
                      <span>{baseline?.label ?? '?'} → {candidate?.label ?? '?'}</span>
                      <span className="eval-comparison__exports">
                        {(['json', 'csv', 'markdown'] as const).map((format) => (
                          <button
                            key={format}
                            type="button"
                            className="task-quiet-action"
                            onClick={() => void downloadComparison(item.id, format).catch(
                              (cause) => setError(errorMessage(cause))
                            )}
                          >
                            <Download size={10} /> {format}
                          </button>
                        ))}
                      </span>
                    </div>
                    {item.result.regressions.length > 0 && (
                      <ul className="eval-comparison__rows" data-kind="regression">
                        {item.result.regressions.map((row, index) => (
                          <li key={`${item.id}-r-${index}`} className="font-mono">
                            × {row.scope} · {row.metric} · {row.delta > 0 ? '+' : ''}{row.delta}
                          </li>
                        ))}
                      </ul>
                    )}
                    {item.result.improvements.length > 0 && (
                      <ul className="eval-comparison__rows" data-kind="improvement">
                        {item.result.improvements.map((row, index) => (
                          <li key={`${item.id}-i-${index}`} className="font-mono">
                            ✓ {row.scope} · {row.metric} · {row.delta}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </div>
      )}

      <Dialog open={runOpen} title="새 실험 실행" onClose={() => setRunOpen(false)}>
        <form onSubmit={runExperiment} className="eval-run-form">
          <Field label="역할" help="baseline은 현재 상태의 기준, candidate는 바꾼 뒤의 도전자입니다.">
            <SegmentedControl
              items={[
                { value: 'baseline', label: 'baseline' },
                { value: 'candidate', label: 'candidate' }
              ] as const}
              value={role}
              onChange={setRole}
              label="실험 역할"
            />
          </Field>
          <Field label="라벨">
            <Input
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="예: prompt-v2"
              required
            />
          </Field>
          <Field label="에이전트 프로필">
            <Listbox
              label="에이전트 프로필"
              value={profileId || profiles[0]?.id || ''}
              options={profileOptions}
              onChange={setProfileId}
            />
          </Field>
          <Field label="반복 횟수" help="비워두면 TaskSuite 기본값을 사용합니다.">
            <Input
              type="number"
              min={1}
              max={20}
              value={repeats}
              onChange={(event) => setRepeats(event.target.value)}
              placeholder="기본값"
            />
          </Field>
          <div className="eval-run-form__actions">
            <Button variant="ghost" type="button" onClick={() => setRunOpen(false)}>닫기</Button>
            <Button variant="primary" type="submit" disabled={busy || !label.trim()}>
              <Play size={12} /> 실행
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  )
}
