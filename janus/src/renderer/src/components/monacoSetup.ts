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
  rules: [
    { token: 'diff.add', foreground: '83A995' },
    { token: 'diff.remove', foreground: 'C97878' },
    { token: 'diff.hunk', foreground: '7796AD', fontStyle: 'bold' },
    { token: 'diff.header', foreground: 'A3A7AA' }
  ],
  colors: {
    'editor.background': '#171819',
    'editor.foreground': '#d8d9d9',
    'editorLineNumber.foreground': '#515557',
    'editorLineNumber.activeForeground': '#a3a7aa',
    'editor.lineHighlightBackground': '#1d1f20',
    'editorCursor.foreground': '#91b5a2',
    'editor.selectionBackground': '#33433b',
    'editorIndentGuide.background1': '#242627',
    'editorIndentGuide.activeBackground1': '#414445',
    'editorGutter.background': '#171819',
    'minimap.background': '#151617'
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
