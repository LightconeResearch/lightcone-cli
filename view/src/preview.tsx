// Artifact previews: an output's bytes, read through Jupyter's `/files/` and
// turned into astra-ui's host-safe preview data. Every read is bounded — a
// Range request, cut while read should a server ignore it — so a large output
// never travels whole to show a few rows.
import type { ArtifactBinding, ResolvedOutput } from '@astra-spec/sdk';
import { ArtifactPreview } from '@astra-spec/ui/components';
import {
  metricPreviewFromJson,
  tablePreviewFromDelimited,
  tablePreviewFromRows,
  type ArtifactPreviewData,
} from '@astra-spec/ui/lib';
import { useEffect, useState } from 'react';

const SAMPLE_BYTES = 65_536;
const JSON_MAX_BYTES = 2_000_000;
const TABLE_LIMITS = { maxRows: 30, maxColumns: 30 };

const IMAGES = new Set(['avif', 'gif', 'jpeg', 'jpg', 'png', 'svg', 'webp']);
const DELIMITERS = new Map([
  ['csv', ','],
  ['tsv', '\t'],
]);
const TEXTS = new Map<string, string | undefined>([
  ['md', 'markdown'],
  ['txt', undefined],
  ['log', undefined],
  ['tex', 'latex'],
  ['yaml', 'yaml'],
  ['yml', 'yaml'],
  ['toml', 'toml'],
]);

// What a clone holds in place of annexed content it has not fetched: a small
// file whose bytes begin with this. Shown as an image it is a broken icon, and
// as text it is a key, so it is said for what it is.
const ANNEX_POINTER = '/annex/objects/';
const ANNEX_POINTER_MAX_BYTES = 32_768;

interface Sample {
  text: string;
  truncated: boolean;
}

async function readSample(url: string, maxBytes: number, size: number): Promise<Sample> {
  const response = await fetch(url, { headers: { Range: `bytes=0-${maxBytes - 1}` } });
  if (!response.ok || !response.body) {
    throw new Error(`The artifact could not be read (${response.status}).`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let text = '';
  let read = 0;
  try {
    while (read < maxBytes) {
      const { value, done } = await reader.read();
      if (done) break;
      const chunk = value.subarray(0, maxBytes - read);
      read += chunk.byteLength;
      text += decoder.decode(chunk, { stream: true });
    }
  } finally {
    await reader.cancel();
  }
  return { text: text + decoder.decode(), truncated: size > maxBytes };
}

function asText(sample: Sample, language?: string): ArtifactPreviewData {
  return { kind: 'text', text: sample.text, language, truncated: sample.truncated };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function previewFor(
  output: ResolvedOutput,
  binding: ArtifactBinding,
  url: string,
  size: number,
): Promise<ArtifactPreviewData> {
  if (
    size <= ANNEX_POINTER_MAX_BYTES &&
    (await readSample(url, ANNEX_POINTER.length, size)).text === ANNEX_POINTER
  ) {
    return {
      kind: 'unavailable',
      reason: `Not in this clone; fetch it with git annex get ${binding.path}`,
    };
  }
  const format = (output.format ?? '').replace(/^\./, '').toLowerCase();
  if (IMAGES.has(format)) {
    return { kind: 'image', url, alt: output.label ?? output.id };
  }
  const delimiter = DELIMITERS.get(format);
  if (delimiter !== undefined) {
    const sample = await readSample(url, SAMPLE_BYTES, size);
    return tablePreviewFromDelimited(sample.text, {
      delimiter,
      ...TABLE_LIMITS,
      sourceTruncated: sample.truncated,
    });
  }
  if (format === 'json') {
    if (size > JSON_MAX_BYTES) return asText(await readSample(url, SAMPLE_BYTES, size), 'json');
    const sample = await readSample(url, JSON_MAX_BYTES, size);
    let value: unknown;
    try {
      value = JSON.parse(sample.text);
    } catch {
      return asText(sample, 'json');
    }
    const metric = output.type === 'metric' ? metricPreviewFromJson(value) : undefined;
    if (metric) return metric;
    if (Array.isArray(value) && value.length > 0 && value.every(isRecord)) {
      return tablePreviewFromRows(value, TABLE_LIMITS);
    }
    const pretty = JSON.stringify(value, null, 2);
    const truncated = pretty.length > SAMPLE_BYTES;
    return asText({ text: pretty.slice(0, SAMPLE_BYTES), truncated }, 'json');
  }
  if (TEXTS.has(format)) {
    return asText(await readSample(url, SAMPLE_BYTES, size), TEXTS.get(format));
  }
  return {
    kind: 'unavailable',
    reason: format ? `No preview for .${format} artifacts.` : 'The output declares no format.',
  };
}

// One read per artifact version for the page's lifetime: a card and its
// dialog show the same bytes, and the binding's cache token is in the URL.
const previews = new Map<string, Promise<ArtifactPreviewData>>();

function load(output: ResolvedOutput, binding: ArtifactBinding, url: string, size: number) {
  let pending = previews.get(url);
  if (!pending) {
    pending = previewFor(output, binding, url, size).catch(
      (error: unknown): ArtifactPreviewData => ({
        kind: 'unavailable',
        reason: error instanceof Error ? error.message : String(error),
      }),
    );
    previews.set(url, pending);
  }
  return pending;
}

/** The preview of one output's artifact, read from the server at `files`. */
export function Preview({
  output,
  binding,
  files,
  compact,
}: {
  output: ResolvedOutput;
  binding: ArtifactBinding | undefined;
  files: string;
  compact: boolean;
}) {
  const size = output.artifact?.byteSize;
  const url =
    binding &&
    `${files}${binding.path.split('/').map(encodeURIComponent).join('/')}` +
      `?v=${encodeURIComponent(binding.cacheToken)}`;
  const [preview, setPreview] = useState<ArtifactPreviewData>({ kind: 'loading' });
  useEffect(() => {
    if (!binding || !url || size === undefined) return;
    let current = true;
    void load(output, binding, url, size).then((value) => current && setPreview(value));
    return () => {
      current = false;
    };
  }, [output, binding, url, size]);

  return (
    <ArtifactPreview
      output={output}
      preview={
        binding && url && size !== undefined
          ? preview
          : { kind: 'unavailable', reason: 'This output has not been materialized.' }
      }
      compact={compact}
      caption={null}
    />
  );
}
