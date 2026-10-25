// Bundle the page into the Python package, where `lc view` serves it from.
// Everything the page needs at runtime lands here — scripts, styles and the
// KaTeX fonts — so a viewer works with no network.
import { readdir, readFile, rm, writeFile } from 'node:fs/promises';
import { build } from 'esbuild';

const outdir = new URL('../src/lightcone/engine/view/static/', import.meta.url).pathname;

await rm(outdir, { recursive: true, force: true });
const { metafile } = await build({
  entryPoints: { main: 'src/main.tsx' },
  outdir,
  bundle: true,
  minify: true,
  format: 'esm',
  target: ['chrome111', 'safari16.2', 'firefox113'],
  jsx: 'automatic',
  define: { 'process.env.NODE_ENV': '"production"' },
  loader: { '.woff2': 'file', '.woff': 'file', '.ttf': 'file' },
  assetNames: 'fonts/[name]-[hash]',
  legalComments: 'none',
  metafile: true,
  logLevel: 'warning',
});

// The licence of every package whose bytes the bundle carries, read from the
// metafile so the list is what shipped rather than what was installed.
const packages = new Map();
for (const input of Object.keys(metafile.inputs)) {
  const at = input.lastIndexOf('node_modules/');
  if (at < 0) continue;
  const parts = input.slice(at + 'node_modules/'.length).split('/');
  const name = parts[0].startsWith('@') ? `${parts[0]}/${parts[1]}` : parts[0];
  packages.set(name, `${input.slice(0, at)}node_modules/${name}`);
}
const notices = [];
for (const [name, dir] of [...packages].sort(([a], [b]) => a.localeCompare(b))) {
  const { version, license } = JSON.parse(await readFile(`${dir}/package.json`, 'utf8'));
  const file = (await readdir(dir)).find((entry) => /^(licen[cs]e|copying)/i.test(entry));
  if (!file) throw new Error(`${name} ships no licence file to include in the notices.`);
  notices.push(`${name}@${version} (${license})\n\n${(await readFile(`${dir}/${file}`, 'utf8')).trim()}\n`);
}
await writeFile(`${outdir}THIRD-PARTY-NOTICES.txt`, notices.join(`\n${'-'.repeat(72)}\n\n`));
