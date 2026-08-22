import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('janus', {
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('pick-folder'),
  backendStatus: () => ipcRenderer.invoke('backend-status'),
  taskBrowserOpen: (input: { taskId: string; url: string }) => ipcRenderer.invoke('task-browser-open', input),
  taskBrowserStatus: (taskId: string) => ipcRenderer.invoke('task-browser-status', taskId),
  taskBrowserScreenshot: (taskId: string) => ipcRenderer.invoke('task-browser-screenshot', taskId),
  taskBrowserInspect: (taskId: string) => ipcRenderer.invoke('task-browser-inspect', taskId),
  authToken: process.env.JANUS_AUTH_TOKEN ?? ''
})
