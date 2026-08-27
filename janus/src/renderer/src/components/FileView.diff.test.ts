import { describe, expect, it } from 'vitest'
import { diffToSides } from './FileView'

const DIFF = `diff --git a/src/todo.js b/src/todo.js
index 111..222 100644
--- a/src/todo.js
+++ b/src/todo.js
@@ -1,3 +1,3 @@
 const items = []
-function add(x) { items.push(x) }
+function add(item) { items.push(item) }
 export { add }
@@ -10,2 +10,3 @@
 function remaining() {
+  // count open items
   return items.length
\\ No newline at end of file
`

describe('diffToSides', () => {
  it('reconstructs aligned left/right excerpts from unified hunks', () => {
    const sides = diffToSides(DIFF)
    expect(sides).not.toBeNull()
    expect(sides!.original.split('\n')).toEqual([
      '@@ -1,3 +1,3 @@',
      'const items = []',
      'function add(x) { items.push(x) }',
      'export { add }',
      '',
      '@@ -10,2 +10,3 @@',
      'function remaining() {',
      '  return items.length'
    ])
    expect(sides!.modified.split('\n')).toEqual([
      '@@ -1,3 +1,3 @@',
      'const items = []',
      'function add(item) { items.push(item) }',
      'export { add }',
      '',
      '@@ -10,2 +10,3 @@',
      'function remaining() {',
      '  // count open items',
      '  return items.length'
    ])
    // hunk 경계 앞까지는 양쪽 줄 수가 같아야 정렬이 유지된다
    const headerAt = (text: string) => text.split('\n').indexOf('@@ -10,2 +10,3 @@')
    expect(headerAt(sides!.original)).toBe(headerAt(sides!.modified))
  })

  it('renders an untracked pseudo-diff as empty-vs-content', () => {
    const untracked = 'diff --git a/src/new.js b/src/new.js\nnew file mode 100644\n'
      + '--- /dev/null\n+++ b/src/new.js\n+const a = 1\n+export { a }\n'
    const sides = diffToSides(untracked)
    expect(sides).toEqual({ original: '', modified: 'const a = 1\nexport { a }' })
  })

  it('returns null when the text has no hunks (binary placeholder)', () => {
    expect(diffToSides('Binary files a/x and b/x differ')).toBeNull()
  })
})
