import { FormEvent, type KeyboardEvent, Suspense, lazy, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  AlertTriangle,
  Archive,
  ArrowUp,
  Check,
  ChevronDown,
  CircleDot,
  FolderGit2,
  GitPullRequest,
  GitBranch,
  GitCompareArrows,
  ExternalLink,
  Laptop,
  Loader2,
  MessageSquare,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  GitCommitHorizontal,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Wifi,
  WifiOff,
  X,
  Zap
} from 'lucide-react'
import { janusApi } from '../../api'
import { useAgentProfileOptions, useStore } from '../../store'
import { useDomainEvent } from '../../domainEvents'
import type { ApprovalRequest, ChangeLayer, ChangeSetFile, Project, Span, Task } from '../../types'
import ContextInspector from './ContextInspector'
import { Button, CascadingMenu, ConfirmDialog, EmptyState, Listbox, type MenuColumn, Status } from '../ui'
import { ModelBlockedNotice, useLocalModelBlock } from '../ModelSetup'
import { taskStatusMeta } from '../../taskStatus'
import { modelOptions, modelSelection, subscriptionChoices } from '../../subscriptionModels'

interface TranscriptItem {
  key: string
  role: string
  content: string
  at?: string
  streaming?: boolean
  tool?: { name: string; callId: string; status: 'active' | 'done' | 'failed'; startedAtMs: number | null; durationMs?: number }
}

/* 계약 §11: 좌측 거터의 mono 타임스탬프. 시각이 없는 항목(낙관적 전사)은 빈 거터. */
const formatClock = (iso?: string): string => {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('ko-KR', { hour12: false, hour: '2-digit', minute: '2-digit' })
}

const toMs = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

const TOOL_ARG_KEYS = ['path', 'file_path', 'command', 'pattern', 'query', 'url']

function toolDetail(args: unknown): string {
  if (!args || typeof args !== 'object') return ''
  const record = args as Record<string, unknown>
  const value = TOOL_ARG_KEYS.map((key) => record[key]).find((v) => typeof v === 'string' && v)
    ?? Object.values(record).find((v) => typeof v === 'string' && v)
  if (typeof value !== 'string') return ''
  return value.length > 72 ? `${value.slice(0, 71)}…` : value
}

function useApprovalCountdown(deadlineMs?: number): number | null {
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    if (deadlineMs === undefined) return
    const id = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [deadlineMs])
  if (deadlineMs === undefined) return null
  return Math.max(0, Math.ceil((deadlineMs - nowMs) / 1000))
}

function ApprovalCard({ approval, variant }: { approval: ApprovalRequest; variant: 'task' | 'worker' }) {
  const respond = useStore((state) => state.respondTaskApproval)
  const dismiss = useStore((state) => state.dismissTaskApproval)
  const remaining = useApprovalCountdown(approval.deadline_epoch_ms)
  const expired = remaining !== null && remaining <= 0
  const outerClass = variant === 'task' ? 'task-decision-card' : 'worker-modal__approval'
  const copyClass = variant === 'task' ? 'task-decision-card__copy' : undefined
  const actionsClass = variant === 'task' ? 'task-decision-card__actions' : 'worker-modal__approval-actions'
  const Hint = variant === 'task' ? 'span' : 'small'
  if (expired) {
    return (
      <div className={outerClass}>
        <div className={copyClass}>
          <strong><code>{approval.tool}</code> 허용 대기가 끝났어요</strong>
          <Hint>제한 시간 안에 응답이 없어 거부로 처리했어요.</Hint>
        </div>
        <div className={actionsClass}>
          <button type="button" onClick={() => dismiss(approval.id)} className="task-quiet-action">닫기</button>
        </div>
      </div>
    )
  }
  // 계약 §11: 예측 힌트("허용하면 ~해요")를 먼저, 버튼은 나중에. 안심 문장은
  // 실제로 되돌릴 수 있는 파일 수정에만 — 셸 명령에 붙이면 거짓말이 된다.
  const consequence = variant !== 'task'
    ? '이 워커가 답을 기다리고 있어요.'
    : approval.approval_scope === 'workspace_shell'
      ? '허용하면 이 작업 공간에서 명령을 실행해요.'
      : approval.approval_scope === 'workspace_write'
        ? '허용하면 이 작업 공간의 파일을 고쳐요.'
        : '허용하면 이 도구를 작업 공간에서 실행해요.'
  return (
    <div className={outerClass}>
      <div className={copyClass}>
        <strong><span className="text-warn" aria-hidden="true">△</span> <code>{approval.tool}</code> — 허용을 기다리고 있어요</strong>
        <Hint>{consequence}</Hint>
        {variant === 'task' && approval.approval_scope === 'workspace_write' && (
          <Hint>허용해도 커밋 전엔 되돌릴 수 있어요.</Hint>
        )}
        {remaining !== null && (
          <span className="approval-timer" data-urgent={remaining <= 60 ? '' : undefined}>
            남은 시간 {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, '0')}
          </span>
        )}
      </div>
      <div className={actionsClass}>
        <button type="button" onClick={() => respond(approval.id, false)} className="task-quiet-action">거부</button>
        <button
          type="button"
          onClick={() => respond(approval.id, true, 'once')}
          className={approval.rememberable ? 'task-quiet-action' : 'task-primary-action'}
        >
          이번만 허용
        </button>
        {approval.rememberable && (
          // 반복 승인 피로의 주범은 '이번만'이 primary였던 것 — 기억 옵션을 기본으로.
          <button type="button" onClick={() => respond(approval.id, true, 'session_workspace')} className="task-primary-action">
            {approval.approval_scope === 'workspace_shell' ? '이 작업에서 명령 허용' : '이 작업에서 파일 수정 허용'}
          </button>
        )}
      </div>
    </div>
  )
}


const TaskDevelopmentSurface = lazy(() => import('./TaskDevelopmentSurface'))
const FileView = lazy(() => import('../FileView'))
// 마크다운 렌더러는 초기 번들 예산을 넘긴다 — 첫 답변이 올 때 받아온다.
const TaskMarkdown = lazy(() => import('./TaskMarkdown'))

/** 컴포저의 실행기 선택 — 칩 하나에서 단계적으로 펼쳐진다.
 *
 *  로컬 프로필은 고를 게 실행기뿐이라 컬럼이 하나다. 구독형을 고르면 그 오른쪽으로
 *  모델·사고 강도 컬럼이 이어진다. 칩 세 개를 나란히 두면 로컬에서 쓰지도 않는
 *  자리가 늘 잡혀 있고, 가로가 좁은 컴포저에서 그만큼 입력이 밀린다.
 *
 *  모델·강도는 config를 통째로 교체하는 API를 쓰므로 한쪽을 바꿀 때 반대쪽 값을
 *  실어 보낸다 — 빠뜨리면 반대쪽 손잡이가 조용히 초기화된다.
 */
function ComposerModelSelect() {
  const options = useAgentProfileOptions()
  const selected = useStore((state) => state.selectedAgentProfileId)
  const selectProfile = useStore((state) => state.selectAgentProfile)
  const session = useStore((state) => state.taskSession)
  const agentProfiles = useStore((state) => state.agentProfiles)
  const modelProfiles = useStore((state) => state.modelProfiles)
  const update = useStore((state) => state.updateModelProfileConfig)
  const busy = useStore((state) => state.profileBusy)
  if (options.length === 0) return <span><Zap size={13} /> 로컬 에이전트</span>

  const profile = modelProfiles.find((item) =>
    item.id === agentProfiles.find((agent) => agent.id === selected)?.model_profile_id
  )
  const choices = subscriptionChoices(profile?.provider)
  const { model, effort } = modelSelection(profile)
  const columns: MenuColumn[] = [
    { label: '실행기', value: selected, options, onChange: selectProfile },
  ]
  if (profile && choices) {
    columns.push({
      label: '모델',
      value: model,
      options: modelOptions(profile.provider, model),
      onChange: (value) => void update(profile.id, { model: value, effort }),
    })
    columns.push({
      label: '사고 강도',
      value: effort,
      options: [
        { value: '', label: '기본' },
        ...choices.efforts.map((level) => ({ value: level, label: level })),
      ],
      onChange: (value) => void update(profile.id, { model, effort: value }),
    })
  }

  // 요약은 목록에 보이는 라벨을 그대로 쓴다 — 칩이 'opus'라 쓰고 메뉴가 'Opus'라
  // 쓰면 같은 값의 두 표기를 사용자가 대조하게 된다.
  const summary = columns
    .map((column) => column.options.find((option) => option.value === column.value)?.label)
    .filter(Boolean)
    .join(' · ')
  const differs = Boolean(session && session.agent_profile_id !== selected)
  return (
    <span className="janus-composer__model" title="실행기·모델·사고 강도 — 새 턴부터 적용">
      <CascadingMenu
        label="모델 선택"
        summary={summary}
        columns={columns}
        disabled={busy}
        placement="top"
        compact
        icon={<Zap size={13} aria-hidden="true" />}
        className="janus-composer__model-trigger"
      />
      {differs && <em>새 시도부터</em>}
    </span>
  )
}

const STATE_LABEL: Record<string, string> = {
  created: '연결 준비', idle: '대화 가능', running: '실행 중', stopped: '중단됨',
  completed: '완료', queued: '대기열', passed: '통과', failed: '실패', error: '오류',
  preparing: '준비 중', ready: '준비됨', archived: '보관됨', merged: '병합됨',
  pending: '대기 중', success: '성공', neutral: '중립', skipped: '건너뜀', unknown: '알 수 없음',
  validating: '검증 중', recovered: '복구됨', allocating: '할당 중', creating: '생성 중',
  interrupted: '중단됨', force_removed: '강제 제거됨'
}
const stateLabel = (value: string) => STATE_LABEL[value.toLowerCase()] ?? value

// `/이름` 자동완성은 새 작업 컴포저와 세션 컴포저가 함께 쓴다 — 한 곳에만 있으면 새 세션에서 스킬을 못 찾는다.
type SkillCommand = ReturnType<typeof useSkillCommand>

function useSkillCommand(value: string, sessionSkills?: { skill_id: string }[]) {
  const profileSkills = useStore((state) => state.agentProfileSkills)
  const [selection, setSelection] = useState(0)
  const suggestions = useMemo(() => {
    if (!value.startsWith('/') || value.slice(1).includes(' ')) return []
    const needle = value.slice(1).toLowerCase()
    const available = new Map(
      profileSkills
        .filter((skill) => skill.activation_mode !== 'off')
        .map((skill) => [skill.skill_id, skill])
    )
    for (const skill of sessionSkills ?? []) available.set(skill.skill_id, skill as never)
    return [...available.values()].filter((skill) =>
      skill.name.toLowerCase().includes(needle)
      || `${skill.namespace}:${skill.name}`.toLowerCase().includes(needle)
    ).slice(0, 6)
  }, [value, profileSkills, sessionSkills])
  useEffect(() => {
    setSelection(0)
  }, [value])
  return { suggestions, selection, setSelection }
}

function skillCommandKeyDown(
  event: KeyboardEvent<HTMLTextAreaElement>,
  command: SkillCommand,
  handlers: { pick: (name: string) => void, clear: () => void },
): boolean {
  const { suggestions, selection, setSelection } = command
  if (suggestions.length === 0) return false
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    setSelection((selection + 1) % suggestions.length)
    return true
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    setSelection((selection - 1 + suggestions.length) % suggestions.length)
    return true
  }
  if (event.key === 'Escape') {
    event.preventDefault()
    handlers.clear()
    return true
  }
  if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
    event.preventDefault()
    handlers.pick(suggestions[selection].name)
    return true
  }
  return false
}

function skillCommandProps(command: SkillCommand) {
  return {
    'aria-haspopup': 'listbox' as const,
    'aria-expanded': command.suggestions.length > 0,
    'aria-controls': 'skill-command-menu',
    'aria-activedescendant': command.suggestions.length > 0
      ? `skill-command-${command.selection}` : undefined
  }
}

function SkillCommandMenu(
  { command, onPick }: { command: SkillCommand, onPick: (name: string) => void },
) {
  if (command.suggestions.length === 0) return null
  return (
    <div className="skill-command-menu" role="listbox" aria-label="사용 가능한 스킬" id="skill-command-menu">
      <div className="skill-command-menu__header">
        <span><Sparkles size={13} /> 스킬</span>
        <small>{command.suggestions.length}개 사용 가능</small>
      </div>
      {command.suggestions.map((skill, index) => (
        <button
          key={skill.skill_version_id}
          id={`skill-command-${index}`}
          type="button"
          role="option"
          aria-selected={index === command.selection}
          aria-label={`/${skill.name} · ${skill.activation_mode === 'manual' ? '수동' : '자동'}`}
          onMouseEnter={() => command.setSelection(index)}
          onClick={() => onPick(skill.name)}
          className="skill-command-menu__item"
        >
          <span className="skill-command-menu__command">/{skill.name}</span>
          <span className="skill-command-menu__description">{skill.description}</span>
          <span className="skill-command-menu__mode">
            {skill.activation_mode === 'manual' ? '수동' : '자동'}
          </span>
        </button>
      ))}
    </div>
  )
}

function StatusBadge({ task }: { task: Task }) {
  const status = task.status
  const meta = taskStatusMeta(task)
  const tone = status === 'failed' ? 'danger'
    : status === 'needs_you' && task.attention_reason === 'conversation_idle' ? 'success'
      : status === 'preparing' || status === 'needs_you' ? 'warning'
        : status === 'todo' ? 'muted' : 'success'
  return <Status tone={tone}>{meta.label}</Status>
}

