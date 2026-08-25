import { FormEvent, useEffect, useMemo, useState } from 'react'
import {
  Bot, Cpu, FolderPlus, Github, GitCommitHorizontal, Loader2, LockKeyhole,
  PackageOpen, Search, ShieldAlert, X,
} from 'lucide-react'
import type { SkillActivationMode, SkillSummary } from '../types'
import { useStore } from '../store'
import { Button, Dialog, EmptyState, IconButton, Input, Section, Select, Status } from './ui'

const compatibilityLabel = {
  native: '네이티브',
  partial: '부분 호환',
  adapter_required: '어댑터 필요',
  blocked: '차단됨',
} as const

function sourceLabel(skill: SkillSummary): string {
  if (skill.source_kind === 'github') return 'GitHub'
  if (skill.source_kind === 'claude') return 'Claude'
  if (skill.source_kind === 'codex') return 'Codex'
  if (skill.source_kind === 'project') return '프로젝트'
  if (skill.source_kind === 'janus') return 'Janus'
  return '로컬'
}

export default function SkillLibrary() {
  const skills = useStore((state) => state.skills)
  const assignments = useStore((state) => state.agentProfileSkills)
  const busy = useStore((state) => state.skillBusy)
  const error = useStore((state) => state.skillError)
  const importPreview = useStore((state) => state.skillImportPreview)
  const previewGithub = useStore((state) => state.previewGithubSkills)
  const confirmGithub = useStore((state) => state.confirmGithubSkills)
  const dismissPreview = useStore((state) => state.dismissSkillPreview)
  const importLocal = useStore((state) => state.importLocalSkills)
  const setSkill = useStore((state) => state.setAgentProfileSkill)
  const [githubUrl, setGithubUrl] = useState('')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selectedImports, setSelectedImports] = useState<string[]>([])

  useEffect(() => {
    setSelectedImports(importPreview?.skills
      .filter((skill) => skill.compatibility !== 'blocked')
      .map((skill) => skill.source_subpath) ?? [])
  }, [importPreview])

  const assignmentBySkill = useMemo(
    () => new Map(assignments.map((item) => [item.skill_id, item])),
    [assignments],
  )
  const filtered = skills.filter((skill) => {
    const needle = query.trim().toLowerCase()
    return !needle || `${skill.namespace} ${skill.name} ${skill.description}`.toLowerCase().includes(needle)
  })
  const selected = skills.find((skill) => skill.id === selectedId) ?? filtered[0] ?? null
  const activeCount = assignments.filter((item) => item.activation_mode !== 'off').length
  const estimatedTokens = assignments.reduce((total, item) => (
    item.activation_mode === 'off' ? total : total + Number(item.report?.estimated_prompt_tokens ?? 0)
  ), 0)

  const submitGithub = (event: FormEvent) => {
    event.preventDefault()
    if (!githubUrl.trim()) return
    void previewGithub(githubUrl)
  }

  const updateMode = (skill: SkillSummary, mode: SkillActivationMode) => {
    if (skill.compatibility === 'blocked' || skill.compatibility === 'adapter_required') return
    void setSkill(skill.id, mode)
  }

  return (
    <section className="workspace-surface">
      <div className="skill-import-bar">
        <form onSubmit={submitGithub} className="skill-import-form">
          <Github size={13} className="ml-3 shrink-0 text-faint" />
          <input
            value={githubUrl}
            onChange={(event) => setGithubUrl(event.target.value)}
            placeholder="https://github.com/owner/skill-repository"
            aria-label="GitHub 스킬 저장소 URL"
          />
          <Button
            variant="primary"
            compact
            type="submit"
            disabled={busy || !githubUrl.trim()}
            className="m-0.5"
          >
            {busy ? <Loader2 size={12} className="animate-spin" /> : 'GitHub 추가'}
          </Button>
        </form>
        <Button
          onClick={() => void importLocal()}
          disabled={busy}
        >
          <FolderPlus size={13} strokeWidth={1.5} /> 폴더 추가
        </Button>
        <Status tone={activeCount ? 'success' : 'muted'}>{activeCount}개 활성</Status>
      </div>

      {error && (
        <div className="error-strip flex items-start gap-2">
          <ShieldAlert size={12} className="mt-0.5 shrink-0" /> {error}
        </div>
      )}

      {importPreview && (
        <Dialog open title="가져올 GitHub 스킬 확인" onClose={dismissPreview}>
            <header className="flex items-start gap-3 border-b border-border px-5 py-4">
              <div className="workspace-toolbar__icon">
                <Github size={16} strokeWidth={1.5} />
              </div>
              <div className="min-w-0 flex-1">
                <h3 id="skill-import-title" className="text-[13px] font-medium">가져올 GitHub 스킬 확인</h3>
                <p className="mt-0.5 truncate font-mono text-[10px] text-faint" title={importPreview.source}>{importPreview.source}</p>
              </div>
              <IconButton onClick={dismissPreview} disabled={busy} label="미리보기 닫기"><X size={15} strokeWidth={1.5} /></IconButton>
            </header>

            <div className="flex items-center gap-4 border-b border-border bg-bg px-5 py-2.5 text-[10px] text-muted">
              <span className="flex items-center gap-1.5"><GitCommitHorizontal size={11} /> <span className="font-mono">{importPreview.revision.slice(0, 12)}</span>로 고정</span>
              <span className="flex items-center gap-1.5"><LockKeyhole size={11} /> 라이선스 {importPreview.license || '확인되지 않음'}</span>
              <span className="ml-auto">{importPreview.skills.length}개 발견</span>
            </div>

            <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-4">
              {importPreview.skills.map((skill) => {
                const checked = selectedImports.includes(skill.source_subpath)
                const blocked = skill.compatibility === 'blocked'
                return (
                  <label key={skill.source_subpath} className={`flex gap-3 border-b border-border p-3 last:border-b-0 ${checked ? 'bg-active' : 'bg-transparent'} ${blocked ? 'opacity-55' : 'cursor-pointer'}`}>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={blocked || busy}
                      onChange={() => setSelectedImports((current) => checked
                        ? current.filter((path) => path !== skill.source_subpath)
                        : [...current, skill.source_subpath])}
                      className="ui-checkbox mt-0.5"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[11.5px] font-medium">{skill.name}</span>
                        <Status tone={skill.compatibility === 'native' ? 'success' : 'warning'}>{compatibilityLabel[skill.compatibility]}</Status>
                      </div>
                      <p className="mt-0.5 truncate font-mono text-[10px] text-faint">{skill.source_subpath || '/'}</p>
                      <p className="mt-1 text-[10px] leading-relaxed text-muted">{skill.description || '설명 없음'}</p>
                      {(skill.report.license || skill.report.license_file) && (
                        <p className="mt-1 text-[10px] text-faint">라이선스 {skill.report.license || `${skill.report.license_file} 포함`}</p>
                      )}
                      {(skill.compiled.capabilities?.required ?? []).length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {skill.compiled.capabilities?.required?.map((capability) => <span key={capability} className="technical-tag">{capability}</span>)}
                        </div>
                      )}
                      {(skill.report.warnings ?? []).length > 0 && <p className="mt-2 text-[10px] text-warn">{skill.report.warnings?.join(' · ')}</p>}
                    </div>
                  </label>
                )
              })}
            </div>

            <footer className="flex items-center justify-between gap-3 border-t border-border px-5 py-3">
              <p className="text-[10px] text-faint">설치 후에도 실행 프로필에서 별도로 활성화해야 합니다.</p>
              <div className="flex gap-2">
                <Button variant="ghost" onClick={dismissPreview} disabled={busy}>취소</Button>
                <Button
                  variant="primary"
                  onClick={() => void confirmGithub(selectedImports)}
                  disabled={busy || selectedImports.length === 0}
                >
                  {busy && <Loader2 size={11} className="animate-spin" />}{selectedImports.length}개 설치
                </Button>
              </div>
            </footer>
        </Dialog>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-[minmax(360px,1fr)_300px]">
        <div className="flex min-h-0 flex-col border-r border-border">
          <div className="flex h-11 shrink-0 items-center gap-3 border-b border-border px-4">
            <div className="relative min-w-0 flex-1">
              <Search size={12} className="pointer-events-none absolute left-2.5 top-2.5 text-faint" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="스킬 검색"
                className="h-8 min-h-8 pl-8"
              />
            </div>
            <span className="whitespace-nowrap font-mono text-[10px] text-faint">
              {activeCount}개 활성 · 최대 {estimatedTokens.toLocaleString()}토큰
            </span>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {filtered.length === 0 ? (
              <EmptyState
                symbol={<PackageOpen size={24} strokeWidth={1.5} />}
                title="설치된 스킬이 없습니다"
                description="GitHub URL을 입력하거나 SKILL.md가 있는 폴더를 선택하세요."
              />
            ) : (
              <div>
                {filtered.map((skill) => {
                  const assignment = assignmentBySkill.get(skill.id)
                  const mode = assignment?.activation_mode ?? 'off'
                  const active = mode !== 'off'
                  return (
                    <div
                      key={skill.id}
                      className="skill-list-row"
                      data-selected={selected?.id === skill.id}
                    >
                      <input
                        type="checkbox"
                        checked={active}
                        onChange={() => updateMode(skill, active ? 'off' : 'auto')}
                        disabled={busy || skill.compatibility === 'blocked' || skill.compatibility === 'adapter_required'}
                        aria-label={`${skill.name} 활성화`}
                        className="ui-checkbox"
                      />
                      <button onClick={() => setSelectedId(skill.id)} className="min-w-0 flex-1 text-left">
                        <div className="flex items-center gap-2">
                          <span className="truncate text-[12px] font-medium">{skill.name}</span>
                          <span className="technical-tag">
                            {sourceLabel(skill)}
                          </span>
                          <Status tone={skill.compatibility === 'native' ? 'success' : 'warning'}>{compatibilityLabel[skill.compatibility]}</Status>
                        </div>
                        <p className="mt-0.5 truncate text-[10.5px] text-faint">{skill.description || '설명 없음'}</p>
                      </button>
                      <Select
                        value={active ? mode : 'auto'}
                        onChange={(event) => updateMode(skill, event.target.value as SkillActivationMode)}
                        disabled={!active || busy || skill.compatibility === 'blocked' || skill.compatibility === 'adapter_required'}
                        aria-label={`${skill.name} 호출 방식`}
                        className="h-7 min-h-7 w-16 py-0 text-[10px]"
                      >
                        <option value="auto">자동</option>
                        <option value="manual">수동</option>
                      </Select>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>

        <aside className="workspace-inspector">
          {selected ? (
            <>
              <Section label="스킬 정보">
                <div className="mb-2 flex items-center gap-2">
                  <Bot size={14} className="text-muted" strokeWidth={1.5} />
                  <span className="font-mono text-[10px] text-faint">{selected.namespace}:{selected.name}</span>
                </div>
                <h3 className="text-[14px] font-medium">{selected.name}</h3>
                <p className="mt-1 text-[10.5px] leading-relaxed text-muted">{selected.description || '설명이 없습니다.'}</p>
              </Section>

              <Section label="변환 정보">
              <dl className="space-y-2 text-[10px]">
                <div className="flex justify-between gap-3"><dt className="text-faint">출처</dt><dd className="truncate text-right text-muted" title={selected.source_locator}>{sourceLabel(selected)} · v{selected.version}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-faint">호환성</dt><dd className="text-muted">{compatibilityLabel[selected.compatibility]}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-faint">실행 위치</dt><dd className="text-muted">{selected.compiled.execution?.context === 'worker' ? '격리 워커' : '현재 세션'}</dd></div>
                <div className="flex justify-between gap-3"><dt className="text-faint">예상 비용</dt><dd className="font-mono text-muted">{Number(selected.report.estimated_prompt_tokens ?? 0).toLocaleString()} 토큰</dd></div>
                {selected.source_revision && <div className="flex justify-between gap-3"><dt className="text-faint">Revision</dt><dd className="font-mono text-muted">{selected.source_revision.slice(0, 10)}</dd></div>}
              </dl>
              </Section>

              <Section label="필요한 도구">
                <div className="mb-2 flex items-center gap-1.5 text-[10px] text-faint"><Cpu size={12} strokeWidth={1.5} /> runtime capability</div>
                <div className="flex flex-wrap gap-1">
                  {(selected.compiled.capabilities?.required ?? []).length ? (
                    selected.compiled.capabilities?.required?.map((capability) => (
                      <span key={capability} className="technical-tag">{capability}</span>
                    ))
                  ) : <span className="text-[10px] text-faint">추가 스킬 없음</span>}
                </div>
              </Section>

              {(selected.report.warnings ?? []).length > 0 && (
                <Section label="변환 보고서">
                  <div className="mb-2 flex items-center gap-1.5 text-[10px] text-warn"><ShieldAlert size={12} strokeWidth={1.5} /> 확인 필요</div>
                  <ul className="space-y-1 text-[10px] leading-relaxed text-muted">
                    {selected.report.warnings?.map((warning) => <li key={warning}>• {warning}</li>)}
                  </ul>
                </Section>
              )}
            </>
          ) : (
            <EmptyState title="스킬을 선택하세요" description="변환 결과와 필요 runtime capability를 표시합니다." />
          )}
        </aside>
      </div>
    </section>
  )
}
