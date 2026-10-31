import type { ArtifactBinding, ResolvedOutput } from '@astra-spec/sdk';
import { ArtifactPreview, type ArtifactRenderer } from '@astra-spec/ui/components';
import {
  metricPreviewFromJson,
  tablePreviewFromDelimited,
  tablePreviewFromRows,
  type ArtifactPreviewData
} from '@astra-spec/ui/lib';
import { PageConfig, URLExt } from '@jupyterlab/coreutils';
import { ServerConnection } from '@jupyterlab/services';
import { useEffect, useState } from 'react';

import { errorMessage } from './api';

// Previews are bounded, so a large artifact costs a sample, never its bytes.
// The bounds follow jupyterlab-lightcone's, so an output previews the same in
// both hosts.
const IMAGE_FORMATS = new Set(['avif', 'gif', 'jpeg', 'jpg', 'png', 'svg', 'webp']);
const DELIMITERS: ReadonlyMap<string, string> = new Map([
  ['csv', ','],
  ['tsv', '\t']
]);
const TABLE_SAMPLE_BYTES = 65_536;
const JSON_MAX_BYTES = 2_000_000;

const unavailable = (reason: string): ArtifactPreviewData => ({ kind: 'unavailable', reason });

/** The first `bytes` of a file, through the server's file handler, which honours a range. */
async function head(url: string, bytes: number, signal: AbortSignal): Promise<string> {
  const settings = ServerConnection.makeSettings();
  const init = { headers: { Range: `bytes=0-${bytes - 1}` }, signal };
  const response = await ServerConnection.makeRequest(url, init, settings);
  if (!response.ok) throw await ServerConnection.ResponseError.create(response);
  return response.text();
}

const isRow = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

async function preview(
  output: ResolvedOutput,
  binding: ArtifactBinding,
  signal: AbortSignal
): Promise<ArtifactPreviewData> {
  const format = output.format ?? '';
  const size = output.artifact?.byteSize ?? 0;
  const url = URLExt.join(PageConfig.getBaseUrl(), 'files', URLExt.encodeParts(binding.path));
  if (output.type === 'figure' && IMAGE_FORMATS.has(format)) {
    return { kind: 'image', url: `${url}?${binding.cacheToken}` };
  }
  const delimiter = DELIMITERS.get(format);
  if (output.type === 'table' && delimiter !== undefined) {
    const sample = await head(url, TABLE_SAMPLE_BYTES, signal);
    return tablePreviewFromDelimited(sample, { delimiter, sourceTruncated: size > TABLE_SAMPLE_BYTES });
  }
  if ((output.type === 'table' || output.type === 'metric') && format === 'json') {
    if (size > JSON_MAX_BYTES) return unavailable('The JSON artifact exceeds the preview limit.');
    let value: unknown;
    try {
      value = JSON.parse(await head(url, JSON_MAX_BYTES, signal));
    } catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
    }
    const data =
      output.type === 'metric'
        ? metricPreviewFromJson(value)
        : Array.isArray(value) && value.every(isRow)
          ? tablePreviewFromRows(value)
          : undefined;
    return data ?? unavailable(`The file is not a JSON ${output.type}.`);
  }
  return unavailable(
    format ? `No preview is available for .${format} artifacts.` : 'This output does not declare an artifact format.'
  );
}

/** Loads once: its key is the file's cache token, so a new version remounts it. */
function Preview({ output, binding, compact }: {
  output: ResolvedOutput;
  binding: ArtifactBinding;
  compact: boolean;
}) {
  const [data, setData] = useState<ArtifactPreviewData>({ kind: 'loading' });
  useEffect(() => {
    const controller = new AbortController();
    preview(output, binding, controller.signal)
      .catch((error: unknown) => unavailable(errorMessage(error)))
      .then(value => {
        if (!controller.signal.aborted) setData(value);
      });
    return () => controller.abort();
  }, []);
  return <ArtifactPreview output={output} preview={data} compact={compact} caption={null} />;
}

/**
 * Bounded previews through astra-ui's artifact slot. An output the
 * resolution did not find on disk keeps astra-ui's own placeholder.
 */
export function artifactRenderer(bindings: ReadonlyMap<string, ArtifactBinding>): ArtifactRenderer {
  return (output, { compact }) => {
    const binding = bindings.get(output.canonicalPath);
    return binding ? (
      <Preview key={`${output.canonicalPath}:${binding.cacheToken}`} output={output} binding={binding} compact={compact} />
    ) : (
      <ArtifactPreview output={output} compact={compact} caption={null} />
    );
  };
}
