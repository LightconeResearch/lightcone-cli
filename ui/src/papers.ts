import { collectCitedDois, normalizeDoi, walkAnalyses, type ResolvedAnalysisDocument } from '@astra-spec/sdk';
import { pdfJsWithWorker, type PdfJsLoader } from '@astra-spec/ui/lib';
import type { InventoryPaperMetadata } from '@astra-spec/ui/model';

import { errorMessage, getJson } from './api';

/**
 * pdf.js for astra-ui's paper viewer. rspack emits a worker only when it is
 * created through `new Worker(new URL(...))`, so it is made here and handed
 * to the viewer, which runs one per open document. The legacy build and its
 * worker are the pair astra-ui validates, and they go together.
 */
export const loadPdfJs: PdfJsLoader = async () =>
  pdfJsWithWorker(
    await import('pdfjs-dist/legacy/build/pdf.mjs'),
    () => new Worker(new URL('pdfjs-dist/legacy/build/pdf.worker.min.mjs', import.meta.url), { type: 'module' })
  );

// A DOI resolves to a landing page, so no general rule turns one into a PDF.
// arXiv's do: the DOI embeds the identifier, and arxiv.org/pdf serves the file
// with CORS and byte ranges, which is what the viewer needs to stream it. Every
// other DOI keeps the viewer's own link to the DOI. The rule is
// jupyterlab-lightcone's, so a paper reads the same in both hosts.
const ARXIV_DOI = '10.48550/arxiv.';
const NEW_STYLE_ID = /^\d{4}\.\d{4,5}$/;
const OLD_STYLE_ID = /^[a-z][a-z-]*(?:\.[a-z][a-z-]*)?\/\d{7}$/;

/**
 * A `pdfUrl` for every cited arXiv paper, keyed by normalized DOI. When
 * insights cite different revisions of one paper the newest wins, and a
 * citation naming none reads the latest, which wins over every pinned one:
 * the viewer reads one file per DOI.
 */
function arxivPapers(document: ResolvedAnalysisDocument): Record<string, InventoryPaperMetadata> {
  const revisions = new Map<string, number | 'latest'>();
  for (const analysis of walkAnalyses(document)) {
    for (const insight of [...analysis.prior_insights, ...analysis.findings]) {
      for (const { doi, version } of insight.evidence) {
        if (typeof doi !== 'string') continue;
        const key = normalizeDoi(doi);
        const id = key.slice(ARXIV_DOI.length);
        if (!key.startsWith(ARXIV_DOI) || !(NEW_STYLE_ID.test(id) || OLD_STYLE_ID.test(id))) continue;
        const cited = typeof version === 'number' && Number.isInteger(version) && version > 0 ? version : 'latest';
        const known = revisions.get(key) ?? cited;
        revisions.set(key, cited === 'latest' || known === 'latest' ? 'latest' : Math.max(cited, known));
      }
    }
  }
  return Object.fromEntries(
    [...revisions].map(([key, revision]) => [
      key,
      { pdfUrl: `https://arxiv.org/pdf/${key.slice(ARXIV_DOI.length)}${revision === 'latest' ? '' : `v${revision}`}` }
    ])
  );
}

/**
 * Every cited paper the page can name or read: astra's paper cache first,
 * through the app's own route (title, authors, and the PDF astra verified
 * quotes against), then arXiv's PDF for an arXiv DOI the cache lacks. Any
 * other DOI keeps the viewer's link to the DOI.
 */
export async function citedPapers(document: ResolvedAnalysisDocument): Promise<Record<string, InventoryPaperMetadata>> {
  const papers = arxivPapers(document);
  const dois = collectCitedDois(document);
  if (dois.length === 0) return papers;
  try {
    const query = new URLSearchParams(dois.map(doi => ['doi', doi]));
    const cached = await getJson<{ papers: Record<string, InventoryPaperMetadata> }>('papers', query);
    Object.assign(papers, cached.papers);
  } catch (error) {
    // The papers still read from arXiv or link to their DOI; say why the rest do not.
    console.warn(`astra's paper cache could not be read: ${errorMessage(error)}`);
  }
  return papers;
}
