import { build } from 'esbuild';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { dirname } from 'node:path';

// Qt 6's Chromium supports this conservative target. Real Qt verification
// remains a release gate; no Node runtime or CDN is needed by the bundle.
await build({
  entryPoints: ['./web/panel.mjs'],
  tsconfigRaw: {},
  outfile: 'web/dist/panel.js',
  bundle: true,
  platform: 'browser',
  target: 'chrome100',
  format: 'iife',
  globalName: 'WayOomPanel',
  legalComments: 'eof',
  banner: { js: '/*! WayOom | Copyright (C) 2026 WayOom contributors | SPDX-License-Identifier: AGPL-3.0-or-later | See LICENSE and NOTICE. */' },
  metafile: true,
}).then(result => {
  if (Object.values(result.metafile.outputs).some(output => output.imports.length)) {
    throw new Error('The panel bundle must be self-contained');
  }
});

// Load the PDF/OCR code only when a PDF is attached. Every worker, font,
// decoder and English language asset is pinned and served by the add-on.
await build({ entryPoints: ['./web/documents-pdf.mjs'], outfile: 'web/dist/documents-pdf.js',
  tsconfigRaw: {}, bundle: true, platform: 'browser', target: 'chrome120', format: 'esm', legalComments: 'eof',
  banner: { js: '/*! WayOom | Copyright (C) 2026 WayOom contributors | SPDX-License-Identifier: AGPL-3.0-or-later | See LICENSE and NOTICE. */' } });
const documentAssets = JSON.parse(await readFile('web/document-assets.json', 'utf8'));
for (const [name, { source, sha256 }] of Object.entries(documentAssets)) {
  const bytes = await readFile(source);
  if (createHash('sha256').update(bytes).digest('hex') !== sha256) throw new Error(`Document asset checksum mismatch: ${name}`);
  const output = `web/dist/documents/${name}`;
  await mkdir(dirname(output), { recursive: true });
  await writeFile(output, bytes);
}
