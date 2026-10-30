import './public-path';
import '../style/index.css';

import {
  AnalysisValidationError,
  indexAnalysis,
  resolveAnalysis,
  type AnalysisIndex,
  type ArtifactBinding,
  type ResolvedAnalysisDocument,
  type ResolvedOutput
} from '@astra-spec/sdk';
import { recordEntry } from '@astra-spec/ui/lib';
import { locateRecord } from '@astra-spec/ui/model';
import { Inventory } from '@astra-spec/ui/views';
import { PageConfig, URLExt } from '@jupyterlab/coreutils';
import { ContentsManager, ServerConnection } from '@jupyterlab/services';
import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';

import { projectReader } from './project-reader';

const UNIVERSE = 'baseline';
const IMAGE = /\.(png|jpe?g|gif|svg|webp)$/i;
const DARK = window.matchMedia('(prefers-color-scheme: dark)');

type Loaded = {
  document: ResolvedAnalysisDocument;
  index: AnalysisIndex;
  artifacts: ReadonlyMap<string, ArtifactBinding>;
};
type State = Loaded | { error: string[] };

async function load(touched: Set<string>): Promise<State> {
  try {
    const reader = projectReader(new ContentsManager(), touched);
    const { document, bindings } = await resolveAnalysis(reader, { universeId: UNIVERSE });
    return {
      document,
      index: indexAnalysis(document),
      artifacts: new Map(bindings.map(binding => [binding.outputPath, binding]))
    };
  } catch (error) {
    if (error instanceof AnalysisValidationError) {
      return {
        error: error.issues.map(({ file, path, message }) => `${file}${path ? ` ${path}` : ''}: ${message}`)
      };
    }
    return { error: [error instanceof Error ? error.message : String(error)] };
  }
}

/** Figures that exist on disk, through the server's file handler; nothing else. */
function renderArtifact({ artifacts }: Loaded) {
  return (output: ResolvedOutput) => {
    const binding = artifacts.get(output.canonicalPath);
    if (!binding || !IMAGE.test(binding.path)) return null;
    const url = URLExt.join(PageConfig.getBaseUrl(), 'files', URLExt.encodeParts(binding.path));
    return <img className="lc-Artifact" src={`${url}?${binding.cacheToken}`} alt="" />;
  };
}

/**
 * Call `changed` when any of `paths` is created, modified or deleted on
 * disk, through jupyter-live-content's WebSocket. Each path's ancestor
 * directories are subscribed too, so a file appearing in a directory that
 * did not exist yet is still seen: the directory's own creation is. Returns
 * the function that stops watching.
 */
function watch(paths: Set<string>, changed: () => void): () => void {
  const settings = ServerConnection.makeSettings();
  let url = URLExt.join(settings.wsUrl, 'api/live-content/ws');
  if (settings.appendToken && settings.token) {
    url += `?token=${encodeURIComponent(settings.token)}`;
  }
  const subscribed = new Set<string>();
  for (const path of paths) {
    for (let at = path; at; at = at.slice(0, Math.max(0, at.lastIndexOf('/')))) {
      subscribed.add(at);
    }
  }
  const socket = new settings.WebSocket(url);
  socket.onopen = () => {
    for (const path of subscribed) {
      socket.send(JSON.stringify({ type: 'client_opened', path }));
    }
  };
  // A save is several events in a burst; one re-resolve answers them all.
  let pending: number | undefined;
  socket.onmessage = () => {
    window.clearTimeout(pending);
    pending = window.setTimeout(changed, 200);
  };
  return () => {
    window.clearTimeout(pending);
    socket.close();
  };
}

/** The dialog a deep link opens: the record, in the analysis that declares it. */
function opening(record: string, { document, index }: Loaded) {
  const analysis = locateRecord(index, record)?.analysis ?? document.analysis;
  return recordEntry(record, analysis.canonicalPath);
}

/**
 * The project's baseline analysis, resolved here in the browser from files
 * read through Jupyter's Contents API and shown as astra-ui's Inventory: each
 * record opens in its own dialog, and `?record=` opens one at load. It is
 * resolved again whenever a file it read changes on disk.
 */
function App({ record }: { record: string | null }) {
  const [state, setState] = useState<State>();
  const [dark, setDark] = useState(DARK.matches);
  useEffect(() => {
    let stop = () => {};
    let live = true;
    const refresh = async () => {
      const touched = new Set<string>();
      const next = await load(touched);
      if (!live) return;
      setState(next);
      stop();
      stop = watch(touched, () => void refresh());
    };
    void refresh();
    return () => {
      live = false;
      stop();
    };
  }, []);
  useEffect(() => {
    const follow = () => setDark(DARK.matches);
    DARK.addEventListener('change', follow);
    return () => DARK.removeEventListener('change', follow);
  }, []);
  return (
    <div className="astra-ui astra-isolate lc-Surface" data-astra-color-scheme={dark ? 'dark' : 'light'}>
      {state === undefined ? null : 'error' in state ? (
        <div className="lc-Error">
          <h2>This analysis cannot be read</h2>
          <pre>{state.error.join('\n')}</pre>
        </div>
      ) : (
        <Inventory
          document={state.document}
          index={state.index}
          renderArtifact={renderArtifact(state)}
          defaultDetail={record ? [opening(record, state)] : undefined}
        />
      )}
    </div>
  );
}

const record = new URLSearchParams(window.location.search).get('record');
createRoot(document.body.appendChild(document.createElement('div'))).render(<App record={record} />);