function DelegationBar({ project }: { project: Project }) {
  const delegateTask = useStore((state) => state.delegateTask)
  const busy = useStore((state) => state.taskBusy)
  const [objective, setObjective] = useState('')
  const [mockupFirst, setMockupFirst] = useState(false)
  const objectiveRef = useRef<HTMLTextAreaElement>(null)
  const skillCommand = useSkillCommand(objective)
  const chooseSkill = (name: string) => {
    setObjective(`/${name} `)
    requestAnimationFrame(() => objectiveRef.current?.focus())
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!objective.trim() || busy) return
    await delegateTask(objective, mockupFirst ? 'mockup' : 'direct')
    if (!useStore.getState().taskActionError) setObjective('')
  }

  return (
    <main className="new-chat-surface">
      <div className="new-chat-intro">
        {/* 계약 §13: 심볼은 타이틀바에 한 번만 — 여기선 반복하지 않는다. */}
        <h1>{project.name}에서 새 작업</h1>
        <p>목표를 말하면 Janus가 작업 계약과 격리된 실행 공간을 만듭니다.</p>
      </div>
      <div className="new-chat-composer">
      <SkillCommandMenu command={skillCommand} onPick={chooseSkill} />
      <form onSubmit={submit} className="janus-composer janus-composer--new">
        <textarea
          autoFocus
          ref={objectiveRef}
          rows={3}
          value={objective}
          {...skillCommandProps(skillCommand)}
          onChange={(event) => setObjective(event.target.value)}
          onKeyDown={(event) => {
            if (skillCommandKeyDown(event, skillCommand, {
              pick: chooseSkill, clear: () => setObjective('')
            })) return
            if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
            event.preventDefault()
            event.currentTarget.form?.requestSubmit()
          }}
          placeholder="무엇을 요청할까요?"
          aria-label="Janus에게 위임할 목표"
        />
        <ModelBlockedNotice />
        <div className="janus-composer__footer">
          <label className="flex items-center gap-1.5 text-[10px] text-faint">
            <input
              type="checkbox"
              checked={mockupFirst}
              onChange={(event) => setMockupFirst(event.target.checked)}
              className="ui-checkbox"
            />
            프론트 목업부터 시작
          </label>
          <div className="janus-composer__meta">
            <ComposerModelSelect />
            <button type="submit" disabled={busy || !objective.trim()} className="janus-composer__send" aria-label={busy ? '준비 중' : '위임'}>
              {busy ? <Loader2 size={15} className="animate-spin" /> : <ArrowUp size={17} />}
            </button>
          </div>
        </div>
      </form>
      </div>
    </main>
  )
}

