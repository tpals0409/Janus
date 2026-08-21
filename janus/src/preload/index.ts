import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('janus', {
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('pick-folder'),
  authToken: process.env.JANUS_AUTH_TOKEN ?? ''
})
