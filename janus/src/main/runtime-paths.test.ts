import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveRuntimePaths } from './runtime-paths.ts'

test('development runtime resolves sibling source projects', () => {
  const paths = resolveRuntimePaths({
    isPackaged: false, appPath: '/repo/janus', resourcesPath: '/unused',
    userDataPath: '/data/Janus'
  })
  assert.equal(paths.backendRoot, '/repo/janus_server')
  assert.equal(paths.modelRuntimeRoot, '/repo/qwen3.8mlx')
})

test('packaged runtime resolves bundled resources and writable environments', () => {
  const paths = resolveRuntimePaths({
    isPackaged: true, appPath: '/Applications/Janus.app/Contents/Resources/app.asar',
    resourcesPath: '/Applications/Janus.app/Contents/Resources', userDataPath: '/user/Janus'
  })
  assert.equal(paths.backendRoot, '/Applications/Janus.app/Contents/Resources/janus_server')
  assert.equal(paths.modelRuntimeRoot, '/Applications/Janus.app/Contents/Resources/qwen3.8mlx')
  assert.equal(paths.backendEnvironment, '/user/Janus/venvs/janus-server')
  assert.equal(paths.modelEnvironment, '/user/Janus/venvs/mlx-vlm')
})
