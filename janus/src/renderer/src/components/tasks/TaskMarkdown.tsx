import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * 답변 렌더러. 표·취소선·체크리스트는 CommonMark가 아니라 GFM이라 remark-gfm이 필요하다.
 * react-markdown은 raw HTML을 렌더하지 않는다 — 모델 출력을 그대로 믿지 않는다.
 *
 * 초기 번들 예산을 넘기므로 호출부에서 lazy로 불러온다.
 */
export default function TaskMarkdown({ content }: { content: string }) {
  return <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
}
