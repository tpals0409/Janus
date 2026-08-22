import { ChangeEvent, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle, Check, CheckCircle2, Download, FlaskConical, Loader2, Play, Square, Upload
} from 'lucide-react'
import { useStore } from '../../store'
import type { EvaluationExperiment } from '../../types'

const TASKS = [
  ['single_file_bug', 'Single-file bug'],
  ['multi_file_refactor', 'Multi-file refactor'],
  ['investigate_code_tests', 'Code + test investigation']
] as const

function ExperimentLane({ role }: { role: 'baseline' | 'candidate' }) {
  const profiles = useStore((state) => state.agentProfiles)
  const experiments = useStore((state) => state.evaluationExperiments)
  const busy = useStore((state) => state.evaluationBusy)
  const start = useStore((state) => state.startEvaluation)
  const cancel = useStore((state) => state.cancelEvaluation)
  const importReport = useStore((state) => state.importEvaluation)
  const [profileId, setProfileId] = useState('agent_default')
  const [label, setLabel] = useState(role === 'baseline' ? 'baseline' : 'candidate')
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
    <section className="rounded-lg border border-border-strong bg-panel p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="font-mono text-[9px] uppercase tracking-[0.18em] text-faint">{role}</div>
          <h2 className="task-title mt-1 text-[18px] font-semibold">
            {role === 'baseline' ? 'Control profile' : 'Challenger profile'}
          </h2>
        </div>
        {latest && <ExperimentStatus item={latest} onCancel={() => void cancel(latest.id)} />}
      </div>
      <div className="mt-4 grid grid-cols-[1fr_110px] gap-2">
        <label>
          <span className="task-label">AgentProfile</span>
          <select
            value={profileId} onChange={(event) => setProfileId(event.target.value)}
            className="task-input mt-1"
          >
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.name} · {profile.worker_policy}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="task-label">Repeats</span>
          <input
            type="number" min={1} max={20} value={repeats}
            onChange={(event) => setRepeats(Number(event.target.value))}
            className="task-input mt-1 font-mono"
          />
        </label>
      </div>
      <label className="mt-3 block">
        <span className="task-label">Experiment label</span>
        <input value={label} onChange={(event) => setLabel(event.target.value)} className="task-input mt-1" />
      </label>
      <div className="mt-3">
        <span className="task-label">TaskSuite shapes</span>
        <div className="mt-1.5 space-y-1">
          {TASKS.map(([id, name]) => (
            <label key={id} className="flex items-center gap-2 text-[10.5px] text-muted">
              <input
                type="checkbox" checked={tasks.includes(id)}
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
        <button
          disabled={busy || !label.trim() || tasks.length === 0}
          onClick={() => void start({
            role, label: label.trim(), agent_profile_id: profileId,
            repeats, tasks, turn_timeout_seconds: 180
          })}
          className="task-primary-action"
        >
          <Play size={11} /> Run {role}
        </button>
        <label className="task-quiet-action cursor-pointer">
          <Upload size={11} /> Import result
          <input type="file" accept="application/json,.json" onChange={importFile} className="hidden" />
        </label>
        <span className="ml-auto text-[9px] text-faint">Qwen 27B · local only</span>
      </div>
    </section>
  )
}

function ExperimentStatus({ item, onCancel }: { item: EvaluationExperiment; onCancel: () => void }) {
  const active = item.status === 'queued' || item.status === 'running'
  const color = item.status === 'completed' ? 'var(--color-ok)'
    : active ? 'var(--color-warn)' : 'var(--color-danger)'
  return (
    <div className="text-right">
      <div className="flex items-center justify-end gap-1.5 text-[10px] font-semibold uppercase" style={{ color }}>
        {active && <Loader2 size={10} className="animate-spin" />}{item.status}
      </div>
      <div className="mt-1 max-w-[170px] truncate font-mono text-[8.5px] text-faint">{item.label}</div>
      {active && (
        <button onClick={onCancel} className="mt-1 inline-flex items-center gap-1 text-[9px] text-danger">
          <Square size={8} /> Cancel
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
    <section className="mt-5 rounded-lg border border-border-strong bg-panel">
      <div className="grid grid-cols-[minmax(0,1fr)_180px_minmax(0,1fr)] items-end gap-3 border-b border-border p-4">
        <label>
          <span className="task-label">Baseline result</span>
          <select value={baselineId} onChange={(event) => setBaselineId(event.target.value)} className="task-input mt-1">
            <option value="">Select baseline</option>
            {baselines.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
        <div className="text-center">
          <div className="font-mono text-[9px] text-faint">REGRESSION GATE</div>
          <button
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
            className="task-primary-action mx-auto mt-1"
          >
            Compare A/B
          </button>
        </div>
        <label>
          <span className="task-label">Candidate result</span>
          <select value={candidateId} onChange={(event) => setCandidateId(event.target.value)} className="task-input mt-1">
            <option value="">Select candidate</option>
            {candidates.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
      </div>
      <div className="flex items-center gap-4 border-b border-border px-4 py-2 text-[9.5px] text-faint">
        <label>Wall regression ≤ <input type="number" value={wallThreshold} onChange={(e) => setWallThreshold(Number(e.target.value))} className="mx-1 w-14 rounded border border-border bg-raised px-1 py-0.5 font-mono" />%</label>
        <label>Token regression ≤ <input type="number" value={tokenThreshold} onChange={(e) => setTokenThreshold(Number(e.target.value))} className="mx-1 w-14 rounded border border-border bg-raised px-1 py-0.5 font-mono" />%</label>
        <span>Acceptance drop: 0 pp · Intervention increase: 0</span>
      </div>
      {latest ? (
        <div className="p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="font-mono text-[9px] uppercase tracking-[0.18em] text-faint">Latest verdict</div>
              <div className="task-title mt-1 text-[28px] font-semibold uppercase" style={{ color: verdictColor }}>
                {verdict?.replace('_', ' ')}
              </div>
            </div>
            <div className="flex gap-1">
              {(verdict === 'improved' || verdict === 'equivalent') && (
                <button
                  disabled={busy || !projectId}
                  onClick={() => void promote(latest.id)}
                  className="task-quiet-action"
                  title={projectId ? 'Use this measured candidate for new Task attempts' : 'Select a Project first'}
                >
                  <CheckCircle2 size={10} />
                  {project?.promoted_comparison_id === latest.id ? 'Project default' : 'Promote default'}
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
            <div className="mt-3 rounded-md border border-[#f8717140] bg-[#f8717112] p-2 text-[10px] text-danger">
              <AlertTriangle size={11} className="mr-1 inline" />
              {latest.result.condition_mismatches.map((item) => item.field).join(', ')} conditions differ; cost claims are not comparable.
            </div>
          )}
          <div className="mt-4 overflow-hidden rounded-md border border-border">
            <div className="grid grid-cols-[1.4fr_repeat(4,1fr)] bg-raised px-3 py-2 font-mono text-[8.5px] uppercase tracking-wider text-faint">
              <span>Task</span><span>Success</span><span>Wall mean ± σ</span><span>Token Δ</span><span>Attention Δ</span>
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
        <div className="p-8 text-center text-[11px] text-faint">
          Run or import a baseline and candidate, then compare them under one hardware condition.
        </div>
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
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => void load(), 1000)
    return () => window.clearInterval(timer)
  }, [active, load])

  return (
    <main className="min-w-0 flex-1 overflow-y-auto bg-bg">
      <div className="mx-auto max-w-[1180px] px-8 py-7">
        <div className="mb-6 flex items-end justify-between gap-5">
          <div>
            <div className="flex items-center gap-2 font-mono text-[9px] uppercase tracking-[0.2em] text-accent-fg">
              <FlaskConical size={12} /> Evaluation Lab
            </div>
            <h1 className="task-title mt-2 text-[30px] font-semibold tracking-[-0.025em]">Prove the policy change.</h1>
            <p className="mt-2 max-w-[700px] text-[11px] leading-relaxed text-faint">
              Run the same fixed tasks under two immutable profile snapshots. Acceptance is the gate; time, tokens, variance, and attention decide the winner.
            </p>
          </div>
          <div className="text-right font-mono text-[9px] text-faint">
            {experiments.length} experiments<br />{active ? 'runner active' : 'runner idle'}
          </div>
        </div>
        {error && <div className="mb-4 rounded-md border border-[#f8717140] bg-[#f8717112] p-3 text-[10.5px] text-danger">{error}</div>}
        <div className="grid grid-cols-2 gap-5">
          <ExperimentLane role="baseline" />
          <ExperimentLane role="candidate" />
        </div>
        <ComparisonLedger />
      </div>
    </main>
  )
}
