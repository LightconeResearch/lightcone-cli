// Inspired from JupyterLab's examples/app/index.js (BSD-3-Clause,
// Copyright (c) Jupyter Development Team): JupyterLab itself, started with a
// chosen plugin list. Here that list is the smallest set the explorer needs.
import './public-path';
import '../style/index.css';

import { JupyterFrontEndPlugin, JupyterLab } from '@jupyterlab/application';
import applicationPlugins from '@jupyterlab/application-extension';
import apputilsPlugins from '@jupyterlab/apputils-extension';
import { IStateDB, StateDB } from '@jupyterlab/statedb';
import themeDark from '@jupyterlab/theme-dark-extension';
import themeLight from '@jupyterlab/theme-light-extension';
import translationPlugins from '@jupyterlab/translation-extension';

import explorer from './explorer';

// LabShell shows nothing until a layout restorer has run; ours restores from
// memory, so the server keeps no workspace for the app.
const state: JupyterFrontEndPlugin<IStateDB> = {
  id: 'lightcone:state',
  autoStart: true,
  provides: IStateDB,
  activate: () => new StateDB()
};

const only = (plugins: JupyterFrontEndPlugin<any>[], ...names: string[]) =>
  plugins.filter(({ id }) => names.includes(id.split(':')[1]));

window.addEventListener('load', async () => {
  const lab = new JupyterLab();
  lab.registerPlugins([
    ...only(applicationPlugins, 'layout', 'paths', 'router', 'shell'),
    ...only(apputilsPlugins, 'settings', 'themes'),
    ...([] as JupyterFrontEndPlugin<any>[]).concat(themeLight, themeDark),
    ...translationPlugins,
    state,
    explorer
  ]);
  await lab.start();
});
