import {
  AnalysisValidationError,
  indexAnalysis,
  resolveAnalysis,
  type AnalysisIndex,
  type ArtifactBinding,
  type ResolvedAnalysisDocument,
  type ResolvedOutput
} from '@astra-spec/sdk';
import { RecordView } from '@astra-spec/ui/components';
import { paperEntry, recordEntry, type DetailEntry } from '@astra-spec/ui/lib';
import { locateRecord, paperForDoi, recordTitle } from '@astra-spec/ui/model';
import { Inventory } from '@astra-spec/ui/views';
import {
  ILabShell,
  IRouter,
  type JupyterFrontEnd,
  type JupyterFrontEndPlugin
} from '@jupyterlab/application';
import { IThemeManager, MainAreaWidget, ReactWidget } from '@jupyterlab/apputils';
import { PageConfig, URLExt } from '@jupyterlab/coreutils';
import { HTMLSelect } from '@jupyterlab/ui-components';
import { Signal } from '@lumino/signaling';
import type { Widget } from '@lumino/widgets';
import type { ReactNode } from 'react';

import { projectReader } from './project-reader';

const OPEN_FROM_URL = 'lightcone:open-from-url';

type Loaded = {
  document: ResolvedAnalysisDocument;
  index: AnalysisIndex;
  artifacts: ReadonlyMap<string, ArtifactBinding>;
};
type State = Loaded | { error: string[] } | undefined;

function describe(error: unknown): string[] {
  if (error instanceof AnalysisValidationError) {
    return error.issues.map(({ file, path, message }) => `${file}${path ? ` ${path}` : ''}: ${message}`);
  }
  return [error instanceof Error ? error.message : String(error)];
}

/** A React view as a Lumino widget, re-rendered on `update()`. */
class View extends ReactWidget {
  constructor(private readonly view: () => ReactNode) {
    super();
  }

  protected render() {
    return <>{this.view()}</>;
  }
}

const IMAGE = /\.(png|jpe?g|gif|svg|webp)$/i;

/** Figures that exist on disk, through the server's file handler; nothing else. */
function renderArtifact({ artifacts }: Loaded) {
  return (output: ResolvedOutput) => {
    const binding = artifacts.get(output.canonicalPath);
    if (!binding || !IMAGE.test(binding.path)) return null;
    const url = URLExt.join(PageConfig.getBaseUrl(), 'files', URLExt.encodeParts(binding.path));
    return <img className="lc-Artifact" src={`${url}?${binding.cacheToken}`} alt="" />;
  };
}

function key(entry: DetailEntry): string {
  return entry.kind === 'record' ? entry.canonicalPath : `paper:${entry.doi}`;
}

function title(entry: DetailEntry, { document, index }: Loaded): string {
  if (entry.kind === 'paper') {
    const analysis = index.analysisByPath.get(entry.analysisPath);
    return (analysis && paperForDoi(document, index, analysis, entry.doi)?.title) || entry.doi;
  }
  const record = index.recordByPath.get(entry.canonicalPath);
  return record ? recordTitle(record) : entry.canonicalPath;
}

/**
 * The project's analysis as JupyterLab tabs: an Overview that lists every
 * record, and one tab per record opened from it. The spec is resolved here,
 * in the browser, from files read through the Contents API.
 */
