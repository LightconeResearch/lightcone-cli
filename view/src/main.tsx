// The inventory of the project `lc view` was started in. The browser resolves
// the analysis itself, through the SDK, over the server's read-only routes;
// nothing here writes, and nothing leaves the server it was loaded from.
import {
  AnalysisValidationError,
  resolveAnalysis,
  type ArtifactBinding,
  type ProjectDirectoryEntry,
  type ProjectEntry,
  type ProjectReader,
  type ResolvedAnalysisDocument,
} from '@astra-spec/sdk';
import { Inventory } from '@astra-spec/ui/views';
import '@astra-spec/ui/styles.css';
import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Preview } from './preview';

const mount = document.getElementById('lightcone-view');
if (!mount?.dataset.api || !mount.dataset.files) {
  throw new Error('The page is missing its server routes.');
}
const { api, files } = mount.dataset as { api: string; files: string };

async function failure(response: Response, path: string): Promise<Error> {
  let reason = response.statusText;
  try {
    reason = ((await response.json()) as { message?: string }).message ?? reason;
  } catch {
    // Not every error page is JSON; the status line is enough.
  }
  return new Error(`Could not read ${path || 'the project root'}: ${reason} (${response.status})`);
}

/** Read the project through `lc view`'s routes, which answer for the current project only. */
const reader: ProjectReader = {
  async readText(path) {
    const response = await fetch(files + path.split('/').map(encodeURIComponent).join('/'));
    if (!response.ok) throw await failure(response, path);
    return response.text();
  },
  async stat(path) {
    const response = await fetch(`${api}/stat?path=${encodeURIComponent(path)}`);
    if (!response.ok) throw await failure(response, path);
    return ((await response.json()) as ProjectEntry | null) ?? undefined;
  },
  async readDirectory(path) {
    const response = await fetch(`${api}/list?path=${encodeURIComponent(path)}`);
    if (!response.ok) throw await failure(response, path);
    return (await response.json()) as ProjectDirectoryEntry[];
  },
};

function describe(error: unknown): string {
  if (error instanceof AnalysisValidationError) {
    return [
      error.message,
      ...error.issues.map((issue) => {
        const location = [issue.file, issue.path].filter(Boolean).join(':');
        return `• ${location ? `${location}: ` : ''}${issue.message}`;
      }),
    ].join('\n');
  }
  return error instanceof Error ? error.message : String(error);
}

const darkQuery = window.matchMedia('(prefers-color-scheme: dark)');

function useColorScheme(): 'light' | 'dark' {
  const [dark, setDark] = useState(darkQuery.matches);
  useEffect(() => {
    const update = (event: MediaQueryListEvent) => setDark(event.matches);
    darkQuery.addEventListener('change', update);
    return () => darkQuery.removeEventListener('change', update);
  }, []);
  return dark ? 'dark' : 'light';
}

type State =
  | { status: 'loading' }
  | {
      status: 'ready';
      document: ResolvedAnalysisDocument;
      bindings: ReadonlyMap<string, ArtifactBinding>;
    }
  | { status: 'failed'; message: string };

function App() {
  const scheme = useColorScheme();
  const [state, setState] = useState<State>({ status: 'loading' });
  useEffect(() => {
    resolveAnalysis(reader).then(
      (bundle) =>
        setState({
          status: 'ready',
          document: bundle.document,
          bindings: new Map(bundle.bindings.map((binding) => [binding.outputPath, binding])),
        }),
      (error: unknown) => setState({ status: 'failed', message: describe(error) }),
    );
  }, []);

  return (
    <div className="astra-ui lightcone-view" data-astra-color-scheme={scheme}>
      {state.status === 'ready' ? (
        <Inventory
          document={state.document}
          renderArtifact={(output, { compact }) => (
            <Preview
              output={output}
              binding={state.bindings.get(output.canonicalPath)}
              files={files}
              compact={compact}
            />
          )}
        />
      ) : state.status === 'failed' ? (
        <pre className="lightcone-view-message" role="alert">
          {state.message}
        </pre>
      ) : (
        <p className="lightcone-view-message" role="status">
          Reading the analysis…
        </p>
      )}
    </div>
  );
}

createRoot(mount).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
