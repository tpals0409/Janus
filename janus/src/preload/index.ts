import { contextBridge, ipcRenderer } from 'electron'

contextBridge.exposeInMainWorld('janus', {
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke('pick-folder')
})