const explorer: JupyterFrontEndPlugin<void> = {
  id: 'lightcone:explorer',
  autoStart: true,
  requires: [ILabShell, IRouter],
  optional: [IThemeManager],
  activate: (
    app: JupyterFrontEnd,
    shell: ILabShell,
    router: IRouter,
    themes: IThemeManager | null
  ) => {
    const changed = new Signal<object, void>(explorer);
    let state: State;
    const load = async (universeId?: string) => {
      try {
        const reader = projectReader(app.serviceManager.contents);
        const { document, bindings } = await resolveAnalysis(reader, universeId ? { universeId } : {});
        state = {
          document,
          index: indexAnalysis(document),
          artifacts: new Map(bindings.map(binding => [binding.outputPath, binding]))
        };
      } catch (error) {
        state = { error: describe(error) };
      }
      changed.emit();
    };
    const ready = load();

    const scheme = () => (themes?.theme && !themes.isLight(themes.theme) ? 'dark' : 'light');
    // Every surface re-renders when the analysis or the theme changes.
    const surface = (body: (loaded: Loaded) => ReactNode) => {
      const widget = new View(() => (
        <div className="astra-ui astra-isolate lc-Surface" data-astra-color-scheme={scheme()}>
          {state === undefined ? null : 'error' in state ? (
            <div className="lc-Error">
              <h2>This analysis cannot be read</h2>
              <pre>{state.error.join('\n')}</pre>
            </div>
          ) : (
            body(state)
          )}
        </div>
      ));
      changed.connect(widget.update, widget);
      themes?.themeChanged.connect(widget.update, widget);
      return widget;
    };

    const tabs = new Map<string, Widget>();
    let opened = 0;
    const open = (entry: DetailEntry) => {
      const existing = tabs.get(key(entry));
      if (existing) {
        shell.activateById(existing.id);
        return;
      }
      // A plain widget, not a MainAreaWidget: a record has no toolbar, and a
      // toolbar attached after the theme is applied sends the web components'
      // accent tokens into a loop that never settles.
      const tab = surface(loaded => (
        <RecordView
          entry={entry}
          document={loaded.document}
          index={loaded.index}
          renderArtifact={renderArtifact(loaded)}
          onOpenRecord={(record, analysis) => open(recordEntry(record.canonicalPath, analysis.canonicalPath))}
          onOpenPaper={(doi, analysis, focus) => open(paperEntry(doi, analysis.canonicalPath, focus))}
          fallback={<p>Nothing at <code>{key(entry)}</code> in this analysis.</p>}
        />
      ));
      const retitle = () => {
        tab.title.label = state && !('error' in state) ? title(entry, state) : key(entry);
        tab.title.caption = key(entry);
      };
      retitle();
      changed.connect(retitle, tab);
      tab.id = `lightcone-record-${++opened}`;
      tab.title.closable = true;
      tab.disposed.connect(() => tabs.delete(key(entry)));
      tabs.set(key(entry), tab);
      shell.add(tab, 'main');
      shell.activateById(tab.id);
    };

    app.commands.addCommand(OPEN_FROM_URL, {
      execute: async args => {
        const path = new URLSearchParams((args as IRouter.ILocation).search).get('record');
        await ready;
        if (!path || !state || 'error' in state) return;
        const analysis = locateRecord(state.index, path)?.analysis ?? state.document.analysis;
        open(recordEntry(path, analysis.canonicalPath));
      }
    });
    router.register({ command: OPEN_FROM_URL, pattern: /[?&]record=/ });

    const overview = new MainAreaWidget({
      content: surface(loaded => (
        <Inventory
          document={loaded.document}
          index={loaded.index}
          renderArtifact={renderArtifact(loaded)}
          detail={[]}
          onDetailChange={next => {
            const entry = next.at(-1);
            if (entry) open(entry);
          }}
        />
      ))
    });
    overview.id = 'lightcone-overview';
    overview.title.label = 'Overview';
    overview.title.closable = false;
    const universes = new View(() => {
      if (!state || 'error' in state) return null;
      const { universeId, availableUniverseIds } = state.document.universe;
      return (
        <label className="lc-Universe">
          Universe
          <HTMLSelect
            value={universeId}
            options={availableUniverseIds.length ? availableUniverseIds : [universeId]}
            onChange={event => void load(event.currentTarget.value)}
          />
        </label>
      );
    });
    changed.connect(universes.update, universes);
    overview.toolbar.addItem('universe', universes);
    shell.add(overview, 'main');
    // There is nothing in the side areas; an empty one keeps its width until
    // it is told it is collapsed.
    void app.restored.then(() => {
      shell.collapseLeft();
      shell.collapseRight();
    });
  }
};

export default explorer;
