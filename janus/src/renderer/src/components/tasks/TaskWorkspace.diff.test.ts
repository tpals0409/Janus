import { describe, expect, it } from 'vitest'
import { buildGaps, diffLines, wordEmphasis } from './TaskWorkspace'

const SAMPLE = [
  '--- a/f', '+++ b/f',
  '@@ -3,3 +3,4 @@',
  ' ctx',
  '-old line',
  '+new line1',
  '+line2',
  ' ctx2',
  '@@ -10,2 +11,2 @@',
  ' a',
  '-foo(bar)',
  '+foo(baz)'
].join('\n')

describe('diff helpers', () => {
  it('finds collapsed context gaps around and between hunks', () => {
    const gaps = buildGaps(diffLines(SAMPLE))
    expect(gaps.map((gap) => [gap.oldStart, gap.newStart, gap.count])).toEqual([
      [1, 1, 2],
      [6, 7, 4],
      [12, 13, null]
    ])
  })

  it('marks the changed span of paired lines so one-character edits stand out', () => {
    const lines = diffLines(SAMPLE)
    const spans = wordEmphasis(lines)
    const removed = lines.find((item) => item.text === '-foo(bar)')!
    const added = lines.find((item) => item.text === '+foo(baz)')!
    expect(removed.text.slice(...spans.get(removed.index)!)).toBe('r')
    expect(added.text.slice(...spans.get(added.index)!)).toBe('z')
  })
})
