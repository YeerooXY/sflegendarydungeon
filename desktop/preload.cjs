'use strict';
const {contextBridge, ipcRenderer} = require('electron');
contextBridge.exposeInMainWorld('dungeonApp', {
  state: () => ipcRenderer.invoke('desktop:state'),
  mode: mode => ipcRenderer.invoke('desktop:mode', mode),
  select: id => ipcRenderer.invoke('desktop:select', id),
  add: profile => ipcRenderer.invoke('desktop:add', profile),
  import: () => ipcRenderer.invoke('desktop:import'),
  fullscreen: () => ipcRenderer.invoke('desktop:fullscreen'),
  modal: open => ipcRenderer.invoke('desktop:modal', open),
  mimicExample: () => ipcRenderer.invoke('desktop:mimic-example'),
  autoplay: on => ipcRenderer.invoke('desktop:autoplay', on),
  onState: callback => { const listener = (_, state) => callback(state); ipcRenderer.on('desktop:update', listener); return () => ipcRenderer.removeListener('desktop:update', listener); }
});
