import { ShieldAlert } from 'lucide-react'
import { useStore } from '../store'
import { Button, Status } from './ui'

/** 승인 프롬프트에 보여줄 요약. bash는 명령어 전문, edit은 치환 내용. */
function summarize(tool: string, args: Record<string, unknown>) {
  if (tool === 'run_bash') {
    return <pre className="whitespace-pre-wrap break-all font-mono text-[12px]">{String(args.command ?? '')}</pre>
  }
  if (tool === 'edit_file') {
    return (
      <div className="font-mono text-[11px]">
        <div className="mb-1 text-muted">{String(args.path ?? '')}</div>
        <div style={{ color: 'var(--color-danger)' }}>- {String(args.old_string ?? '').slice(0, 300)}</div>
        <div style={{ color: 'var(--color-ok)' }}>+ {String(args.new_string ?? '').slice(0, 300)}</div>
      </div>
    )
  }
  if (tool === 'write_file') {
    const c = String(args.content ?? '')
    return (
      <div className="font-mono text-[11px]">
        <div className="mb-1 text-muted">
          {String(args.path ?? '')} <span className="text-faint">({c.length}자)</span>
        </div>
        <pre className="max-h-24 overflow-auto whitespace-pre-wrap break-words text-faint">
          {c.slice(0, 400)}
        </pre>
      </div>
    )
  }
  return (
    <pre className="whitespace-pre-wrap break-words font-mono text-[11px]">
      {JSON.stringify(args, null, 2)}
    </pre>
  )
}

/**
 * 실제 승인이다 — Orca처럼 터미널에 키 입력을 흘려보내는 프록시가 아니라,
 * 서버에서 대기 중인 에이전트 스레드를 실제로 풀어주거나 막는다.
 */
export default function ApprovalCard() {
  const approvals = useStore((s) => s.approvals)
  const respond = useStore((s) => s.respondApproval)
  const approval = approvals[0]
  if (!approval) return null

  return (
    <div className="border-t border-border-strong bg-panel px-4 py-3">
      <div className="mb-2 flex items-center gap-2">
        <ShieldAlert size={14} style={{ color: 'var(--color-warn)' }} />
        <span className="text-[12px]">
          <b>{approval.node_id}</b> 에이전트가{' '}
          <b className="font-mono">{approval.tool}</b> 실행을 요청합니다
        </span>
        {approvals.length > 1 && (
          <Status tone="warning">대기 {approvals.length - 1}건</Status>
        )}
        <div className="ml-auto flex gap-2">
          <Button
            onClick={() => respond(approval.id, false)}
            variant="ghost"
            compact
          >
            거부
          </Button>
          <Button
            onClick={() => respond(approval.id, true)}
            compact
          >
            승인
          </Button>
        </div>
      </div>
      <div className="max-h-32 overflow-auto border border-border bg-base px-2.5 py-2">
        {summarize(approval.tool, approval.args)}
      </div>
    </div>
  )
}
