import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('janus', {
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('pick-folder'),
  backendStatus: () => ipcRenderer.invoke('backend-status'),
  authToken: process.env.JANUS_AUTH_TOKEN ?? ''
})
