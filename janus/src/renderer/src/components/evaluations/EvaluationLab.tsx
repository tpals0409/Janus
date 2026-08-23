import { ChangeEvent, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle, Check, CheckCircle2, Download, FlaskConical, Loader2, Play, Square, Upload
} from 'lucide-react'
import { useStore } from '../../store'
import { useDomainEvent } from '../../domainEvents'
import type { EvaluationExperiment } from '../../types'
import { Button, EmptyState, Field, Input, Select, Status } from '../ui'

const TASKS = [
  ['single_file_bug', '단일 파일 버그'],
  ['multi_file_refactor', '다중 파일 리팩토링'],
  ['investigate_code_tests', '코드와 테스트 조사']
] as const

const STATUS_LABEL: Record<string, string> = {
  queued: '대기 중', running: '실행 중', completed: '완료', failed: '실패', cancelled: '취소됨'
}
const VERDICT_LABEL: Record<string, string> = {
  improved: '개선', equivalent: '동등', regression: '회귀', incomparable_conditions: '조건 비교 불가'
}

function ExperimentLane({ role }: { role: 'baseline' | 'candidate' }) {
  const profiles = useStore((state) => state.agentProfiles)
  const experiments = useStore((state) => state.evaluationExperiments)
  const busy = useStore((state) => state.evaluationBusy)
  const start = useStore((state) => state.startEvaluation)
  const cancel = useStore((state) => state.cancelEvaluation)
  const importReport = useStore((state) => state.importEvaluation)
  const [profileId, setProfileId] = useState('agent_default')
  const [label, setLabel] = useState(role === 'baseline' ? '기준' : '후보')
  const [repeats, setRepeats] = useState(5)
  const [tasks, setTasks] = useState<string[]>(TASKS.map(([id]) => id))
  const latest = experiments.find((item) => item.role === role)

  const importFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    await importReport(role, JSON.parse(await file.text()) as Record<string, unknown>)
  }

  return (
    <section className="border border-border-subtle bg-panel p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-mono text-[9px] uppercase tracking-[0.18em] text-faint">{role === 'baseline' ? '기준' : '후보'}</div>
          <h2 className="task-title mt-1 text-[16px] font-semibold">
            {role === 'baseline' ? '대조 프로필' : '후보 프로필'}
          </h2>
        </div>
        {latest && <ExperimentStatus item={latest} onCancel={() => void cancel(latest.id)} />}
      </div>
      <div className="mt-4 grid grid-cols-[1fr_110px] gap-2">
        <Field label="에이전트 프로필">
          <Select
            value={profileId} onChange={(event) => setProfileId(event.target.value)}
          >
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name} · {profile.worker_policy}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="반복 횟수">
          <Input
            type="number" min={1} max={20} value={repeats}
            onChange={(event) => setRepeats(Number(event.target.value))}
            className="font-mono"
          />
        </Field>
      </div>
      <Field label="실험 이름" className="mt-3">
        <Input value={label} onChange={(event) => setLabel(event.target.value)} />
      </Field>
      <div className="mt-3">
        <span className="task-label">작업 모양</span>
        <div className="mt-1.5 space-y-1">
          {TASKS.map(([id, name]) => (
            <label key={id} className="ui-checkbox-row">
              <input
                type="checkbox" checked={tasks.includes(id)}
                className="ui-checkbox"
                onChange={(event) => setTasks(
                  event.target.checked ? [...tasks, id] : tasks.filter((item) => item !== id)
                )}
              />
              <span className="flex-1">{name}</span><code className="text-[9px] text-faint">{id}</code>
            </label>
          ))}
        </div>
      </div>
      <div className="mt-4 flex items-center gap-2 border-t border-border pt-3">
        <Button
          disabled={busy || !label.trim() || tasks.length === 0}
          onClick={() => void start({
            role, label: label.trim(), agent_profile_id: profileId,
            repeats, tasks, turn_timeout_seconds: 180
          })}
        >
          <Play size={11} /> {role === 'baseline' ? '기준' : '후보'} 실행
        </Button>
        <label className="ui-button ui-button--secondary ui-button--compact cursor-pointer">
          <Upload size={11} /> 결과 가져오기
          <input type="file" accept="application/json,.json" onChange={importFile} className="hidden" />
        </label>
        <span className="ml-auto text-[9px] text-faint">Qwen 27B · 로컬 전용</span>
      </div>
    </section>
  )
}

