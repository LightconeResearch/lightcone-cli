import './public-path';
import '../style/index.css';

import {
  AnalysisValidationError,
  collectCitedDois,
  indexAnalysis,
  projectDirname,
  resolveAnalysis,
  type AnalysisIndex,
  type ArtifactBinding,
  type ResolvedAnalysisDocument
} from '@astra-spec/sdk';
import { recordEntry } from '@astra-spec/ui/lib';
import { locateRecord, type InventoryPaperMetadataMap } from '@astra-spec/ui/model';
import { Inventory } from '@astra-spec/ui/views';
import { URLExt } from '@jupyterlab/coreutils';
import { ContentsManager, ServerConnection } from '@jupyterlab/services';
import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';

import { errorMessage } from './api';
import { artifactRenderer } from './artifacts';
import { citedPapers, loadPdfJs } from './papers';
import { projectReader } from './project-reader';
import { Provenance } from './provenance';

const UNIVERSE = 'baseline';
const DARK = window.matchMedia('(prefers-color-scheme: dark)');
const CONTENTS = new ContentsManager();

type Loaded = {
  document: ResolvedAnalysisDocument;
  index: AnalysisIndex;
  artifacts: ReadonlyMap<string, ArtifactBinding>;
};
type State = Loaded | { error: string[] };

async function load(touched: Set<string>): Promise<State> {
  try {
    const reader = projectReader(CONTENTS, touched);
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
    return { error: [errorMessage(error)] };
  }
}

/**
 * Call `changed` when a followed path is created, modified or deleted on
 * disk, through a jupyter-live-content WebSocket; `follow` moves its
 * subscriptions to a new set of paths. Each path's ancestor directories are
 * subscribed too, so a file appearing in a directory that did not exist yet
 * is still seen: the directory's own creation is. The socket authenticates
 * with the page's login cookie.
 *
 * A dropped socket (a sleeping laptop, a reset tunnel, a stopped server) is
 * reopened with backoff, and `connected` says whether the page is following
 * the disk meanwhile. Reopened, it resubscribes and calls `changed`, since
 * whatever moved while it was down went unannounced.
 */
function watcher(changed: () => void, connected: (following: boolean) => void) {
  const settings = ServerConnection.makeSettings();
  let socket: WebSocket | undefined;
  let subscribed = new Set<string>();
  let wanted = new Set<string>();
  let pending: number | undefined;
  let retry: number | undefined;
  let delay = 1000;
  let dropped = false;
  let closed = false;
  const sync = () => {
    const ws = socket;
    if (ws?.readyState !== WebSocket.OPEN) return;
    const send = (type: string, path: string) => ws.send(JSON.stringify({ type, path }));
    for (const path of subscribed) if (!wanted.has(path)) send('client_closed', path);
    for (const path of wanted) if (!subscribed.has(path)) send('client_opened', path);
    subscribed = wanted;
  };
  const open = () => {
    socket = new settings.WebSocket(URLExt.join(settings.wsUrl, 'api/live-content/ws'));
    socket.onopen = () => {
      delay = 1000;
      sync();
      if (dropped) {
        dropped = false;
        connected(true);
        changed();
      }
    };
    // A save is several events in a burst; one re-resolve answers them all.
    socket.onmessage = () => {
      window.clearTimeout(pending);
      pending = window.setTimeout(changed, 200);
    };
    // An error is always followed by a close, so the close alone reconnects.
    socket.onclose = () => {
      if (closed) return;
      subscribed = new Set();
      if (!dropped) {
        dropped = true;
        connected(false);
      }
      retry = window.setTimeout(open, delay);
      delay = Math.min(delay * 2, 30_000);
    };
  };
  open();
  return {
    follow(paths: Set<string>) {
      wanted = new Set();
      for (const path of paths) {
        for (let at = path; at; at = projectDirname(at)) wanted.add(at);
      }
      sync();
    },
    close() {
      closed = true;
      window.clearTimeout(pending);
      window.clearTimeout(retry);
      socket?.close();
    }
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
 * record opens in its own dialog, and `?record=` opens one at load. Outputs
 * preview their artifacts and show the run lc recorded; cited papers read in
 * place, from astra's paper cache or arXiv. It is resolved again whenever a
 * file it read changes on disk.
 */
function App({ record }: { record: string | null }) {
  const [state, setState] = useState<State>();
  const [papers, setPapers] = useState<InventoryPaperMetadataMap>({});
  const [dark, setDark] = useState(DARK.matches);
  const [following, setFollowing] = useState(true);
  useEffect(() => {
    let live = true;
    // One resolution at a time; changes during one ask for exactly one more.
    let loading = false;
    let again = false;
    const watching = watcher(() => void refresh(), setFollowing);
    const refresh = async () => {
      if (loading) {
        again = true;
        return;
      }
      loading = true;
      do {
        again = false;
        const touched = new Set<string>();
        const next = await load(touched);
        if (!live) return;
        setState(next);
        // The tab names the analysis: several projects can be open at once.
        window.document.title = 'document' in next ? next.document.analysis.name : 'Lightcone';
        watching.follow(touched);
      } while (again);
      loading = false;
    };
    void refresh();
    return () => {
      live = false;
      watching.close();
    };
  }, []);
  // Papers follow the cited DOIs rather than every change on disk (astra's
  // cache is outside the project), and arrive after the analysis is shown.
  const analysis = state && 'document' in state ? state.document : undefined;
  const cited = analysis ? collectCitedDois(analysis).join(' ') : '';
  useEffect(() => {
    if (!analysis) return;
    let live = true;
    void citedPapers(analysis).then(found => {
      if (live) setPapers(found);
    });
    return () => {
      live = false;
    };
  }, [cited]);
  useEffect(() => {
    const follow = () => setDark(DARK.matches);
    DARK.addEventListener('change', follow);
    return () => DARK.removeEventListener('change', follow);
  }, []);
  return (
    <div className="astra-ui astra-isolate lc-Surface" data-astra-color-scheme={dark ? 'dark' : 'light'}>
      {following ? null : (
        <p className="lc-Offline" role="status">
          Not following changes on disk: the connection to the server was lost. Retrying — if this
          persists, run <code>lc view</code> again for a new link.
        </p>
      )}
      {state === undefined ? null : 'error' in state ? (
        <div className="lc-Error">
          <h2>This analysis cannot be read</h2>
          <pre>{state.error.join('\n')}</pre>
        </div>
      ) : (
        <Inventory
          document={state.document}
          index={state.index}
          renderArtifact={artifactRenderer(state.artifacts)}
          renderProvenance={output => {
            const binding = state.artifacts.get(output.canonicalPath);
            return <Provenance key={`${output.canonicalPath}:${binding?.cacheToken}`} path={binding?.path} />;
          }}
          paperMetadata={papers}
          loadPdfJs={loadPdfJs}
          defaultDetail={record ? [opening(record, state)] : undefined}
        />
      )}
    </div>
  );
}

const record = new URLSearchParams(window.location.search).get('record');
createRoot(document.body.appendChild(document.createElement('div'))).render(<App record={record} />);
