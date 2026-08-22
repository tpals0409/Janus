import { join, resolve } from 'path'

export interface RuntimePathInput {
  isPackaged: boolean
  appPath: string
  resourcesPath: string
  userDataPath: string
}

export function resolveRuntimePaths(input: RuntimePathInput) {
  const repositoryRoot = resolve(input.appPath, '..')
  const resourceRoot = input.isPackaged ? input.resourcesPath : repositoryRoot
  return {
    backendRoot: join(resourceRoot, 'janus_server'),
    modelRuntimeRoot: join(resourceRoot, 'qwen3.8mlx'),
    logRoot: join(input.userDataPath, 'logs'),
    backendEnvironment: join(input.userDataPath, 'venvs', 'janus-server'),
    modelEnvironment: join(input.userDataPath, 'venvs', 'mlx-vlm')
  }
}