function ExperimentStatus({ item, onCancel }: { item: EvaluationExperiment; onCancel: () => void }) {
  const active = item.status === 'queued' || item.status === 'running'
  const tone = item.status === 'completed' ? 'success' : active ? 'warning' : 'danger'
  return (
    <div className="text-right">
      <Status tone={tone} pulse={active}>{active && <Loader2 size={10} className="animate-spin" />}{STATUS_LABEL[item.status] ?? item.status}</Status>
      <div className="mt-1 max-w-[170px] truncate font-mono text-[8.5px] text-faint">{item.label}</div>
      {active && (
        <button onClick={onCancel} className="mt-1 inline-flex items-center gap-1 text-[9px] text-danger">
          <Square size={8} /> 취소
        </button>
      )}
    </div>
  )
}

function ComparisonLedger() {
  const experiments = useStore((state) => state.evaluationExperiments)
  const comparisons = useStore((state) => state.evaluationComparisons)
  const compare = useStore((state) => state.compareEvaluations)
  const exportResult = useStore((state) => state.exportEvaluation)
  const promote = useStore((state) => state.promoteEvaluation)
  const projectId = useStore((state) => state.projectId)
  const project = useStore((state) => state.projects.find((item) => item.id === state.projectId))
  const busy = useStore((state) => state.evaluationBusy)
  const baselines = experiments.filter((item) => item.role === 'baseline' && item.status === 'completed')
  const candidates = experiments.filter((item) => item.role === 'candidate' && item.status === 'completed')
  const [baselineId, setBaselineId] = useState('')
  const [candidateId, setCandidateId] = useState('')
  const [wallThreshold, setWallThreshold] = useState(15)
  const [tokenThreshold, setTokenThreshold] = useState(10)
  const latest = comparisons[0]

  useEffect(() => {
    if (!baselineId && baselines[0]) setBaselineId(baselines[0].id)
    if (!candidateId && candidates[0]) setCandidateId(candidates[0].id)
  }, [baselines, candidates, baselineId, candidateId])

  const verdict = latest?.result.verdict
  const verdictColor = verdict === 'improved' ? 'var(--color-ok)'
    : verdict === 'regression' || verdict === 'incomparable_conditions'
      ? 'var(--color-danger)' : 'var(--color-warn)'
  return (
    <section className="mt-4 border border-border-subtle bg-panel">
      <div className="grid grid-cols-[minmax(0,1fr)_180px_minmax(0,1fr)] items-end gap-3 border-b border-border p-4">
        <label>
          <span className="task-label">기준 결과</span>
          <select value={baselineId} onChange={(event) => setBaselineId(event.target.value)} className="task-input mt-1">
            <option value="">기준 선택</option>
            {baselines.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
        <div className="text-center">
          <div className="font-mono text-[9px] text-faint">회귀 판정</div>
          <Button
            disabled={busy || !baselineId || !candidateId}
            onClick={() => void compare({
              baseline_id: baselineId, candidate_id: candidateId,
              thresholds: {
                max_success_rate_drop_pp: 0,
                max_wall_regression_pct: wallThreshold,
                max_token_regression_pct: tokenThreshold,
                max_intervention_increase: 0,
                min_improvement_pct: 5
              }
            })}
            className="mx-auto mt-1"
          >
            A/B 비교
          </Button>
        </div>
        <label>
          <span className="task-label">후보 결과</span>
          <select value={candidateId} onChange={(event) => setCandidateId(event.target.value)} className="task-input mt-1">
            <option value="">후보 선택</option>
            {candidates.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
      </div>
      <div className="flex items-center gap-4 border-b border-border px-4 py-2 text-[9.5px] text-faint">
        <label>소요 시간 회귀 ≤ <input type="number" value={wallThreshold} onChange={(e) => setWallThreshold(Number(e.target.value))} className="mx-1 w-14 rounded border border-border bg-raised px-1 py-0.5 font-mono" />%</label>
        <label>토큰 회귀 ≤ <input type="number" value={tokenThreshold} onChange={(e) => setTokenThreshold(Number(e.target.value))} className="mx-1 w-14 rounded border border-border bg-raised px-1 py-0.5 font-mono" />%</label>
        <span>수용률 하락: 0%p · 개입 증가: 0</span>
      </div>
      {latest ? (
        <div className="p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="font-mono text-[9px] uppercase tracking-[0.18em] text-faint">최신 판정</div>
              <div className="task-title mt-1 text-[20px] font-semibold uppercase" style={{ color: verdictColor }}>
                {verdict ? VERDICT_LABEL[verdict] ?? verdict : '—'}
              </div>
            </div>
            <div className="flex gap-1">
              {(verdict === 'improved' || verdict === 'equivalent') && (
                <button
                  disabled={busy || !projectId}
                  onClick={() => void promote(latest.id)}
                  className="task-quiet-action"
                  title={projectId ? '측정한 후보를 새 작업의 기본값으로 사용' : '먼저 프로젝트를 선택하세요'}
                >
                  <CheckCircle2 size={10} />
                  {project?.promoted_comparison_id === latest.id ? '프로젝트 기본값' : '기본값으로 승격'}
                </button>
              )}
              {(['json', 'csv', 'markdown'] as const).map((format) => (
                <button key={format} onClick={() => void exportResult(latest.id, format)} className="task-quiet-action uppercase">
                  <Download size={10} /> {format === 'markdown' ? 'md' : format}
                </button>
              ))}
            </div>
          </div>
          {latest.result.condition_mismatches.length > 0 && (
            <div className="error-strip mt-3">
              <AlertTriangle size={11} className="mr-1 inline" />
              {latest.result.condition_mismatches.map((item) => item.field).join(', ')} 조건이 달라 비용을 비교할 수 없습니다.
            </div>
          )}
          <div className="mt-4 overflow-hidden border border-border">
            <div className="grid grid-cols-[1.4fr_repeat(4,1fr)] bg-raised px-3 py-2 font-mono text-[8.5px] uppercase tracking-wider text-faint">
              <span>작업</span><span>성공</span><span>평균 시간 ± σ</span><span>토큰 Δ</span><span>개입 Δ</span>
            </div>
            {latest.result.rows.map((row) => (
              <div key={row.task_id} className="grid grid-cols-[1.4fr_repeat(4,1fr)] border-t border-border px-3 py-2.5 text-[10px]">
                <code className="text-muted">{row.task_id}</code>
                <span>{row.baseline.successes}/{row.baseline.runs} → {row.candidate.successes}/{row.candidate.runs}</span>
                <span>{(row.candidate.wall_mean_ms / 1000).toFixed(1)}s ± {(row.candidate.wall_stdev_ms / 1000).toFixed(1)}</span>
                <span>{row.token_delta_pct == null ? '—' : `${row.token_delta_pct > 0 ? '+' : ''}${row.token_delta_pct.toFixed(1)}%`}</span>
                <span>{row.intervention_delta > 0 ? '+' : ''}{row.intervention_delta.toFixed(1)}</span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <EmptyState title="비교 결과 없음" description="기준과 후보를 실행하거나 가져온 뒤 같은 하드웨어 조건에서 비교하세요." />
      )}
    </section>
  )
}

export default function EvaluationLab() {
  const load = useStore((state) => state.loadEvaluations)
  const experiments = useStore((state) => state.evaluationExperiments)
  const error = useStore((state) => state.evaluationError)
  const active = useMemo(
    () => experiments.some((item) => item.status === 'queued' || item.status === 'running'),
    [experiments]
  )
  useEffect(() => { void load() }, [load])
  useDomainEvent('evaluation', () => void load())

  return (
    <main className="workspace-surface min-w-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-[1180px] px-5 py-5">
        <div className="mb-4 flex items-end justify-between gap-5">
          <div>
            <div className="flex items-center gap-2 font-mono text-[9px] uppercase tracking-[0.2em] text-muted">
              <FlaskConical size={12} /> 평가 실험실
            </div>
            <h1 className="task-title mt-2 text-[20px] font-semibold tracking-[-0.01em]">정책 변경을 증명하세요.</h1>
            <p className="mt-2 max-w-[700px] text-[11px] leading-relaxed text-faint">
              변경할 수 없는 두 프로필 스냅샷으로 같은 고정 작업을 실행합니다. 수용 여부를 먼저 판정하고 시간·토큰·분산·사람의 개입으로 우승 후보를 결정합니다.
            </p>
          </div>
          <div className="text-right font-mono text-[9px] text-faint">
            실험 {experiments.length}개<br />{active ? '실행기 작동 중' : '실행기 대기 중'}
          </div>
        </div>
        {error && <div className="error-strip mb-4">{error}</div>}
        <div className="grid grid-cols-2 gap-4">
          <ExperimentLane role="baseline" />
          <ExperimentLane role="candidate" />
        </div>
        <ComparisonLedger />
      </div>
    </main>
  )
}
