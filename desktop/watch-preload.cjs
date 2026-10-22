'use strict';
const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('watchDesktop', {
  pauseAll: () => ipcRenderer.invoke('watch:pause-all'),
  flag: () => ipcRenderer.invoke('watch:flag'),
  archive: () => ipcRenderer.invoke('watch:archive'),
});