function WorkspaceCard({ task }: { task: Task }) {
  const prepare = useStore((state) => state.prepareWorkspace)
  const retry = useStore((state) => state.retryWorkspace)
  const inspect = useStore((state) => state.inspectWorkspace)
  const archive = useStore((state) => state.archiveWorkspace)
  const deleteBranch = useStore((state) => state.deleteWorkspaceBranch)
  const updateTask = useStore((state) => state.updateTask)
  const inspection = useStore((state) => state.workspaceInspection)
  const busy = useStore((state) => state.taskBusy)
  const [danger, setDanger] = useState<'force' | 'branch' | null>(null)
  const [editingBase, setEditingBase] = useState(false)
  const [baseRef, setBaseRef] = useState(task.base_ref)
  const workspace = task.workspace
  const gitStatus = inspection?.git_status

  useEffect(() => {
    setBaseRef(task.base_ref)
    setEditingBase(false)
  }, [task.id, task.base_ref])

  if (!workspace) {
    return (
      <section className="task-card">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="task-label">작업 공간</div>
            <h3 className="mt-1 text-[14px] font-semibold">아직 워크트리가 없습니다</h3>
            <p className="mt-1 max-w-[560px] text-[11px] leading-relaxed text-faint">
              저장소와 기준 리프를 검증합니다. 작업은 이 저장소의 현재 브랜치에서 직접 이뤄집니다.
            </p>
          </div>
          <button onClick={prepare} disabled={busy} className="task-primary-action">
            <FolderGit2 size={13} /> 작업 공간 준비
          </button>
        </div>
      </section>
    )
  }

  const stateColor =
    workspace.state === 'ready'
      ? 'var(--color-ok)'
      : workspace.state === 'failed'
        ? 'var(--color-danger)'
        : workspace.state === 'preparing'
          ? 'var(--color-warn)'
          : 'var(--color-muted)'

  return (
    <section className="task-card">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="task-label">작업 공간</div>
          <div className="mt-1 flex items-center gap-2">
            {workspace.state === 'preparing' ? (
              <Loader2 size={14} className="animate-spin" style={{ color: stateColor }} />
            ) : (
              <CircleDot size={14} style={{ color: stateColor }} />
            )}
            <h3 className="text-[14px] font-semibold">{stateLabel(workspace.state)}</h3>
            <span className="font-mono text-[10px] text-faint">{stateLabel(workspace.progress)}</span>
          </div>
        </div>
        {workspace.state === 'failed' && (
          <button onClick={retry} disabled={busy} className="task-primary-action">
            <RotateCcw size={13} /> 준비 재시도
          </button>
        )}
        {workspace.state === 'ready' && (
          <button onClick={inspect} disabled={busy} className="task-quiet-action">
            <RefreshCw size={12} /> 변경 확인
          </button>
        )}
      </div>

      {workspace.error && (
        <div className="error-strip mt-3 font-mono text-[10px] leading-relaxed">
          {workspace.error}
        </div>
      )}

      {workspace.state === 'failed' && (
        <div className="mt-3 border border-border bg-surface p-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="task-label">기준 리프 복구</div>
              <p className="mt-1 text-[10.5px] text-faint">
                기록된 리프가 없다면 재시도 전에 작업 계약을 수정하세요.
              </p>
            </div>
            {!editingBase && (
              <button onClick={() => setEditingBase(true)} className="task-quiet-action">
                리프 편집
              </button>
            )}
          </div>
          {editingBase && (
            <div className="mt-3 flex gap-2">
              <input
                value={baseRef}
                onChange={(event) => setBaseRef(event.target.value)}
                className="task-input mt-0 min-w-0 flex-1 font-mono text-[10.5px]"
              />
              <button
                onClick={async () => {
                  await updateTask({ base_ref: baseRef.trim() })
                  setEditingBase(false)
                }}
                disabled={busy || !baseRef.trim()}
                className="task-primary-action"
              >
                리프 저장
              </button>
            </div>
          )}
        </div>
      )}

      <dl className="mt-4 grid grid-cols-[100px_1fr] gap-x-4 gap-y-2 border-t border-border pt-3 text-[10.5px]">
        <dt className="text-faint">브랜치</dt>
        <dd className="flex min-w-0 items-center gap-1.5 font-mono text-muted">
          <GitBranch size={11} className="shrink-0" />
          <span className="truncate">{workspace.branch_name ?? '할당 중…'}</span>
        </dd>
        <dt className="text-faint">루트</dt>
        <dd className="truncate font-mono text-muted">{workspace.root_path ?? '할당 중…'}</dd>
      </dl>

      {workspace.state === 'ready' && gitStatus && (
        <div className="mt-3 flex items-center gap-2 border border-border bg-panel px-3 py-2 text-[10.5px]">
          {gitStatus.dirty ? <AlertTriangle size={13} className="text-warn" /> : <ShieldCheck size={13} className="text-ok" />}
          <span className={gitStatus.dirty ? 'text-warn' : 'text-ok'}>
            {gitStatus.dirty
              ? `추적 ${gitStatus.tracked_changes.length}건 · 미추적 ${gitStatus.untracked.length}건 · 미병합 ${gitStatus.unmerged.length}건`
              : '깨끗함 · 안전하게 보관 가능'}
          </span>
        </div>
      )}

      <div className="mt-4 flex items-center justify-between border-t border-border pt-3">
        <span className="text-[10px] text-faint">
          안전 보관은 워크트리를 제거하고 브랜치는 보존합니다.
        </span>
        <div className="flex gap-2">
          {workspace.state === 'ready' && (
            <>
              <button
                onClick={() => archive(false)}
                disabled={busy}
                className="task-quiet-action"
              >
                <Archive size={12} /> 안전 보관
              </button>
              <button onClick={() => setDanger('force')} className="task-danger-link">
                강제 제거…
              </button>
            </>
          )}
          {workspace.state === 'archived' && workspace.branch_name && (
            <button onClick={() => setDanger('branch')} className="task-danger-link">
              브랜치 삭제…
            </button>
          )}
        </div>
      </div>

      {danger && (
        <div className="mt-3 flex items-center justify-between gap-4 border border-danger bg-panel px-3 py-2">
          <div className="text-[10.5px] leading-relaxed text-danger">
            {danger === 'force'
              ? '워크트리 변경을 즉시 폐기합니다. 브랜치는 보존됩니다.'
              : `${workspace.branch_name} 브랜치를 영구 삭제합니다.`}
          </div>
          <div className="flex shrink-0 gap-2">
            <button onClick={() => setDanger(null)} className="rounded px-2 py-1 text-[10.5px] text-muted">
              취소
            </button>
            <button
              onClick={() => {
                if (danger === 'force') archive(true)
                else deleteBranch()
                setDanger(null)
              }}
              className="ui-button ui-button--danger ui-button--compact"
            >
              확인
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

function TaskRuntimeCard({ task }: { task: Task }) {
  const profiles = useStore((state) => state.agentProfiles)
  const profileOptions = useAgentProfileOptions()
  const profileSkills = useStore((state) => state.agentProfileSkills)
  const selectedProfileId = useStore((state) => state.selectedAgentProfileId)
  const selectProfile = useStore((state) => state.selectAgentProfile)
  const session = useStore((state) => state.taskSession)
  const events = useStore((state) => state.taskSessionEvents)
  const connected = useStore((state) => state.taskConnected)
  const active = useStore((state) => state.taskTurnActive)
  const busy = useStore((state) => state.taskBusy)
  const runtimeError = useStore((state) => state.taskRuntimeError)
  const approvals = useStore((state) => state.taskApprovals)
  const pendingDelegation = useStore((state) => state.pendingDelegation)
  const startSession = useStore((state) => state.startTaskSession)
  const resumeSession = useStore((state) => state.resumeTaskSession)
  const sendMessage = useStore((state) => state.sendTaskMessage)
  const cancelTurn = useStore((state) => state.cancelTaskTurn)
  const stopSession = useStore((state) => state.stopTaskSession)
  const revokeApprovalScope = useStore((state) => state.revokeTaskApprovalScope)
  const approveMockup = useStore((state) => state.approveTaskMockup)
  const rejectMockup = useStore((state) => state.rejectTaskMockup)
  const [message, setMessage] = useState('')
  const [confirmNewAttempt, setConfirmNewAttempt] = useState(false)
  const messageRef = useRef<HTMLTextAreaElement>(null)
  const ready = task.workspace?.state === 'ready'
  const resumable = session?.status === 'created' || session?.status === 'idle'
  const restartable = session?.status === 'stopped' || session?.status === 'failed'
  const selectedProfile = profiles.find((profile) => profile.id === selectedProfileId)
  const budget = session?.dispatch.budget ?? selectedProfile?.budget
  const usage = session?.dispatch.usage
  const adaptive = session?.dispatch.adaptive_decision
  // 서버 프롬프트 캐시(APC) 실측 적중률 — usage 이벤트 누적. 미보고 서버는 null.
  const cacheRate = useMemo(() => {
    let prompt = 0
    let cached = 0
    for (const event of events) {
      if (event.kind !== 'agent_event' || event.payload.kind !== 'usage') continue
      prompt += Number(event.payload.prompt_tokens) || 0
      cached += Number(event.payload.cached_tokens) || 0
    }
    return prompt > 0 && cached > 0 ? Math.round((cached / prompt) * 100) : null
  }, [events])
  const [priority, setPriority] = useState(selectedProfile?.budget.queue.priority ?? 0)
  const [queueTimeout, setQueueTimeout] = useState(
    Math.round((selectedProfile?.budget.queue.timeout_ms ?? 300000) / 1000)
  )

  useEffect(() => {
    if (!selectedProfile) return
    setPriority(selectedProfile.budget.queue.priority)
    setQueueTimeout(Math.round(selectedProfile.budget.queue.timeout_ms / 1000))
  }, [selectedProfile])

  const transcriptRef = useRef<HTMLDivElement>(null)
  const transcript = useMemo(() => {
    const persisted = events.filter((event) => event.kind === 'transcript')
    const lastTranscriptSeq = persisted.at(-1)?.seq ?? 0
    const live = events.filter((event) => {
      if (event.seq <= lastTranscriptSeq) return false
      if (event.kind === 'optimistic_transcript') return true
      if (event.kind !== 'agent_event') return false
      // worker에게 보낸 지시/받은 답은 대화가 아니다 — 사람이 두 번 입력한 것처럼 보인다.
      if (event.payload.worker_id) return false
      const kind = String(event.payload.kind ?? '')
      return kind === 'user' || kind === 'assistant'
        || kind === 'reasoning_delta' || kind === 'text_delta'
        || kind === 'tool_start' || kind === 'tool_result'
    })
    const confirmedUsers = new Set(
      live
        .filter((event) => event.kind === 'agent_event' && event.payload.kind === 'user')
        .map((event) => String(event.payload.content ?? event.payload.text ?? ''))
    )
    const visibleLive = live.filter((event) => !(
      event.kind === 'optimistic_transcript' &&
      confirmedUsers.has(String(event.payload.content ?? event.payload.text ?? ''))
    ))
    const items: TranscriptItem[] = []
    const ROLES: Record<string, string> = { reasoning_delta: 'reasoning', text_delta: 'assistant' }
    for (const event of [...persisted, ...visibleLive]) {
      const payload = event.payload
      const raw = String(payload.kind ?? 'event')
      if (raw === 'tool_start' || raw === 'tool_result') {
        const callId = String(payload.call_id ?? event.seq)
        if (raw === 'tool_start') {
          items.push({
            key: `tool-${callId}-${event.seq}`, role: 'tool', content: toolDetail(payload.args),
            tool: { name: String(payload.name ?? 'tool'), callId, status: 'active', startedAtMs: toMs(payload.at_ms) }
          })
          continue
        }
        const failed = !!payload.value && typeof payload.value === 'object'
          && 'error' in (payload.value as Record<string, unknown>)
        const open = items.find((item) => item.tool?.callId === callId && item.tool.status === 'active')
        if (open?.tool) {
          open.tool.status = failed ? 'failed' : 'done'
          const endedAtMs = toMs(payload.at_ms)
          if (open.tool.startedAtMs !== null && endedAtMs !== null) {
            open.tool.durationMs = endedAtMs - open.tool.startedAtMs
          }
        } else {
          // 재개 직후처럼 start를 못 본 result도 행으로 남긴다.
          items.push({
            key: `tool-${callId}-${event.seq}`, role: 'tool', content: '',
            tool: { name: String(payload.name ?? 'tool'), callId, status: failed ? 'failed' : 'done', startedAtMs: null }
          })
        }
        continue
      }
      const role = ROLES[raw] ?? raw
      const streaming = raw.endsWith('_delta')
      const content = String(payload.content ?? payload.text ?? '')
      if (!content) continue
      const previous = items.at(-1)
      // 토큰은 조각으로 흘러온다 — 이어 붙여야 한 덩어리 글이 된다.
      // 줄바꿈뿐인 조각도 글의 일부이므로 이어 붙이는 중에는 버리지 않는다.
      if (streaming && previous?.streaming && previous.role === role) {
        previous.content += content
        continue
      }
      // 공백뿐인 내용은 빈 말풍선이 된다. 이미 저장된 기록에도 남아 있어 여기서 막는다.
      if (!content.trim()) continue
      // 한 step이 끝나면 완결된 assistant 이벤트가 온다. 같은 글이므로 흘려둔 조각을 대체한다.
      if (!streaming && role === 'assistant' && previous?.streaming && previous.role === 'assistant') {
        previous.content = content
        previous.streaming = false
        continue
      }
      items.push({ key: `${event.seq}-${event.kind}`, role, content, streaming, at: event.created_at })
    }
    if (
      items.length === 0 && !session &&
      pendingDelegation?.taskId === task.id
    ) {
      items.push({ key: `pending-${task.id}`, role: 'user', content: pendingDelegation.objective })
    }
    return items
  }, [events, pendingDelegation, session, task.id])

  const partialRecovery = events.some((event) =>
    event.kind === 'agent_event' && event.payload.kind === 'worker_state' &&
    event.payload.status === 'completed_partial'
  )
  const phase = useMemo(() => {
    if (partialRecovery && active) return '부분 결과 검증 중'
    for (let i = events.length - 1; i >= 0; i -= 1) {
      const kind = String(events[i].payload.kind ?? '')
      if (kind === 'text_delta' || kind === 'assistant') return '답하는 중'
      if (kind === 'reasoning_delta') return '사고 중'
      if (kind.startsWith('tool_')) return '도구 실행 중'
      if (kind === 'resource_queue_wait' || kind === 'resource_queue_enter') return '모델 대기 중'
    }
    return '준비 중'
  }, [active, events, partialRecovery])
  const lastTurnOutcome = useMemo(() => {
    const payload = [...events].reverse().find((event) => event.kind === 'turn_end')?.payload
    const raw = payload?.outcome
    if (!raw || typeof raw !== 'object') return null
    const outcome = raw as Record<string, unknown>
    return {
      status: String(outcome.outcome ?? 'partial'),
      summary: String(outcome.summary ?? ''),
      evidence: Array.isArray(outcome.evidence) ? outcome.evidence.map(String) : []
    }
  }, [events])
  const latestMtpMetrics = useMemo(() => {
    const payload = [...events].reverse().find((event) => (
      event.kind === 'agent_event' && event.payload.kind === 'speculative_metrics'
    ))?.payload
    if (!payload) return null
    return {
      acceptance: Number(payload.acceptance_rate ?? 0),
      accepted: Number(payload.accepted_tokens ?? 0),
      drafted: Number(payload.draft_tokens ?? 0),
      tokensPerSecond: Number(payload.predicted_tokens_per_second ?? 0)
    }
  }, [events])
  const executionSummary = useMemo(() => {
    const toolRuns = transcript.filter((item) => item.role === 'tool').length
    const reasoningChars = transcript
      .filter((item) => item.role === 'reasoning')
      .reduce((total, item) => total + item.content.length, 0)
    return {
      toolRuns,
      reasoningChars,
      elapsedSeconds: Math.round((usage?.active_time_ms ?? 0) / 1000)
    }
  }, [transcript, usage?.active_time_ms])
  const skillCommand = useSkillCommand(message, session?.skills)
  const chooseSkill = (name: string) => {
    setMessage(`/${name} `)
    requestAnimationFrame(() => messageRef.current?.focus())
  }

  // 답이 흘러나오는 동안 바닥에 붙어 있게 한다. 위로 올려 읽는 중이면 끌어내리지 않는다.
  useEffect(() => {
    const node = transcriptRef.current
    if (!node) return
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight
    if (distance > 120) return
    node.scrollTop = node.scrollHeight
  }, [transcript, phase])

  const queueWait = useMemo(() => {
    const waiting = new Map<string, Record<string, unknown>>()
    for (const event of events) {
      if (event.kind !== 'agent_event') continue
      const kind = String(event.payload.kind ?? '')
      const operationId = String(event.payload.operation_id ?? '')
      if (!operationId) continue
      if (kind === 'resource_queue_wait') waiting.set(operationId, event.payload)
      if (kind === 'resource_lease_acquired' || kind === 'resource_queue_end') {
        waiting.delete(operationId)
      }
    }
    return [...waiting.values()].at(-1) ?? null
  }, [events])
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!message.trim()) return
    if (task.workflow_stage === 'mockup' && task.status === 'needs_you') {
      const recorded = await rejectMockup(message)
      if (!recorded) return
    }
    sendMessage(message)
    setMessage('')
  }

  return (
    <section className="task-card task-runtime-card">
      <details className="task-session-settings" open={!session}>
        <summary>
          <span className="flex min-w-0 items-center gap-2">
            <CircleDot size={12} className={connected ? 'text-ok' : 'text-faint'} />
            <strong>{session ? `시도 ${session.dispatch.attempt}` : '에이전트 세션 준비'}</strong>
            <span className="font-mono text-[10px] text-faint">{session ? stateLabel(session.status) : '실행 전'}</span>
          </span>
          <span className="flex items-center gap-1.5 text-[10px] text-faint"><Settings2 size={11} /> 실행 설정</span>
        </summary>
        <div className="task-session-settings__body">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="task-label">에이전트 세션</div>
          <div className="mt-1 flex items-center gap-2">
            <MessageSquare size={14} className="text-muted" />
            <h3 className="text-[14px] font-semibold">
              {session ? `시도 ${session.dispatch.attempt}` : '실행 시도 없음'}
            </h3>
            {session && (
              <span className="rounded-full border border-border-strong px-2 py-0.5 font-mono text-[10px] uppercase text-muted">
                {stateLabel(session.status)}
              </span>
            )}
            <span className={`flex items-center gap-1 text-[10px] ${connected ? 'text-ok' : 'text-faint'}`}>
              {connected ? <Wifi size={10} /> : <WifiOff size={10} />}
              {connected ? '연결됨' : '오프라인'}
            </span>
          </div>
        </div>
        <div className="flex items-end gap-2">
          <div>
            <span className="task-label">에이전트 프로필</span>
            <Listbox
              label="에이전트 프로필 선택"
              value={selectedProfileId}
              options={profileOptions}
              onChange={selectProfile}
              disabled={busy}
              compact
              className="task-select mt-1"
            />
          </div>
          <label>
            <span className="task-label">우선순위</span>
            <input
              type="number"
              value={priority}
              onChange={(event) => setPriority(Number(event.target.value))}
              disabled={busy}
              className="task-input mt-1 w-16"
            />
          </label>
          <label>
            <span className="task-label">대기열 초</span>
            <input
              type="number"
              min={1}
              value={queueTimeout}
              onChange={(event) => setQueueTimeout(Math.max(1, Number(event.target.value)))}
              disabled={busy}
              className="task-input mt-1 w-20"
            />
          </label>
          <button
            onClick={() => {
              if (session && resumable) setConfirmNewAttempt(true)
              else void startSession({ priority, queue_timeout_ms: queueTimeout * 1000 })
            }}
            disabled={!ready || busy || active}
            className="task-primary-action"
            title={ready ? '영속화되는 새 디스패치 시도 생성' : '먼저 작업 공간을 준비하세요'}
          >
            <Play size={12} /> {session ? '새 시도' : '시작'}
          </button>
        </div>
      </div>

      {session && (
        <div className="mt-3 border-t border-border pt-3 font-mono text-[10px] text-faint">
          <div className="grid grid-cols-3 gap-2">
            <span className="truncate" title={session.id}>세션 · {session.id}</span>
            <span className="truncate" title={session.dispatch_id}>디스패치 · {session.dispatch_id}</span>
            <span className="truncate" title={session.agent_profile_id}>프로필 · {session.agent_profile_id}</span>
          </div>
          {(session.skills?.length ?? 0) > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-border pt-2">
              <span className="mr-1 text-faint">고정된 스킬</span>
              {session.skills?.map((skill) => (
                <span
                  key={skill.skill_version_id}
                  title={skill.loaded_at ? skill.load_reason ?? undefined : '이번 세션에서 아직 로드되지 않음'}
                  className="rounded border border-border-strong bg-raised px-1.5 py-0.5 text-muted"
                >
                  {skill.namespace}:{skill.name} · v{skill.version} · {skill.activation_mode === 'auto' ? '자동' : '수동'} · {skill.loaded_at ? '로드됨' : '대기'}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {budget && (
        <div className={`mt-3 grid ${cacheRate !== null ? 'grid-cols-5' : 'grid-cols-4'} gap-2 border border-border bg-base px-3 py-2 font-mono text-[10px] text-faint`}>
          <span>토큰 · {usage ? usage.prompt_tokens + usage.completion_tokens : 0}/{budget.dispatch.token_limit}</span>
          <span>단계 · {usage?.steps ?? 0}/{budget.dispatch.step_limit}</span>
          <span>시간 · {Math.round((usage?.active_time_ms ?? 0) / 1000)}초/{Math.round(budget.dispatch.time_limit_ms / 1000)}초</span>
          <span>워커 · {usage?.workers_started ?? 0}/{budget.workers.total_limit}</span>
          {cacheRate !== null && <span>캐시 · {cacheRate}%</span>}
          {session?.dispatch.budget_exhausted_reason && (
            <strong className="col-span-full text-danger">
              예산 소진 · {session.dispatch.budget_exhausted_reason}
            </strong>
          )}
          {latestMtpMetrics && (
            <span className="col-span-full text-secondary">
              MTP · 승인 {(latestMtpMetrics.acceptance * 100).toFixed(1)}%
              {' '}({latestMtpMetrics.accepted}/{latestMtpMetrics.drafted})
              {' · '}{latestMtpMetrics.tokensPerSecond.toFixed(1)} tok/s
            </span>
          )}
        </div>
      )}

      {adaptive?.effective && (
        <div className="mt-3 border border-border bg-panel px-3 py-2.5">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[10px] uppercase tracking-[0.08em]">
            <span className="text-secondary">적응형 · {adaptive.task_class?.replaceAll('_', ' ')}</span>
            <span className="text-muted">정책 {adaptive.effective.worker_policy}</span>
            <span className="text-muted">
              역할 {adaptive.effective.worker_roles.length
                ? adaptive.effective.worker_roles.join(' → ')
                : '상위 에이전트만'}
            </span>
            <span className="text-muted">
              슬롯 {adaptive.scheduler?.model_generation.active ?? 0}/
              {adaptive.scheduler?.model_generation.cap ?? 1} · 대기 {adaptive.scheduler?.model_generation.queued ?? 0}
            </span>
          </div>
          {adaptive.retry?.failure_type && (
            <div className="mt-2 flex items-center justify-between gap-3 border-t border-border-subtle pt-2 text-[10px]">
              <span className="text-warn">
                재시도 · {adaptive.retry.failure_type.replaceAll('_', ' ')} → {adaptive.retry.strategy.replaceAll('_', ' ')}
              </span>
              <span className="font-mono text-[10px] text-faint">
                {adaptive.retry.allowed ? '제한된 재시도' : '수동 전용'}
              </span>
            </div>
          )}
        </div>
      )}

      {runtimeError && (
        <div className="error-strip mt-3">
          {runtimeError}
        </div>
      )}

      {queueWait && (
        <div className="mt-3 flex items-center justify-between gap-3 border border-warn bg-panel px-3 py-2 text-[10.5px] text-warn">
          <span>
            <strong>{String(queueWait.resource).replaceAll('_', ' ')}</strong> 대기 중
            {' · '}{queueWait.reason === 'capacity_exhausted'
              ? '로컬 용량 사용 중'
              : '우선순위가 높은 작업이 앞에 있음'}
          </span>
          <span className="shrink-0 font-mono text-[10px]">
            대기 {String(queueWait.position)} · 실행 {String(queueWait.active)}/{String(queueWait.cap)}
          </span>
        </div>
      )}

        </div>
      </details>

      <div className="task-session-console">
        <div ref={transcriptRef} className="task-transcript">
          {transcript.length === 0 ? (
            <div className="grid h-full place-items-center px-6 text-center text-[10.5px] leading-relaxed text-faint">
              {session
                ? '영속화된 세션에 연결한 뒤 다음 지시를 보내세요.'
                : '프로필을 선택하고 시도를 시작하세요. 실행 로그는 재시작 후에도 남습니다.'}
            </div>
          ) : transcript.map((item) => (
            item.role === 'tool' && item.tool ? (
              <div key={item.key} className="task-tool-row" data-status={item.tool.status}>
                <span className="task-tool-row__glyph" aria-hidden="true">
                  {item.tool.status === 'active' ? '◉' : item.tool.status === 'failed' ? '×' : '✓'}
                </span>
                <span className="sr-only">{item.tool.status === 'active' ? '실행 중' : item.tool.status === 'failed' ? '실패' : '완료'}</span>
                <code>{item.tool.name}</code>
                {item.content && <span className="task-tool-row__detail">{item.content}</span>}
                {item.tool.durationMs !== undefined && item.tool.durationMs >= 100 && (
                  <span className="task-tool-row__time">{(item.tool.durationMs / 1000).toFixed(1)}s</span>
                )}
              </div>
            ) : item.role === 'reasoning' ? (
              <details key={item.key} className="task-reasoning">
                <summary>사고 과정</summary>
                <p>{item.content}</p>
              </details>
            ) : (
              <div key={item.key} className="task-message" data-role={item.role}>
                <span className="task-message__time" aria-hidden="true">{formatClock(item.at)}</span>
                <div className="task-message__body">
                  <span className="sr-only">{item.role === 'user' ? '나' : 'Janus'}</span>
                  {item.role === 'user' ? (
                    <p>{item.content}</p>
                  ) : (
                    <div className="task-markdown">
                      <Suspense fallback={<p>{item.content}</p>}>
                        <TaskMarkdown content={item.content} />
                      </Suspense>
                    </div>
                  )}
                </div>
              </div>
            )
          ))}
          {active && (
            <div className="task-thinking" role="status" aria-live="polite">
              <span className="task-thinking__mark" aria-hidden="true" />
              <span>{phase}</span>
            </div>
          )}
          {!session && pendingDelegation?.taskId === task.id && (
            <div className="task-message" data-role="assistant">
              <span className="task-message__time" aria-hidden="true"></span>
              <div className="task-message__body">
                <span className="sr-only">Janus</span>
                <p>작업 공간을 준비하고 있어요. 로컬 모델이 준비되면 이 대화에서 바로 실행할게요.</p>
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="task-action-stack">
        {(active || lastTurnOutcome || executionSummary.toolRuns > 0 || executionSummary.reasoningChars > 0) && (
          <details className="task-execution-rail" open={active || undefined}>
            <summary>
              <span className="task-execution-rail__identity">
                <span className="task-thinking__mark" aria-hidden="true" />
                <strong>{active ? phase : '최근 실행'}</strong>
              </span>
              <span className="task-execution-rail__metrics">
                {executionSummary.reasoningChars > 0 && <span>사고 {executionSummary.reasoningChars.toLocaleString()}자</span>}
                {executionSummary.toolRuns > 0 && <span>도구 {executionSummary.toolRuns}회</span>}
                {executionSummary.elapsedSeconds > 0 && <span>{executionSummary.elapsedSeconds}초</span>}
              </span>
            </summary>
            <div className="task-execution-rail__body">
              {active && <p>Janus가 현재 <strong>{phase}</strong>입니다. 실행 내역은 우측 환경 패널과 대화 기록에 동시에 보존됩니다.</p>}
              {lastTurnOutcome && !active && (
                <>
                  <p><strong>{lastTurnOutcome.status.replaceAll('_', ' ')}</strong>{lastTurnOutcome.summary ? ` · ${lastTurnOutcome.summary}` : ''}</p>
                  {lastTurnOutcome.evidence.length > 0 && (
                    <ul>{lastTurnOutcome.evidence.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>
                  )}
                </>
              )}
              {latestMtpMetrics && (
                <p className="font-mono">MTP 승인 {(latestMtpMetrics.acceptance * 100).toFixed(1)}% · {latestMtpMetrics.accepted}/{latestMtpMetrics.drafted} · {latestMtpMetrics.tokensPerSecond.toFixed(1)} tok/s</p>
              )}
            </div>
          </details>
        )}

        {approvals.map((approval) => (
          <ApprovalCard key={approval.id} approval={approval} variant="task" />
        ))}

        {session?.approval_scopes?.some((item) => item.scope === 'workspace_write') && (
        <div className="task-session-notice">
          <span className="text-[10.5px] text-muted">이 작업에서 파일 수정을 허용했습니다 — 새 시도에도 유지됩니다.</span>
          <button
            type="button"
            onClick={() => void revokeApprovalScope('workspace_write')}
            disabled={busy}
            className="task-quiet-action"
          >
            파일 수정 권한 취소
          </button>
        </div>
        )}
        {session?.approval_scopes?.some((item) => item.scope === 'workspace_shell') && (
        <div className="task-session-notice">
          <span className="text-[10.5px] text-muted">이 작업에서 명령 실행을 허용했습니다 — 새 시도에도 유지됩니다.</span>
          <button
            type="button"
            onClick={() => void revokeApprovalScope('workspace_shell')}
            disabled={busy}
            className="task-quiet-action"
          >
            명령 실행 권한 취소
          </button>
        </div>
        )}

      {task.workflow_stage === 'mockup' && task.status === 'needs_you' && session?.status === 'idle' && !active && (
        <div className="task-decision-card">
          <div className="task-decision-card__copy">
            <strong className="block text-secondary">프론트 목업 승인 대기</strong>
            <span>화면과 주요 상호작용을 확인한 뒤 구현 진행 여부를 선택하세요.</span>
          </div>
          <div className="task-decision-card__actions">
            <button
              type="button"
              onClick={() => {
                setMessage((current) => current.trim() ? current : '목업 수정 요청: ')
                requestAnimationFrame(() => messageRef.current?.focus())
              }}
              disabled={busy || !connected}
              className="task-quiet-action"
            >
              거절 · 수정 요청
            </button>
            <button
              type="button"
              onClick={() => void approveMockup()}
              disabled={busy}
              className="task-primary-action"
            >
              목업 승인 · 구현 진행
            </button>
          </div>
        </div>
      )}

        {session && (!connected || restartable || ['created', 'running', 'idle'].includes(session.status)) && (
          <div className="task-session-actions">
            <span>
              <CircleDot size={10} className={connected ? 'text-ok' : 'text-faint'} />
              {stateLabel(session.status)} · {connected ? '연결됨' : '연결 끊김'}
            </span>
            <div>
              {!connected && resumable && (
                <button type="button" onClick={() => void resumeSession()} disabled={busy} className="task-primary-action">
                  <Play size={11} /> 재개
                </button>
              )}
              {restartable && (
                <button
                  type="button"
                  onClick={() => void startSession({ priority, queue_timeout_ms: queueTimeout * 1000 })}
                  disabled={!ready || busy || active}
                  className="task-primary-action"
                  title={ready ? '새 디스패치로 작업을 다시 시작' : '먼저 작업 공간을 준비하세요'}
                >
                  <Play size={11} /> 다시 시작
                </button>
              )}
              {['created', 'running', 'idle'].includes(session.status) && (
                <button type="button" onClick={() => void stopSession()} disabled={busy} className="task-quiet-action">세션 중단</button>
              )}
            </div>
          </div>
        )}
      </div>

      <div className="janus-composer-shell">
        <SkillCommandMenu command={skillCommand} onPick={chooseSkill} />
      <form onSubmit={submit} className="janus-composer janus-composer--session">
        <textarea
          ref={messageRef}
          rows={3}
          aria-label="작업 지시"
          value={message}
          {...skillCommandProps(skillCommand)}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (skillCommandKeyDown(event, skillCommand, {
              pick: chooseSkill, clear: () => setMessage('')
            })) return
            if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
            event.preventDefault()
            event.currentTarget.form?.requestSubmit()
          }}
          disabled={!connected || !resumable}
          placeholder={
            !connected
              ? '계속하려면 세션을 재개하세요'
              : active
                ? '지금 보내면 이 턴이 끝난 뒤 실행됩니다'
                : '다음 작업 지시 보내기…'
          }
        />
        <ModelBlockedNotice />
        <div className="janus-composer__footer">
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="janus-composer__tool"
              aria-label="스킬 호출"
              title="스킬 호출"
              onClick={() => {
                setMessage((current) => current.startsWith('/') ? current : `/${current}`)
                requestAnimationFrame(() => messageRef.current?.focus())
              }}
              disabled={!connected || !resumable}
            >
              <Plus size={20} />
            </button>
          </div>
          <div className="janus-composer__meta">
            <ComposerModelSelect />
            {active && (
              <button type="button" onClick={cancelTurn} className="janus-composer__stop" aria-label="턴 취소">
                <Square size={13} />
              </button>
            )}
            <button
              disabled={!connected || !message.trim() || !resumable}
              className="janus-composer__send"
              aria-label={active ? '다음 턴으로 보내기' : '보내기'}
            >
              <ArrowUp size={17} />
            </button>
          </div>
        </div>
      </form>
      </div>
      <ConfirmDialog
        open={confirmNewAttempt}
        title="새 시도를 시작할까요?"
        description="현재 재개 가능한 세션은 중단되고 새 디스패치가 실행 권한을 갖습니다."
        confirmLabel="새 시도 시작"
        onClose={() => setConfirmNewAttempt(false)}
        onConfirm={() => {
          setConfirmNewAttempt(false)
          void startSession({ priority, queue_timeout_ms: queueTimeout * 1000 })
        }}
      />
    </section>
  )
}

const CHANGE_LAYERS: ChangeLayer[] = ['committed', 'staged', 'unstaged', 'untracked']
const CHANGE_LAYER_LABEL: Record<ChangeLayer, string> = {
  committed: '커밋됨', staged: '스테이징됨', unstaged: '스테이징 안 됨', untracked: '미추적'
}

function diffStat(diff: string | null): { add: number; del: number } {
  let add = 0
  let del = 0
  for (const line of (diff ?? '').split('\n')) {
    if (line.startsWith('+') && !line.startsWith('+++')) add += 1
    else if (line.startsWith('-') && !line.startsWith('---')) del += 1
  }
  return { add, del }
}

export function diffLines(diff: string | null) {
  let oldLine = 0
  let newLine = 0
  let hunk: string | null = null
  return (diff ?? '').split('\n').map((text, index) => {
    const header = text.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/)
    if (header) {
      oldLine = Number(header[1])
      newLine = Number(header[3])
      hunk = text
      return {
        index, text, oldLine: null, newLine: null, hunk, header: true,
        oldStart: Number(header[1]), oldCount: Number(header[2] ?? 1),
        newStart: Number(header[3]), newCount: Number(header[4] ?? 1)
      }
    }
    if (text.startsWith('+') && !text.startsWith('+++')) {
      return { index, text, oldLine: null, newLine: newLine++, hunk, header: false }
    }
    if (text.startsWith('-') && !text.startsWith('---')) {
      return { index, text, oldLine: oldLine++, newLine: null, hunk, header: false }
    }
    if (text.startsWith(' ')) {
      return { index, text, oldLine: oldLine++, newLine: newLine++, hunk, header: false }
    }
    return { index, text, oldLine: null, newLine: null, hunk, header: false }
  })
}

type DiffRow = ReturnType<typeof diffLines>[number]

interface DiffGap { key: string; beforeIndex: number; oldStart: number; newStart: number; count: number | null }

/** 접힌 컨텍스트 구간 — hunk 사이·앞뒤의 변경 없는 줄 범위. count null은 파일 끝까지. */
export function buildGaps(lines: DiffRow[]): DiffGap[] {
  const headers = lines.filter((item) => item.header)
  if (headers.length === 0) return []
  const gaps: DiffGap[] = []
  let prevOldEnd = 0
  let prevNewEnd = 0
  for (const header of headers) {
    const count = (header.oldStart ?? 1) - prevOldEnd - 1
    if (count > 0) {
      gaps.push({
        key: `gap-${header.index}`, beforeIndex: header.index,
        oldStart: prevOldEnd + 1, newStart: prevNewEnd + 1, count
      })
    }
    prevOldEnd = (header.oldStart ?? 1) + (header.oldCount ?? 1) - 1
    prevNewEnd = (header.newStart ?? 1) + (header.newCount ?? 1) - 1
  }
  gaps.push({
    key: 'gap-tail', beforeIndex: Number.POSITIVE_INFINITY,
    oldStart: prevOldEnd + 1, newStart: prevNewEnd + 1, count: null
  })
  return gaps
}

/** 짝지어진 -/+ 줄의 공통 앞뒤를 제외한 실제 변경 구간. 한 글자 변경을 놓치지 않게 한다. */
export function wordEmphasis(lines: DiffRow[]): Map<number, [number, number]> {
  const spans = new Map<number, [number, number]>()
  let i = 0
  while (i < lines.length) {
    if (lines[i].text.startsWith('-') && !lines[i].text.startsWith('---')) {
      const removed: DiffRow[] = []
      while (i < lines.length && lines[i].text.startsWith('-') && !lines[i].text.startsWith('---')) removed.push(lines[i++])
      const added: DiffRow[] = []
      while (i < lines.length && lines[i].text.startsWith('+') && !lines[i].text.startsWith('+++')) added.push(lines[i++])
      for (let pair = 0; pair < Math.min(removed.length, added.length); pair += 1) {
        const a = removed[pair].text.slice(1)
        const b = added[pair].text.slice(1)
        if (a === b) continue
        let prefix = 0
        while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix]) prefix += 1
        let suffix = 0
        while (
          suffix < a.length - prefix && suffix < b.length - prefix
          && a[a.length - 1 - suffix] === b[b.length - 1 - suffix]
        ) suffix += 1
        spans.set(removed[pair].index, [prefix + 1, a.length - suffix + 1])
        spans.set(added[pair].index, [prefix + 1, b.length - suffix + 1])
      }
    } else i += 1
  }
  return spans
}

const REV_BY_LAYER: Record<ChangeLayer, string> = {
  committed: 'head', staged: 'index', unstaged: 'worktree', untracked: 'worktree'
}

// Monaco는 FileView와 같은 지연 청크를 공유한다 — 실패나 미지원 언어는 plain으로 남는다.
let monacoSetupPromise: Promise<typeof import('../monacoSetup')> | null = null

async function colorizeDiffLines(
  lines: DiffRow[], path: string
): Promise<Map<number, string> | null> {
  if (import.meta.env.MODE === 'test') return null
  try {
    monacoSetupPromise ??= import('../monacoSetup')
    const { monaco, languageIdFor } = await monacoSetupPromise
    const language = languageIdFor(path)
    if (language === 'plaintext') return null
    monaco.editor.setTheme('janus-ide')
    const rows = lines.filter(
      (item) => !item.header && (item.oldLine !== null || item.newLine !== null)
    )
    if (rows.length === 0) return null
    const html = await monaco.editor.colorize(
      rows.map((item) => item.text.slice(1)).join('\n'), language, { tabSize: 2 }
    )
    const parts = html.split(/<br\/?>/)
    const map = new Map<number, string>()
    rows.forEach((item, position) => {
      if (parts[position] !== undefined) map.set(item.index, parts[position])
    })
    return map
  } catch {
    return null
  }
}

function ChangeSetCard() {
  const changeSet = useStore((state) => state.changeSet)
  const refresh = useStore((state) => state.inspectWorkspace)
  const commitChanges = useStore((state) => state.commitWorkspaceChanges)
  const busy = useStore((state) => state.taskBusy)
  const [commitMessage, setCommitMessage] = useState('')
  const [layer, setLayer] = useState<ChangeLayer>('unstaged')
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [commentLine, setCommentLine] = useState<ReturnType<typeof diffLines>[number] | null>(null)
  const [commentBody, setCommentBody] = useState('')
  const review = useStore((state) => state.review)
  const addComment = useStore((state) => state.addReviewComment)
  const resolveComment = useStore((state) => state.resolveReviewComment)
  const files = changeSet?.sections[layer] ?? []
  const selected: ChangeSetFile | undefined =
    files.find((item) => item.path === selectedPath) ?? files[0]
  const task = useStore((state) => state.task)
  const lines = useMemo(() => diffLines(selected?.diff ?? null), [selected?.diff])
  const hunks = lines.filter((item) => item.header)
  const gaps = useMemo(() => buildGaps(lines), [lines])
  const emphasis = useMemo(() => wordEmphasis(lines), [lines])
  const [syntax, setSyntax] = useState<Map<number, string> | null>(null)
  useEffect(() => {
    setSyntax(null)
    if (!selected?.diff || selected.binary) return
    let cancelled = false
    void colorizeDiffLines(lines, selected.path).then((map) => {
      if (!cancelled) setSyntax(map)
    })
    return () => { cancelled = true }
  }, [lines, selected?.path, selected?.binary, selected?.diff])
  const comments = review?.comments.filter(
    (item) => item.layer === layer && item.file_path === selected?.path
  ) ?? []
  const [expandedGaps, setExpandedGaps] = useState<Set<string>>(new Set())
  const [fileContents, setFileContents] = useState<Record<string, string[] | null>>({})
  const contentKey = selected ? `${layer}:${selected.path}` : null
  const contentLines = contentKey ? fileContents[contentKey] : undefined

  const reviewedKey = changeSet ? `janus.reviewed.${changeSet.revision}` : null
  const [viewed, setViewed] = useState<Set<string>>(new Set())
  useEffect(() => {
    if (!reviewedKey) return
    try {
      setViewed(new Set(JSON.parse(localStorage.getItem(reviewedKey) ?? '[]') as string[]))
    } catch {
      setViewed(new Set())
    }
  }, [reviewedKey])
  const toggleViewed = (key: string) => {
    setViewed((previous) => {
      const next = new Set(previous)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      if (reviewedKey) localStorage.setItem(reviewedKey, JSON.stringify([...next]))
      return next
    })
  }
  const totalFiles = CHANGE_LAYERS.reduce(
    (total, item) => total + (changeSet?.counts[item] ?? 0), 0
  )

  const expandGap = (gap: DiffGap) => {
    setExpandedGaps((previous) => new Set(previous).add(gap.key))
    if (!task || !selected || !contentKey || fileContents[contentKey] !== undefined) return
    void janusApi<{ content: string }>(
      `/tasks/${task.id}/development/file?path=${encodeURIComponent(selected.path)}&rev=${REV_BY_LAYER[layer]}`
    ).then(
      (data) => setFileContents((previous) => ({ ...previous, [contentKey]: String(data.content).split('\n') })),
      () => setFileContents((previous) => ({ ...previous, [contentKey]: null }))
    )
  }

  // 리뷰 키보드 — j/k 파일, n/p 변경 구간, v 확인 표시. 입력 중에는 개입하지 않는다.
  const hunkCursor = useRef(-1)
  useEffect(() => { hunkCursor.current = -1 }, [selected?.path, layer])
  useEffect(() => {
    const handler = (event: globalThis.KeyboardEvent) => {
      const origin = event.target as HTMLElement | null
      if (origin?.closest('input, textarea, select, [contenteditable="true"]')) return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (event.key === 'j' || event.key === 'k') {
        if (files.length === 0) return
        const currentIndex = files.findIndex((item) => item.path === selected?.path)
        const next = files[Math.min(
          files.length - 1,
          Math.max(0, (currentIndex < 0 ? 0 : currentIndex) + (event.key === 'j' ? 1 : -1))
        )]
        if (next) setSelectedPath(next.path)
      } else if (event.key === 'n' || event.key === 'p') {
        if (hunks.length === 0) return
        hunkCursor.current = Math.min(
          hunks.length - 1, Math.max(0, hunkCursor.current + (event.key === 'n' ? 1 : -1))
        )
        document.getElementById(`diff-${layer}-${hunks[hunkCursor.current].index}`)
          ?.scrollIntoView({ block: 'center' })
      } else if (event.key === 'v') {
        if (selected) toggleViewed(`${layer}:${selected.path}`)
      } else return
      event.preventDefault()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  })

  useEffect(() => {
    setSelectedPath(null)
    setCommentLine(null)
    setCommentBody('')
    setExpandedGaps(new Set())
    setFileContents({})
  }, [layer, changeSet?.head_commit, changeSet?.derived_at])

  // 비어 있는 레이어를 보여주며 시작하지 않는다 — 변경이 있는 첫 레이어로 이동.
  useEffect(() => {
    if (!changeSet || changeSet.counts[layer] > 0) return
    const populated = CHANGE_LAYERS.find((item) => changeSet.counts[item] > 0)
    if (populated) setLayer(populated)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [changeSet?.derived_at])

  if (!changeSet) return null
  return (
    <section className="task-card">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="task-label">Git 변경 목록</div>
          <div className="mt-1 font-mono text-[10px] text-faint">
            {changeSet.base_ref}…{changeSet.head_commit.slice(0, 8)} · Git에서 파생됨
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[10px] text-faint">
            확인 {viewed.size}/{totalFiles} · j/k 파일 · n/p 구간 · v 확인
          </span>
          <button onClick={() => void refresh()} className="task-quiet-action">
            <RefreshCw size={12} /> diff 새로고침
          </button>
        </div>
      </div>
      {(changeSet.counts.staged + changeSet.counts.unstaged + changeSet.counts.untracked) > 0 && (
        <form
          className="mt-3 flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            const message = commitMessage.trim()
            if (!message) return
            void commitChanges(message).then((ok) => { if (ok) setCommitMessage('') })
          }}
        >
          <input
            value={commitMessage}
            onChange={(event) => setCommitMessage(event.target.value)}
            placeholder="commit 메시지"
            className="h-7 min-w-0 flex-1 border border-border bg-base px-2 font-mono text-[11px] text-fg placeholder:text-faint"
          />
          <button
            type="submit"
            disabled={busy || !commitMessage.trim()}
            className="task-quiet-action"
            title="staged·unstaged·untracked 변경을 모두 commit합니다"
          >
            <GitCommitHorizontal size={12} /> 커밋
          </button>
        </form>
      )}
      {changeSet.unmerged.length > 0 && (
        <div className="error-strip mt-3">
          <AlertTriangle size={12} className="mr-1.5 inline" />
          미병합 변경 {changeSet.unmerged.length}건으로 검토와 배포가 차단됩니다.
        </div>
      )}
      <div className="mt-4 flex gap-1 border-b border-border">
        {CHANGE_LAYERS.map((item) => (
          <button
            key={item}
            onClick={() => setLayer(item)}
            className="border-b-2 px-2.5 py-2 text-[10.5px] capitalize"
            style={{
              borderColor: item === layer ? 'var(--color-fg)' : 'transparent',
              color: item === layer ? 'var(--color-fg)' : 'var(--color-faint)'
            }}
          >
            {CHANGE_LAYER_LABEL[item]} <span className="ml-1 font-mono">{changeSet.counts[item]}</span>
          </button>
        ))}
      </div>
      <div className="grid min-h-[240px] grid-cols-[220px_minmax(0,1fr)] border-x border-b border-border">
        <div className="border-r border-border bg-raised/40 p-2">
          {files.map((file) => {
            const fileKey = `${layer}:${file.path}`
            const isViewed = viewed.has(fileKey)
            return (
              <div
                key={`${file.status}:${file.old_path ?? ''}:${file.path}`}
                className="mb-1 flex items-start gap-1 rounded hover:bg-panel"
                style={{
                  background: selected?.path === file.path ? 'var(--color-accent-soft)' : undefined,
                  opacity: isViewed ? 0.55 : undefined
                }}
              >
                <button
                  onClick={() => setSelectedPath(file.path)}
                  className="flex min-w-0 flex-1 items-start gap-2 px-2 py-1.5 text-left"
                >
                  <span className="w-7 shrink-0 font-mono text-[10px] text-secondary">{file.status}</span>
                  <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-muted" title={file.path}>
                    {file.old_path ? `${file.old_path} → ${file.path}` : file.path}
                  </span>
                  {(() => {
                    const stat = diffStat(file.diff)
                    if (stat.add === 0 && stat.del === 0) return null
                    return (
                      <span className="shrink-0 font-mono text-[9.5px]">
                        {stat.add > 0 && <span className="text-ok">+{stat.add}</span>}
                        {stat.add > 0 && stat.del > 0 && ' '}
                        {stat.del > 0 && <span className="text-danger">−{stat.del}</span>}
                      </span>
                    )
                  })()}
                </button>
                <button
                  type="button"
                  onClick={() => toggleViewed(fileKey)}
                  aria-pressed={isViewed}
                  aria-label={`${file.path} 확인함 표시`}
                  title="확인함 표시 (v)"
                  className="shrink-0 px-1.5 py-1.5 font-mono text-[10px]"
                  style={{ color: isViewed ? 'var(--success)' : 'var(--text-disabled)' }}
                >
                  ✓
                </button>
              </div>
            )
          })}
          {files.length === 0 && <div className="p-3 text-[10.5px] text-faint">{CHANGE_LAYER_LABEL[layer]} 변경이 없습니다.</div>}
        </div>
        <div className="min-w-0 overflow-auto bg-base p-3">
          {selected ? (
            selected.binary ? (
              <div className="text-[11px] text-faint">바이너리 파일 · {selected.diff_bytes}바이트</div>
            ) : (
              <>
                {selected.large && (
                  <div className="mb-2 text-[10px] text-warn">큰 diff · 미리보기 잘림</div>
                )}
                {hunks.length > 0 && (
                  <div className="sticky top-0 z-10 mb-2 flex gap-1 bg-base pb-2">
                    {hunks.map((item, index) => (
                      <button
                        key={item.index}
                        onClick={() => document.getElementById(`diff-${layer}-${item.index}`)?.scrollIntoView({ block: 'nearest' })}
                        className="rounded border border-border px-1.5 py-0.5 font-mono text-[10px] text-faint hover:text-fg"
                      >
                        변경 구간 {index + 1}
                      </button>
                    ))}
                  </div>
                )}
                <div className="min-w-max font-mono text-[10px] leading-4 text-muted">
                  {(() => {
                    const canExpand = !selected.binary && !selected.status.startsWith('D')
                      && layer !== 'untracked' && contentLines !== null
                    const renderContext = (gap: DiffGap) => {
                      if (!contentLines) {
                        return <div key={`${gap.key}-loading`} className="py-0.5 pl-16 text-faint">불러오는 중…</div>
                      }
                      const count = gap.count ?? Math.max(0, contentLines.length - gap.newStart + 1)
                      return Array.from({ length: count }, (_, offset) => {
                        const oldLine = gap.oldStart + offset
                        const newLine = gap.newStart + offset
                        const text = ` ${contentLines[gap.newStart - 1 + offset] ?? ''}`
                        return (
                          <button
                            key={`${gap.key}-${offset}`}
                            onClick={() => setCommentLine({
                              index: -1, text, oldLine, newLine, hunk: null, header: false
                            })}
                            className="block w-full whitespace-pre text-left hover:bg-hover"
                          >
                            <span className="mr-3 inline-block w-16 select-none text-right text-faint">
                              {oldLine} {newLine}
                            </span>{text}
                          </button>
                        )
                      })
                    }
                    const renderGap = (gap: DiffGap) => {
                      if (!canExpand) return null
                      if (expandedGaps.has(gap.key)) return renderContext(gap)
                      return (
                        <button
                          key={gap.key}
                          type="button"
                          onClick={() => expandGap(gap)}
                          className="block w-full border-y border-border-subtle py-0.5 pl-16 text-left text-faint hover:text-fg"
                        >
                          ⋯ {gap.count !== null ? `${gap.count}줄 펼치기` : '나머지 펼치기'}
                        </button>
                      )
                    }
                    const gapBefore = new Map(
                      gaps.filter((gap) => Number.isFinite(gap.beforeIndex))
                        .map((gap) => [gap.beforeIndex, gap])
                    )
                    const tail = gaps.find((gap) => !Number.isFinite(gap.beforeIndex))
                    const rendered: ReactNode[] = []
                    for (const item of lines) {
                      const gap = gapBefore.get(item.index)
                      if (gap) rendered.push(renderGap(gap))
                      const added = item.text.startsWith('+') && !item.text.startsWith('+++')
                      const removed = item.text.startsWith('-') && !item.text.startsWith('---')
                      const span = emphasis.get(item.index)
                      rendered.push(
                        <button
                          id={`diff-${layer}-${item.index}`}
                          key={item.index}
                          onClick={() => {
                            if (item.oldLine || item.newLine) setCommentLine(item)
                          }}
                          className="block w-full whitespace-pre text-left hover:bg-hover"
                          style={{
                            background: added ? 'var(--diff-add-bg)'
                              : removed ? 'var(--diff-remove-bg)' : undefined,
                            color: added || removed ? 'var(--text-primary)' : undefined
                          }}
                        >
                          <span className="mr-3 inline-block w-16 select-none text-right text-faint">
                            {item.oldLine ?? '·'} {item.newLine ?? '·'}
                          </span>
                          {span && span[1] > span[0] ? (
                            <>
                              {item.text.slice(0, span[0])}
                              <span className={added ? 'diff-emphasis-add' : 'diff-emphasis-remove'}>
                                {item.text.slice(span[0], span[1])}
                              </span>
                              {item.text.slice(span[1])}
                            </>
                          ) : !item.header && syntax?.has(item.index) ? (
                            <>
                              {item.text.slice(0, 1)}
                              {/* Monaco tokenizer 출력 — 원문을 escape해 토큰 span만 담는다 */}
                              <span dangerouslySetInnerHTML={{ __html: syntax.get(item.index)! }} />
                            </>
                          ) : (item.text || ' ')}
                        </button>
                      )
                    }
                    if (tail) rendered.push(renderGap(tail))
                    return rendered
                  })()}
                </div>
                {commentLine && (
                  <div className="sticky bottom-0 mt-3 flex gap-2 border border-border-strong bg-panel p-2">
                    <input
                      autoFocus value={commentBody}
                      onChange={(event) => setCommentBody(event.target.value)}
                      placeholder={`${commentLine.newLine ?? commentLine.oldLine}번 줄에 의견 추가`}
                      className="task-input mt-0 min-w-0 flex-1"
                    />
                    <button
                      disabled={!commentBody.trim()}
                      onClick={async () => {
                        await addComment({
                          revision: changeSet.revision, layer, file_path: selected.path,
                          old_line: commentLine.oldLine, new_line: commentLine.newLine,
                          hunk_header: commentLine.hunk, body: commentBody.trim()
                        })
                        setCommentLine(null)
                        setCommentBody('')
                      }}
                      className="task-primary-action"
                    >
                      <MessageSquare size={11} /> 추가
                    </button>
                    <button onClick={() => setCommentLine(null)} className="task-quiet-action">취소</button>
                  </div>
                )}
                {comments.length > 0 && (
                  <div className="mt-3 space-y-1 border-t border-border pt-2">
                    {comments.map((comment) => (
                      <div key={comment.id} className="flex items-center gap-2 rounded bg-panel px-2 py-1.5 text-[10px]">
                        <span className="font-mono text-faint">L{comment.new_line ?? comment.old_line}</span>
                        <span className={comment.resolved_at ? 'flex-1 line-through text-faint' : 'flex-1 text-fg'}>
                          {comment.body}
                        </span>
                        <button
                          onClick={() => void resolveComment(comment.id, !comment.resolved_at)}
                          className="task-quiet-action"
                        >
                          {comment.resolved_at ? '다시 열기' : '해결'}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </>
            )
          ) : (
            <div className="text-[10.5px] text-faint">변경된 파일을 선택하세요.</div>
          )}
        </div>
      </div>
    </section>
  )
}

function VerificationCard({ task }: { task: Task }) {
  const projects = useStore((state) => state.projects)
  const runs = useStore((state) => state.verificationRuns)
  const busy = useStore((state) => state.verificationBusy)
  const saveCommands = useStore((state) => state.setProjectVerificationCommands)
  const runAll = useStore((state) => state.runVerifications)
  const rerun = useStore((state) => state.rerunVerification)
  const load = useStore((state) => state.loadVerifications)
  const project = projects.find((item) => item.id === task.project_id)
  const [commands, setCommands] = useState<Record<'test' | 'lint' | 'typecheck', string>>({
    test: '', lint: '', typecheck: ''
  })

  useEffect(() => {
    const configured = project?.verification_commands ?? []
    setCommands({
      test: configured.find((item) => item.kind === 'test')?.command ?? '',
      lint: configured.find((item) => item.kind === 'lint')?.command ?? '',
      typecheck: configured.find((item) => item.kind === 'typecheck')?.command ?? ''
    })
  }, [project?.id, project?.updated_at])

  useDomainEvent(
    'verification',
    (event) => { if (event.task_id === task.id) void load() }
  )

  const save = async () => {
    await saveCommands(
      (Object.entries(commands) as Array<[keyof typeof commands, string]>)
        .filter(([, command]) => command.trim())
        .map(([kind, command]) => ({ kind, command: command.trim() }))
    )
  }
  const latest = runs.slice(0, 8)
  return (
    <section className="task-card">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="task-label">독립 검증</div>
          <h3 className="mt-1 text-[14px] font-semibold">Janus 실행기</h3>
          <p className="mt-1 text-[10.5px] text-faint">
            에이전트의 주장은 표시일 뿐입니다. Janus 상태는 관측한 종료 코드로 판정합니다.
          </p>
        </div>
        <button onClick={() => void runAll()} disabled={busy} className="task-primary-action">
          {busy ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          모두 실행
        </button>
      </div>
      <div className="mt-4 grid grid-cols-3 gap-2">
        {(['test', 'lint', 'typecheck'] as const).map((kind) => (
          <label key={kind}>
            <span className="task-label">{{ test: '테스트', lint: '린트', typecheck: '타입 검사' }[kind]}</span>
            <input
              value={commands[kind]}
              onChange={(event) => setCommands({ ...commands, [kind]: event.target.value })}
              placeholder={`${{ test: '테스트', lint: '린트', typecheck: '타입 검사' }[kind]} 명령`}
              className="task-input mt-1 font-mono text-[10px]"
            />
          </label>
        ))}
      </div>
      <div className="mt-2 flex items-center justify-between">
        <code className="truncate text-[10px] text-faint">수용 검증 · {task.acceptance_command}</code>
        <button onClick={() => void save()} disabled={busy} className="task-quiet-action">
          <Check size={11} /> 프로젝트 명령 저장
        </button>
      </div>
      <div className="mt-4 space-y-2 border-t border-border pt-3">
        {latest.map((run) => {
          const running = run.status === 'queued' || run.status === 'running'
          const color = run.status === 'passed'
            ? 'var(--color-ok)'
            : running ? 'var(--color-warn)' : 'var(--color-danger)'
          return (
            <div key={run.id} className="rounded-md border border-border bg-raised/40 px-3 py-2">
              <div className="flex items-center gap-2 text-[10.5px]">
                {running && <Loader2 size={11} className="animate-spin" style={{ color }} />}
                <span className="font-semibold" style={{ color }}>{stateLabel(run.status)}</span>
                <span className="rounded bg-panel px-1.5 py-0.5 font-mono text-[10px] text-muted">{({ test: '테스트', lint: '린트', typecheck: '타입 검사', acceptance: '수용 검증' } as Record<string, string>)[run.kind] ?? run.kind}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-faint">{run.command}</span>
                <span className="font-mono text-[10px] text-faint">
                  {run.duration_ms == null ? '—' : `${Math.round(run.duration_ms)}ms`} · 종료 {run.exit_code ?? '—'}
                </span>
                {!running && (
                  <button onClick={() => void rerun(run.id)} disabled={busy} className="task-quiet-action">
                    <RotateCcw size={10} /> 재실행
                  </button>
                )}
              </div>
              <div className="mt-1 flex gap-4 text-[10px] text-faint">
                <span>에이전트 주장: {run.agent_claim ?? '기록 없음'}</span>
                <span>Janus 결과: <b style={{ color }}>{stateLabel(run.status)}</b></span>
              </div>
              {(run.stdout || run.stderr || run.error) && (
                <pre className="mt-2 max-h-24 overflow-auto whitespace-pre-wrap bg-base p-2 font-mono text-[10px] leading-4 text-muted">
                  {[run.stdout, run.stderr, run.error].filter(Boolean).join('\n')}
                </pre>
              )}
            </div>
          )
        })}
        {latest.length === 0 && (
          <div className="py-3 text-center text-[10.5px] text-faint">아직 독립 검증 실행이 없습니다.</div>
        )}
      </div>
    </section>
  )
}

function ReviewDecisionCard({ task }: { task: Task }) {
  const review = useStore((state) => state.review)
  const decide = useStore((state) => state.decideReview)
  const busy = useStore((state) => state.taskBusy)
  const [message, setMessage] = useState('')
  const unresolved = review?.comments.filter((item) => !item.resolved_at) ?? []
  const unmerged = review?.unmerged.length ?? 0

  return (
    <section className="task-card">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="task-label">검토 결정</div>
          <h3 className="mt-1 text-[14px] font-semibold">
            미해결 {unresolved.length}건 · 미병합 {unmerged}건
          </h3>
          <p className="mt-1 text-[10.5px] text-faint">
            수용은 현재 리비전의 독립 검증을 통과해야 합니다.
          </p>
        </div>
        <span className="font-mono text-[10px] text-faint">{review?.revision.slice(0, 10) ?? '로딩 중'}</span>
      </div>
      <textarea
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        placeholder="일괄 수정 지시"
        rows={2}
        className="task-input mt-3 resize-none"
      />
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          onClick={() => void decide({ decision: 'accept', message })}
          disabled={busy || unresolved.length > 0 || unmerged > 0}
          className="task-primary-action"
        >
          <Check size={11} /> 수용
        </button>
        <button
          onClick={() => void decide({ decision: 'request_changes', message })}
          disabled={busy || unresolved.length === 0 || unmerged > 0}
          className="task-quiet-action"
        >
          <MessageSquare size={11} /> 변경 요청 ({unresolved.length})
        </button>
        <button
          onClick={() => {
            const confirmation = window.prompt(`커밋하지 않은 모든 변경을 폐기하려면 작업 ID를 입력하세요:\n${task.id}`)
            if (confirmation !== task.id || !task.workspace) return
            void decide({
              decision: 'discard', message,
              confirm_workspace_id: task.workspace.id, confirm_discard: confirmation
            })
          }}
          disabled={busy || unmerged > 0}
          className="task-danger-link ml-auto"
          title={unmerged ? '먼저 미병합 변경을 수동으로 해결하세요' : '커밋하지 않은 변경 폐기'}
        >
          <X size={11} /> 변경 폐기…
        </button>
      </div>
      {review?.decisions.length ? (
        <div className="mt-3 border-t border-border pt-2 text-[10px] text-faint">
          최신: {{ accept: '수용', request_changes: '변경 요청', discard: '폐기' }[review.decisions[review.decisions.length - 1].decision] ?? review.decisions[review.decisions.length - 1].decision}
        </div>
      ) : null}
    </section>
  )
}

function TaskShippingCard() {
  const task = useStore((state) => state.task)
  const review = useStore((state) => state.review)
  const changeSet = useStore((state) => state.changeSet)
  const shipments = useStore((state) => state.shipments)
  const handoff = useStore((state) => state.shipHandoff)
  const pullRequestSnapshot = useStore((state) => state.taskPullRequest)
  const busy = useStore((state) => state.taskBusy)
  const commitTask = useStore((state) => state.commitTask)
  const pushTask = useStore((state) => state.pushTask)
  const loadHandoff = useStore((state) => state.loadShipHandoff)
  const createPullRequest = useStore((state) => state.createTaskPullRequest)
  const refreshPullRequest = useStore((state) => state.refreshTaskPullRequest)
  const archiveWorkspace = useStore((state) => state.archiveWorkspace)
  const [message, setMessage] = useState('')
  const [prTitle, setPrTitle] = useState(task?.title ?? '')
  const [prBody, setPrBody] = useState(task?.objective ?? '')
  const [prBase, setPrBase] = useState(task?.base_ref.replace(/^origin\//, '') ?? 'main')
  const [showCreatePr, setShowCreatePr] = useState(false)
  const latestDecision = review?.decisions[review.decisions.length - 1]
  const accepted = Boolean(
    latestDecision?.decision === 'accept' && latestDecision.revision === changeSet?.revision
  )
  const commit = [...shipments].reverse().find(
    (item) => item.action === 'commit' && item.status === 'completed'
  )
  const failedCommit = [...shipments].reverse().find(
    (item) => item.action === 'commit' && item.status === 'failed'
  )
  const pushed = commit && shipments.some(
    (item) => item.action === 'push' && item.status === 'completed'
      && item.commit_sha === commit.commit_sha
  )
  const failedPush = [...shipments].reverse().find(
    (item) => item.action === 'push' && item.status === 'failed'
  )
  const pullRequest = pullRequestSnapshot?.pull_request
  const checksPassed = Boolean(
    pullRequest?.checks.length && pullRequest.checks.every((check) =>
      ['SUCCESS', 'NEUTRAL', 'SKIPPED', 'PASS'].includes(String(check.state ?? check.bucket).toUpperCase())
    )
  )
  const releaseStages = [
    ['커밋', Boolean(commit)], ['푸시', Boolean(pushed)], ['PR', Boolean(pullRequest?.number)],
    ['검사', checksPassed], ['병합', pullRequest?.state === 'merged']
  ] as const

  useEffect(() => {
    if (commit && !handoff) void loadHandoff()
  }, [commit?.id, handoff, loadHandoff])

  return (
    <section className="task-card">
      <div className="task-label">작업 브랜치 출하</div>
      <div className="mt-2 grid grid-cols-5 gap-1.5" aria-label="배포 진행률">
        {releaseStages.map(([label, reached], index) => (
          <div key={label} className="relative">
            <div className="mb-1 flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.1em] text-faint">
              <span className="grid h-4 w-4 place-items-center rounded-full border text-[10px]" style={{
                borderColor: reached ? 'var(--color-ok)' : 'var(--color-border-strong)',
                color: reached ? 'var(--color-ok)' : 'var(--color-faint)'
              }}>{reached ? '✓' : index + 1}</span>
              {label}
            </div>
            <div className="h-px" style={{ background: reached ? 'var(--color-ok)' : 'var(--border-default)' }} />
          </div>
        ))}
      </div>
      <div className="mt-2 flex gap-2">
        <input
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="커밋 메시지"
          className="task-input mt-0 min-w-0 flex-1"
        />
        <button
          onClick={() => void commitTask(message.trim())}
          disabled={busy || !accepted || !message.trim()}
          className="task-primary-action"
        >
          <GitBranch size={11} /> Janus에서 커밋
        </button>
        <button
          onClick={() => void pushTask('origin')}
          disabled={busy || !commit || Boolean(pushed)}
          className="task-quiet-action"
        >
          <Send size={11} /> {pushed ? '푸시됨' : '브랜치 푸시'}
        </button>
      </div>
      <p className="mt-2 text-[10px] text-faint">
        Janus는 선택한 원본 저장소의 현재 브랜치에서 직접 커밋합니다.
      </p>
      {failedCommit && !commit && (
        <div className="error-strip mt-2 text-[10px]">
          커밋 실패 · {failedCommit.error}. 작업 공간의 변경은 그대로입니다. Git 신원 또는 디스크 접근 문제를 해결한 뒤 재시도하세요.
        </div>
      )}
      {failedPush && !pushed && (
        <div className="error-strip mt-2 text-[10px]">
          푸시 실패 · {failedPush.error}. 커밋과 작업 브랜치는 유지됩니다. 원격 접근 문제를 해결한 뒤 재시도하세요.
        </div>
      )}
      {commit && (
        <div className="mt-3 rounded-md border border-border bg-raised/40 p-2.5">
          <div className="flex items-center gap-2 text-[10px]">
            <span className="text-ok">커밋됨</span>
            <code className="text-muted">{commit.commit_sha.slice(0, 12)}</code>
            <code className="min-w-0 flex-1 truncate text-faint">{commit.branch_name}</code>
          </div>
          {handoff?.local_apply_command && (
            <div className="mt-2 flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate bg-base px-2 py-1.5 text-[10px] text-faint">
                {handoff.local_apply_command}
              </code>
              <button
                onClick={() => {
                  const command = handoff?.local_apply_command
                  if (command) void navigator.clipboard.writeText(command)
                }}
                className="task-quiet-action"
              >
                cherry-pick 복사
              </button>
            </div>
          )}
        </div>
      )}

      {pushed && !pullRequest?.number && (
        <div className="mt-3 border-t border-border pt-3">
          {!showCreatePr ? (
            <button onClick={() => setShowCreatePr(true)} className="task-primary-action">
              <GitPullRequest size={11} /> GitHub PR 생성
            </button>
          ) : (
            <div className="border border-border-strong bg-panel p-3">
              <div className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-secondary">
                검토 경계 게시
              </div>
              <div className="grid grid-cols-[1fr_110px] gap-2">
                <input value={prTitle} onChange={(event) => setPrTitle(event.target.value)} className="task-input mt-0" placeholder="PR 제목" />
                <input value={prBase} onChange={(event) => setPrBase(event.target.value)} className="task-input mt-0 font-mono" placeholder="기준 브랜치" />
              </div>
              <textarea value={prBody} onChange={(event) => setPrBody(event.target.value)} rows={3} className="task-input mt-2 resize-none" placeholder="변경 내용과 검증 방법" />
              <div className="mt-2 flex justify-end gap-2">
                <button onClick={() => setShowCreatePr(false)} className="task-quiet-action">취소</button>
                <button
                  onClick={() => void createPullRequest({ title: prTitle.trim(), body: prBody, base: prBase.trim(), draft: false })}
                  disabled={busy || !prTitle.trim() || !prBase.trim()}
                  className="task-primary-action"
                >
                  <GitPullRequest size={11} /> PR 생성
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {pullRequest && (
        <div className="mt-3 overflow-hidden border border-border bg-panel">
          <div className="flex items-start justify-between gap-3 border-b border-border px-3 py-2.5">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <GitPullRequest size={12} className="text-secondary" />
                <span className="font-mono text-[10px] text-faint">PR #{pullRequest.number ?? '—'}</span>
                <span className="rounded-full border px-1.5 py-0.5 font-mono text-[10px] uppercase" style={{
                  color: pullRequest.state === 'merged' ? 'var(--color-ok)' : pullRequest.state === 'error' ? 'var(--color-danger)' : 'var(--color-accent-fg)',
                  borderColor: pullRequest.state === 'merged' ? 'var(--success)' : pullRequest.state === 'error' ? 'var(--danger)' : 'var(--border-strong)'
                }}>{stateLabel(pullRequest.state)}</span>
              </div>
              <div className="mt-1 truncate text-[11px] font-medium">{pullRequest.title}</div>
              <div className="mt-1 font-mono text-[10px] text-faint">
                {pullRequest.head_branch} → {pullRequest.base_branch} · {pullRequest.merge_state ? stateLabel(pullRequest.merge_state) : '원격 대기 중'} · {pullRequest.review_decision ?? '검토 결정 없음'}
              </div>
            </div>
            <div className="flex shrink-0 gap-1">
              <button onClick={() => void refreshPullRequest()} disabled={busy || !pullRequest.number} className="task-quiet-action">
                <RefreshCw size={10} /> 새로고침
              </button>
              {pullRequest.url && (
                <button onClick={() => window.open(pullRequest.url!, '_blank', 'noopener,noreferrer')} className="task-quiet-action">
                  <ExternalLink size={10} /> 열기
                </button>
              )}
            </div>
          </div>

          {pullRequest.error && (
            <div className="border-b border-danger bg-panel px-3 py-2 text-[10px] text-danger">
              동기화 실패 · {pullRequest.error}. 저장된 PR과 CI 데이터는 유지됩니다.
            </div>
          )}
          <div className="grid grid-cols-2 gap-px bg-border">
            <div className="bg-panel p-3">
              <div className="task-label">CI 검사 · {pullRequest.checks.length}</div>
              <div className="mt-2 space-y-1.5">
                {pullRequest.checks.map((check) => {
                  const passed = ['SUCCESS', 'NEUTRAL', 'SKIPPED', 'PASS'].includes(String(check.state ?? check.bucket).toUpperCase())
                  return (
                    <div key={`${check.workflow}-${check.name}`} className="flex items-center justify-between gap-2 text-[10px]">
                      <span className="truncate text-muted">{check.workflow ? `${check.workflow} / ` : ''}{check.name}</span>
                      <span className={passed ? 'text-ok' : String(check.bucket).toLowerCase() === 'pending' ? 'text-warn' : 'text-danger'}>
                        {stateLabel(String(check.state ?? check.bucket ?? 'unknown'))}
                      </span>
                    </div>
                  )
                })}
                {pullRequest.checks.length === 0 && <div className="text-[10px] text-faint">보고된 검사가 없습니다.</div>}
              </div>
            </div>
            <div className="bg-panel p-3">
              <div className="task-label">실패 로그 · {pullRequest.failed_logs.length}</div>
              <div className="mt-2 space-y-1.5">
                {pullRequest.failed_logs.map((failure) => (
                  <details key={failure.run_id} className="border border-danger bg-base px-2 py-1.5">
                    <summary className="cursor-pointer truncate text-[10px] text-danger">{failure.name} · {failure.conclusion}</summary>
                    <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap font-mono text-[10px] leading-relaxed text-muted">{failure.log || '실패 로그 출력이 없습니다.'}</pre>
                    {failure.truncated && <div className="mt-1 text-[10px] text-warn">영속화 안전 한도에서 로그가 잘렸습니다.</div>}
                  </details>
                ))}
                {pullRequest.failed_logs.length === 0 && <div className="text-[10px] text-faint">실패한 워크플로 로그가 없습니다.</div>}
              </div>
            </div>
          </div>
          {pullRequestSnapshot?.archive_reason && (
            <div className="flex items-center justify-between gap-3 border-t border-border px-3 py-2.5 text-[10px]">
              <span className={pullRequestSnapshot.archive_recommended ? 'text-ok' : 'text-faint'}>
                {pullRequestSnapshot.archive_reason}. 로컬 브랜치는 보존됩니다.
              </span>
              {pullRequestSnapshot.archive_recommended && (
                <button onClick={() => void archiveWorkspace(false)} disabled={busy} className="task-quiet-action">
                  <Archive size={10} /> 작업 공간 보관
                </button>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  )
}

type TaskView = 'conversation' | 'workspace' | 'changes' | 'verification' | 'review' | 'ship' | 'development' | 'context'

type RuntimeWorkerState =
  | 'queued'
  | 'running'
  | 'waiting_approval'
  | 'stopping'
  | 'partial'
  | 'success'
  | 'error'
  | 'suppressed'

type RuntimeWorker = {
  id: string
  name: string
  role: string
  state: RuntimeWorkerState
  reason?: string
  error?: string
  startedAt?: string
  endedAt?: string
  durationMs?: number
}

const WORKER_STATE_META: Record<RuntimeWorkerState, { glyph: string, label: string, tone: string }> = {
  queued: { glyph: '◷', label: '모델 대기', tone: 'var(--warning)' },
  running: { glyph: '●', label: '실행 중', tone: 'var(--accent)' },
  waiting_approval: { glyph: '!', label: '승인 대기', tone: 'var(--warning)' },
  stopping: { glyph: '◌', label: '중지 중', tone: 'var(--warning)' },
  partial: { glyph: '◐', label: '부분 완료', tone: 'var(--warning)' },
  success: { glyph: '✓', label: '완료', tone: 'var(--success)' },
  error: { glyph: '×', label: '실패', tone: 'var(--danger)' },
  suppressed: { glyph: '—', label: '억제', tone: 'var(--warning)' }
}

/** 워커 활동은 대화창에서 걸러진다(worker_id가 있으면 숨김) — 여기서만 볼 수 있다. */
function useWorkerActivity(workerId: string | null) {
  const events = useStore((state) => state.taskSessionEvents)
  return useMemo(() => {
    const calls: { key: string, tool: string, detail: string }[] = []
    let reasoning = ''
    let answer = ''
    let task = ''
    if (!workerId) return { reasoning, answer, calls, task }
    for (const event of events) {
      if (event.kind !== 'agent_event') continue
      if (String(event.payload.worker_id ?? '') !== workerId) continue
      const kind = String(event.payload.kind ?? '')
      const text = String(event.payload.text ?? '')
      if (kind === 'reasoning_delta') reasoning += text
      else if (kind === 'text_delta') answer += text
      else if (kind === 'assistant') answer = String(event.payload.content ?? answer)
      else if (kind === 'worker_task') task = String(event.payload.task ?? task)
      else if (kind.startsWith('tool_')) {
        if (kind !== 'tool_start') continue
        calls.push({
          key: String(event.seq),
          tool: String(event.payload.name ?? event.payload.tool ?? '도구'),
          detail: JSON.stringify(event.payload.args ?? {}).slice(0, 240)
        })
      }
    }
    return { reasoning, answer, calls, task }
  }, [events, workerId])
}

const RUNNING_STATES = new Set<RuntimeWorkerState>(['queued', 'running', 'waiting_approval', 'stopping'])

/** 실행 중인 워커는 값이 멈춰 있으면 안 된다 — 끝날 때까지 1초마다 다시 센다. */
function useElapsedLabel(worker: RuntimeWorker | null): string | null {
  const running = Boolean(worker && RUNNING_STATES.has(worker.state))
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setTick((value) => value + 1), 1000)
    return () => clearInterval(timer)
  }, [running])
  if (!worker?.startedAt) return null
  const started = Date.parse(worker.startedAt)
  if (Number.isNaN(started)) return null
  const ended = worker.endedAt ? Date.parse(worker.endedAt) : null
  const ms = worker.durationMs
    ?? ((ended !== null && !Number.isNaN(ended) ? ended : Date.now()) - started)
  if (ms < 0) return null
  const seconds = Math.floor(ms / 1000)
  if (seconds < 60) return `${seconds}초`
  return `${Math.floor(seconds / 60)}분 ${String(seconds % 60).padStart(2, '0')}초`
}

function WorkerDetailModal({ worker, onClose }: { worker: RuntimeWorker, onClose: () => void }) {
  const { reasoning, answer, calls, task } = useWorkerActivity(
    worker.state === 'suppressed' ? null : worker.id
  )
  const elapsed = useElapsedLabel(worker)
  const meta = WORKER_STATE_META[worker.state]
  // 승인을 기다리는 워커는 여기서 바로 풀 수 있어야 한다 — 대화 화면까지 돌아가
  // 카드를 찾아야 한다면, 워커는 답 없이 APPROVAL_TIMEOUT을 그대로 태운다.
  const pending = useStore((state) => state.taskApprovals).filter(
    (item) => item.node_id === worker.id
  )
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const empty = !reasoning && !answer && calls.length === 0 && !worker.error && !worker.reason && pending.length === 0
  return (
    <div className="worker-modal__backdrop" role="presentation" onClick={onClose}>
      <div
        className="worker-modal"
        role="dialog"
        aria-modal="true"
        aria-label={`워커 ${worker.name}`}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="worker-modal__header">
          <div className="min-w-0">
            <strong>{worker.name}</strong>
            <small>{worker.role}</small>
          </div>
          <span className="worker-modal__badge" style={{ color: meta.tone }}>
            {meta.glyph} {meta.label}
          </span>
          {elapsed && <em className="worker-modal__elapsed">{elapsed}</em>}
          <button type="button" onClick={onClose} aria-label="닫기" className="worker-modal__close">
            <X size={14} />
          </button>
        </header>
        <div className="worker-modal__body">
          {pending.map((approval) => (
            <ApprovalCard key={approval.id} approval={approval} variant="worker" />
          ))}
          {task && (
            <div className="worker-modal__turn worker-modal__turn--task">
              <small>지시</small>
              <p>{task}</p>
            </div>
          )}
          {reasoning && (
            <div className="worker-modal__turn worker-modal__turn--reasoning">
              <small>사고</small>
              <p>{reasoning}</p>
            </div>
          )}
          {calls.map((call) => (
            <div key={call.key} className="worker-modal__turn worker-modal__turn--tool">
              <small>{call.tool}</small>
              <code>{call.detail}</code>
            </div>
          ))}
          {answer && (
            <div className="worker-modal__turn worker-modal__turn--answer">
              <small>답</small>
              <p>{answer}</p>
            </div>
          )}
          {worker.reason && (
            <div className="worker-modal__turn worker-modal__turn--note">
              <small>사유</small>
              <p>{worker.reason}</p>
            </div>
          )}
          {worker.error && (
            <div className="worker-modal__turn worker-modal__turn--error">
              <small>오류</small>
              <p>{worker.error}</p>
            </div>
          )}
          {empty && <p className="worker-modal__empty">아직 이 워커의 활동 기록이 없습니다.</p>}
        </div>
      </div>
    </div>
  )
}

function RuntimeWorkerGraph({
  task,
  verificationStatus
}: {
  task: Task
  verificationStatus?: string
}) {
  const session = useStore((state) => state.taskSession)
  const connected = useStore((state) => state.taskConnected)
  const events = useStore((state) => state.taskSessionEvents)
  const [selectedWorkerId, setSelectedWorkerId] = useState<string | null>(null)
  const workers = useMemo(() => {
    const spans = new Map<string, RuntimeWorker>()
    const suppressed: RuntimeWorker[] = []
    for (const event of events) {
      if (event.kind === 'span_start' || event.kind === 'span_end') {
        const raw = event.payload.span
        if (!raw || typeof raw !== 'object') continue
        const span = raw as Span
        if (!span.node_id || span.node_id === 'orchestrator') continue
        const known = spans.get(span.node_id)
        spans.set(span.node_id, {
          ...known,
          id: span.node_id,
          name: span.label ?? span.node_id,
          role: '워커',
          state: span.status,
          startedAt: known?.startedAt ?? event.created_at,
          endedAt: event.kind === 'span_end' ? event.created_at : known?.endedAt,
          durationMs: span.duration_ms ?? known?.durationMs
        })
      }
      if (event.kind === 'agent_event' && event.payload.kind === 'worker_spawn_suppressed') {
        suppressed.push({
          id: `suppressed-${event.seq}`,
          name: String(event.payload.name ?? `요청 ${suppressed.length + 1}`),
          role: String(event.payload.role ?? '워커'),
          state: 'suppressed',
          reason: String(event.payload.reason ?? '정책 억제'),
          startedAt: event.created_at
        })
      }
      if (event.kind === 'agent_event' && event.payload.kind === 'worker_state') {
        const workerId = String(event.payload.worker_id ?? '')
        const previous = spans.get(workerId)
        if (!workerId || !previous) continue
        const rawStatus = String(event.payload.status ?? '')
        const state: RuntimeWorkerState =
          rawStatus === 'queued' ? 'queued'
            : rawStatus === 'waiting_approval' ? 'waiting_approval'
              : rawStatus === 'stopping' ? 'stopping'
                : rawStatus === 'completed_partial' ? 'partial'
                  : rawStatus === 'completed' ? 'success'
                    : rawStatus === 'failed' || rawStatus === 'cancelled' ? 'error'
                      : 'running'
        spans.set(workerId, {
          ...previous,
          state,
          error: typeof event.payload.error === 'string' ? event.payload.error : previous.error
        })
      }
    }
    return [...spans.values(), ...suppressed].slice(-6)
  }, [events])

  const selected = workers.find((worker) => worker.id === selectedWorkerId) ?? null
  return (
    <div className="runtime-graph" aria-label="Janus 실행 흐름">
      <div className="runtime-graph__request" title={task.objective}>
        <span>01</span>
        <div>
          <small>요청</small>
          <strong>{task.title}</strong>
        </div>
      </div>
      <div className="runtime-graph__root">
        <span className={connected ? 'text-ok' : 'text-muted'}>{connected ? '●' : '○'}</span>
        <div>
          <small>오케스트레이터</small>
          <strong>JANUS</strong>
        </div>
        <em>{session ? stateLabel(session.status) : '세션 전'}</em>
      </div>
      {workers.length ? (
        <div className="runtime-graph__workers">
          {workers.map((worker) => {
            const meta = WORKER_STATE_META[worker.state]
            return (
              <button
                type="button"
                className="runtime-graph__worker"
                key={worker.id}
                title={worker.reason}
                aria-label={`워커 ${worker.name} 상세`}
                onClick={() => setSelectedWorkerId(worker.id)}
              >
                <span style={{ color: meta.tone }}>{meta.glyph}</span>
                <div className="min-w-0">
                  <strong>{worker.name}</strong>
                  <small>{worker.role}</small>
                </div>
                <em style={{ color: meta.tone }}>{meta.label}</em>
              </button>
            )
          })}
        </div>
      ) : (
        <div className="runtime-graph__empty">워커가 시작되면 이 축에 표시됩니다.</div>
      )}
      {selected && (
        <WorkerDetailModal worker={selected} onClose={() => setSelectedWorkerId(null)} />
      )}
      <div className="runtime-graph__outcome" data-ready={Boolean(verificationStatus)}>
        <span>{verificationStatus ? '✓' : '○'}</span>
        <div>
          <small>결과</small>
          <strong>{verificationStatus ? `검증 ${stateLabel(verificationStatus)}` : '검증 대기'}</strong>
        </div>
      </div>
    </div>
  )
}

function TaskContextPanel({ task, view, onView }: { task: Task; view: TaskView; onView: (view: TaskView) => void }) {
  const changeSet = useStore((state) => state.changeSet)
  const verificationRuns = useStore((state) => state.verificationRuns)
  const review = useStore((state) => state.review)
  const shipments = useStore((state) => state.shipments)
  const session = useStore((state) => state.taskSession)
  const changedFiles = changeSet ? Object.values(changeSet.counts).reduce((total, count) => total + count, 0) : 0
  const latestVerification = verificationRuns.at(0)
  const includedSources = session?.context?.items.filter((item) => item.status === 'included').length ?? 0
  const activeSkills = session?.skills?.filter((skill) => skill.activation_mode !== 'off').length ?? 0

  const row = (target: TaskView, icon: ReactNode, label: string, value?: ReactNode) => (
    <button className="context-panel-row" aria-current={view === target ? 'page' : undefined} onClick={() => onView(target)}>
      <span>{icon}</span>
      <strong>{label}</strong>
      {value && <em>{value}</em>}
    </button>
  )

  return (
    <aside id="task-context-panel" className="task-context-panel" aria-label="실행 컨텍스트">
      <div className="task-context-panel__title">
        <span>환경</span>
        <Status tone={task.workspace?.state === 'ready' ? 'success' : 'muted'}>
          {task.workspace?.state === 'ready' ? '로컬' : '준비 중'}
        </Status>
      </div>
      <section className="task-context-panel__environment">
        {changedFiles > 0 && row('changes', <GitCompareArrows size={16} />, '변경 사항', `${changedFiles}개`)}
        {row('workspace', <Laptop size={16} />, '로컬', task.workspace?.state ? stateLabel(task.workspace.state) : '미생성')}
        {row('workspace', <GitBranch size={14} />, task.workspace?.branch_name ?? task.base_ref)}
        {latestVerification && row('verification', <ShieldCheck size={16} />, '검증', stateLabel(latestVerification.status))}
        {shipments.length > 0 && row('ship', <GitPullRequest size={16} />, '커밋 또는 푸시', `${shipments.length}`)}
      </section>
      <section>
        <div className="task-context-panel__section-label">하위 에이전트</div>
        <RuntimeWorkerGraph task={task} verificationStatus={latestVerification?.status} />
      </section>
      <section>
        <div className="task-context-panel__section-label">출처</div>
        {includedSources > 0 && row('context', <CircleDot size={16} />, '컨텍스트 출처', `${includedSources}개`)}
        {activeSkills > 0 && row('context', <Settings2 size={16} />, '활성 스킬', `${activeSkills}개`)}
        {(review?.comments.length ?? 0) > 0 && row('review', <MessageSquare size={16} />, '검토 의견', `${review?.comments.length}`)}
        {row('development', <Settings2 size={16} />, '개발 도구')}
      </section>
      <div className="task-context-panel__meta">
        <span>{task.workspace?.root_path ?? '작업 공간 준비 전'}</span>
      </div>
    </aside>
  )
}

function TaskDetail({ task }: { task: Task }) {
  const archiveTask = useStore((state) => state.archiveTask)
  const busy = useStore((state) => state.taskBusy)
  const session = useStore((state) => state.taskSession)
  const events = useStore((state) => state.taskSessionEvents)
  const canArchiveTask = !task.workspace || task.workspace.state === 'archived'
  const [confirmArchive, setConfirmArchive] = useState(false)
  const [view, setView] = useState<TaskView>('conversation')

  useEffect(() => setView('conversation'), [task.id])

  const workspaceReady = task.workspace?.state === 'ready'
  const unavailable = (title: string) => (
    <div className="grid h-full place-items-center px-8 text-center">
      <EmptyState
        title={`${title} 준비 중`}
        description="작업 공간이 준비되면 이 화면에서 확인할 수 있습니다."
      />
    </div>
  )

  return (
    <>
    <main className="task-chat-shell min-w-0 flex-1">
      <header className="task-chat-header">
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <StatusBadge task={task} />
            <h1 className="truncate text-[13px] font-medium text-fg">{task.title}</h1>
          </div>
          <div className="mt-1 flex min-w-0 items-center gap-2 font-mono text-[10px] text-faint">
            <span className="truncate">{task.workspace?.branch_name ?? task.base_ref}</span>
            <span>·</span>
            <span className="truncate">{task.workspace?.root_path ?? task.id}</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {view !== 'conversation' && (
            <Button onClick={() => setView('conversation')} variant="ghost" compact>
              <MessageSquare size={12} /> 대화로 돌아가기
            </Button>
          )}
          <Button
            onClick={() => setConfirmArchive(true)}
            disabled={busy || !canArchiveTask}
            title={canArchiveTask ? '작업 보관' : '먼저 작업 공간을 보관하세요'}
            variant="ghost"
            compact
          >
            <Archive size={12} /> 작업 보관
          </Button>
        </div>
      </header>
      <div className={`task-chat-view task-chat-view--${view}`}>
        {view === 'conversation' && <TaskRuntimeCard task={task} />}
        {view === 'workspace' && (
          <div className="task-secondary-view">
            <section className="task-card">
              <div className="task-label">목표</div>
              <p className="mt-2 whitespace-pre-wrap text-[13px] leading-6 text-fg">{task.objective}</p>
              <div className="mt-4 grid grid-cols-[1fr_150px] gap-4 border-t border-border-subtle pt-4">
                <div>
                  <div className="task-label">수용 검증</div>
                  <code className="mt-1.5 block border border-border bg-base px-3 py-2 font-mono text-[10.5px] text-secondary">
                    {task.acceptance_command}
                  </code>
                </div>
                <div>
                  <div className="task-label">기준 리프</div>
                  <div className="mt-1.5 flex items-center gap-1.5 border border-border px-3 py-2 font-mono text-[10.5px] text-muted">
                    <GitBranch size={11} /> {task.base_ref}
                  </div>
                </div>
              </div>
            </section>
            <WorkspaceCard task={task} />
          </div>
        )}
        {view === 'changes' && (workspaceReady ? <div className="task-secondary-view"><ChangeSetCard /></div> : unavailable('변경'))}
        {view === 'verification' && (workspaceReady ? <div className="task-secondary-view"><VerificationCard task={task} /></div> : unavailable('검증'))}
        {view === 'review' && (workspaceReady ? <div className="task-secondary-view"><ReviewDecisionCard task={task} /></div> : unavailable('검토'))}
        {view === 'ship' && (workspaceReady ? <div className="task-secondary-view"><TaskShippingCard /></div> : unavailable('출하'))}
        {view === 'context' && (session ? <ContextInspector session={session} events={events} /> : unavailable('컨텍스트'))}
        {view === 'development' && (workspaceReady ? (
          <Suspense fallback={<section className="task-card text-[10px] text-faint">작업 개발 화면 로딩 중…</section>}>
            <TaskDevelopmentSurface task={task} />
          </Suspense>
        ) : unavailable('개발'))}
      </div>
      <ConfirmDialog
        open={confirmArchive}
        title={`“${task.title}” 작업을 보관할까요?`}
        description="보관된 작업은 활성 작업 목록에서 숨겨집니다."
        confirmLabel="작업 보관"
        danger
        onClose={() => setConfirmArchive(false)}
        onConfirm={() => { setConfirmArchive(false); void archiveTask() }}
      />
    </main>
    {/* 계약 §7: 인스펙터는 검사할 객체가 있을 때만 — 세션도 작업 공간도 없으면 빈 패널을 세우지 않는다. */}
    {(session || task.workspace) && <TaskContextPanel task={task} view={view} onView={setView} />}
    </>
  )
}

function EmptyTaskState({ hasProject, onOpenSettings }: {
  hasProject: boolean
  onOpenSettings?: () => void
}) {
  const addProject = useStore((state) => state.addProjectFromPicker)
  const modelBlocked = useLocalModelBlock()
  // 신규 사용자가 반드시 지나는 화면이다. 모델이 없으면 저장소보다 그게 먼저다 —
  // 전에는 여기서 모델을 한 번도 언급하지 않아 위임이 무반응인 이유를 알 수 없었다.
  if (modelBlocked && onOpenSettings) {
    return (
      <div className="workspace-surface grid min-w-0 flex-1 place-items-center px-8 text-center">
        <EmptyState
          symbol={<Laptop size={20} strokeWidth={1.5} />}
          title="로컬 모델 준비가 필요합니다"
          description={modelBlocked}
          action={<Button variant="primary" onClick={onOpenSettings}>
            <Settings2 size={13} /> 설정 열기
          </Button>}
        />
      </div>
    )
  }
  return (
    <div className="workspace-surface grid min-w-0 flex-1 place-items-center px-8 text-center">
      <EmptyState
        title={hasProject ? 'Janus에게 목표를 위임하세요' : '로컬 Git 저장소 추가'}
        description={hasProject
          ? 'Janus가 내부 작업 계약과 검증 경계를 만들고 격리된 로컬 에이전트 실행을 시작합니다.'
          : 'Janus는 이 저장소의 현재 브랜치에서 직접 작업합니다 — 변경은 커밋 전까지 작업 트리에 남습니다.'}
        action={hasProject ? undefined : <Button onClick={addProject}><FolderGit2 size={13} /> 저장소 추가</Button>}
      />
    </div>
  )
}

/** 이벤트 유실 보정 대상 — 준비 과도 상태이거나 위임 자동 시작을 기다리는 중. */
export function awaitsPreparation(
  task: { id: string; status: string; workspace?: { state: string } | null } | null,
  pendingDelegationTaskId: string | null,
  hasSession: boolean
): boolean {
  if (!task) return false
  if (task.status === 'preparing' || task.workspace?.state === 'preparing') return true
  return pendingDelegationTaskId === task.id && !hasSession
}

export default function TaskWorkspace({
  newConversation,
  onNewConversationChange,
  onOpenSettings
}: {
  newConversation: boolean
  onNewConversationChange: (value: boolean) => void
  onOpenSettings?: () => void
}) {
  const projects = useStore((state) => state.projects)
  const projectId = useStore((state) => state.projectId)
  const task = useStore((state) => state.task)
  const previousTaskId = useRef(task?.id)
  const sidebarTab = useStore((state) => state.sidebarTab)
  const openedFile = useStore((state) => state.openedFile)
  const refresh = useStore((state) => state.refreshSelectedTask)
  const error = useStore((state) => state.taskActionError)
  const clearError = useStore((state) => state.clearTaskError)
  const pendingDelegation = useStore((state) => state.pendingDelegation)
  const session = useStore((state) => state.taskSession)
  const taskSocket = useStore((state) => state.taskWs)
  const connected = useStore((state) => state.taskConnected)
  const runtimeError = useStore((state) => state.taskRuntimeError)
  const busy = useStore((state) => state.taskBusy)
  const mlxUp = useStore((state) => state.mlxUp)
  const agentProfilesForGate = useStore((state) => state.agentProfiles)
  const modelProfilesForGate = useStore((state) => state.modelProfiles)
  const selectedProfileForGate = useStore((state) => state.selectedAgentProfileId)
  // 구독형(CLI) 모델은 로컬 MLX 서버가 없어도 실행된다 — 로컬 프로바이더만 게이트.
  const gateProvider = modelProfilesForGate.find((model) =>
    model.id === agentProfilesForGate.find((profile) => profile.id === selectedProfileForGate)?.model_profile_id
  )?.provider ?? 'local'
  const modelReady = gateProvider === 'local' ? Boolean(mlxUp) : true
  const startTaskSession = useStore((state) => state.startTaskSession)
  const resumeTaskSession = useStore((state) => state.resumeTaskSession)
  const project = useMemo(
    () => projects.find((item) => item.id === projectId) ?? null,
    [projects, projectId]
  )

  useDomainEvent(
    'workspace',
    (event) => { if (event.task_id === task?.id) void refresh() }
  )

  // workspace ready 이벤트는 재생되지 않는다 — 빠른 준비(실측 52ms)가 이벤트 소켓
  // 핸드셰이크보다 먼저 끝나면 이벤트를 놓쳐 화면이 preparing에 영원히 갇힌다.
  // 과도 상태 동안만 1초 폴링으로 수렴시킨다. 이벤트는 가속기, 폴링이 보증.
  useEffect(() => {
    if (!awaitsPreparation(task, pendingDelegation?.taskId ?? null, Boolean(session))) return
    const id = window.setInterval(() => void refresh(), 1000)
    return () => window.clearInterval(id)
  }, [task, pendingDelegation, session, refresh])

  useEffect(() => {
    if (
      !pendingDelegation || pendingDelegation.taskId !== task?.id ||
      task.workspace?.state !== 'ready' || session || busy || !modelReady
    ) return
    void startTaskSession({ initialMessage: pendingDelegation.objective })
  }, [pendingDelegation, task, session, busy, modelReady, startTaskSession])

  useEffect(() => {
    if (
      pendingDelegation || !session || taskSocket || connected || busy || !modelReady || runtimeError ||
      (session.status !== 'created' && session.status !== 'idle')
    ) return
    void resumeTaskSession()
  }, [pendingDelegation, session, taskSocket, connected, busy, modelReady, runtimeError, resumeTaskSession])

  useEffect(() => {
    if (previousTaskId.current !== task?.id) onNewConversationChange(false)
    previousTaskId.current = task?.id
  }, [task?.id, onNewConversationChange])

  return (
    <>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="relative flex min-h-0 flex-1">
        {sidebarTab === 'files' && openedFile ? (
          <Suspense fallback={<div className="grid flex-1 place-items-center text-[11px] text-faint">파일 뷰어 로딩 중…</div>}>
            <FileView />
          </Suspense>
        ) : sidebarTab === 'tasks' && project && (!task || newConversation) ? (
          <DelegationBar project={project} />
        ) : task && !newConversation ? (
          <TaskDetail task={task} />
        ) : (
          <EmptyTaskState hasProject={Boolean(project)} onOpenSettings={onOpenSettings} />
        )}
        {error && (
          <div className="toast-error">
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-danger" />
            <span className="text-[10.5px] leading-relaxed text-danger">{error}</span>
            <button onClick={clearError} className="ml-2 text-danger/70 hover:text-danger">
              <X size={13} />
            </button>
          </div>
        )}
        </div>
      </div>
    </>
  )
}
