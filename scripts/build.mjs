import { build } from 'esbuild';

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
  metafile: true,
}).then(result => {
  if (Object.values(result.metafile.outputs).some(output => output.imports.length)) {
    throw new Error('The panel bundle must be self-contained');
  }
});
