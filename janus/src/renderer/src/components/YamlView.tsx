import Editor, { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'
import { useStore } from '../store'
import { toYaml } from '../yaml'

// CDN에서 받지 않고 번들된 monaco를 쓴다 (CSP가 외부 요청을 막는다)
loader.config({ monaco })

export default function YamlView() {
  const spec = useStore((s) => s.spec)
  const saved = useStore((s) => s.yaml)
  const dirty = useStore((s) => s.dirty)

  // 편집 중이면 현재 스펙을 직렬화해 보여준다. 저장본은 서버가 준 YAML 그대로.
  const text = dirty && spec ? toYaml(spec) : saved

  return (
    <div className="relative h-full">
      <div className="absolute right-4 top-2 z-10 rounded bg-raised px-2 py-0.5 text-[10px] text-faint">
        읽기 전용 · 편집은 그래프에서
      </div>
      <Editor
        height="100%"
        language="yaml"
        theme="vs-dark"
        value={text}
        options={{
          readOnly: true,
          minimap: { enabled: false },
          fontSize: 12,
          lineNumbers: 'on',
          scrollBeyondLastLine: false,
          renderLineHighlight: 'none',
          padding: { top: 12 }
        }}
      />
    </div>
  )
}
