// 공유 Monaco 구성 — FileView 편집기와 diff colorizer가 같은 인스턴스·테마를 쓴다.
import * as monaco from 'monaco-editor/editor/editor.api'
import 'monaco-editor/languages/definitions/css/register'
import 'monaco-editor/languages/definitions/go/register'
import 'monaco-editor/languages/definitions/html/register'
import 'monaco-editor/languages/definitions/javascript/register'
import 'monaco-editor/languages/definitions/markdown/register'
import 'monaco-editor/languages/definitions/python/register'
import 'monaco-editor/languages/definitions/rust/register'
import 'monaco-editor/languages/definitions/shell/register'
import 'monaco-editor/languages/definitions/sql/register'
import 'monaco-editor/languages/definitions/typescript/register'
import 'monaco-editor/languages/definitions/xml/register'
import 'monaco-editor/languages/definitions/yaml/register'

monaco.editor.defineTheme('janus-ide', {
  base: 'vs-dark',
  inherit: true,
  /* v2 토큰 (DESIGN_SYSTEM.md §4) — Monaco는 CSS var를 못 받아 리터럴로 미러링한다 */
  rules: [
    { token: 'diff.add', foreground: '4FB583' },
    { token: 'diff.remove', foreground: 'B3766F' },
    { token: 'diff.hunk', foreground: '989EA3', fontStyle: 'bold' },
    { token: 'diff.header', foreground: '989EA3' }
  ],
  colors: {
    'editor.background': '#141517',
    'editor.foreground': '#e6e8ea',
    'editorLineNumber.foreground': '#55595e',
    'editorLineNumber.activeForeground': '#989ea3',
    'editor.lineHighlightBackground': '#191b1e',
    'editorCursor.foreground': '#e6e8ea',
    'editor.selectionBackground': '#2c3a33',
    'editorIndentGuide.background1': '#1e2023',
    'editorIndentGuide.activeBackground1': '#2c2f33',
    'editorGutter.background': '#141517',
    'minimap.background': '#141517'
  }
})

if (!monaco.languages.getLanguages().some((language) => language.id === 'janus-diff')) {
  monaco.languages.register({ id: 'janus-diff' })
  monaco.languages.setMonarchTokensProvider('janus-diff', {
    tokenizer: {
      root: [
        [/^\+\+\+.*$/, 'diff.header'],
        [/^---.*$/, 'diff.header'],
        [/^@@.*@@.*$/, 'diff.hunk'],
        [/^\+.*/, 'diff.add'],
        [/^-.*/, 'diff.remove'],
        [/^diff --git.*$/, 'diff.header'],
        [/^(?:index|new file mode|deleted file mode|similarity index).*$/, 'diff.header']
      ]
    }
  })
}

export function languageIdFor(path: string): string {
  const extension = path.split('.').pop()?.toLowerCase() ?? ''
  return ({
    ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
    css: 'css', scss: 'scss', html: 'html', json: 'json', md: 'markdown', py: 'python',
    rs: 'rust', go: 'go', sh: 'shell', bash: 'shell', yaml: 'yaml', yml: 'yaml',
    xml: 'xml', sql: 'sql'
  } as Record<string, string>)[extension] ?? 'plaintext'
}

export { monaco }
