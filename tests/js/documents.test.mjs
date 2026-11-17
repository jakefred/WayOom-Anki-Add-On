import test from 'node:test';
import assert from 'node:assert/strict';
import { zipSync, strToU8 } from 'fflate';
import { openDocument, parsePageSelection, readOfficeArchive, readOfficeImages, resolveImageTarget, checkedSource } from '../../web/documents.mjs';
import { imageInfo, createImageReader } from '../../web/documents-images.mjs';
import { requestSchema } from '../../web/validation.mjs';
import { generationPrompt } from '../../web/generation-provider.mjs';
import { GenerationPanel } from '../../web/form.mjs';
import { panelDocument } from './panel-dom.mjs';
import { LocalOcr } from '../../web/ocr-worker.mjs';
import { normalizeOcrLanguages } from '../../web/ocr-languages.mjs';

const base = { requestId: 'document-job', destination: { mode: 'existing', deckId: 1 }, cardCount: 3,
  topic: '', difficulty: 'beginner', providerId: 'openai' };
const source = { name: 'Lecture.txt', selection: 'Whole document', text: 'A cell membrane regulates transport.' };

test('automatic difficulty normalizes to null and produces guidance without an implicit fixed level', () => {
  const { difficulty, ...input } = { ...base, topic: 'Cell biology' };
  const request = requestSchema.parse(input);
  assert.equal(request.difficulty, null);
  const prompt = generationPrompt({ request, stage: 'cards', count: 3 }).prompt;
  assert.match(prompt, /No difficulty level is specified/);
  assert.doesNotMatch(prompt, /undefined|Difficulty: intermediate/);
});

test('page ranges are bounded, deduplicated, sorted, and reject invalid/out-of-range selections', () => {
  assert.deepEqual(parsePageSelection(' 5, 1-3, 2 ', 6), [1, 2, 3, 5]);
  assert.deepEqual(parsePageSelection('', 3), [1, 2, 3]);
  for (const value of ['0', '3-2', '7', '1,,3', '2.5', 'all', '-1']) assert.throws(() => parsePageSelection(value, 6));
  assert.throws(() => parsePageSelection('', 500), /50/);
  assert.deepEqual(parsePageSelection('500', 500), [500]);
});

test('text, CSV, TSV and Markdown extraction preserves content and decodes Unicode BOMs', async () => {
  for (const ext of ['txt', 'csv', 'tsv', 'md']) {
    const doc = await openDocument(new File(['question,answer\n"Cell, membrane",Transport'], `input.${ext}`));
    assert.equal((await doc.read()).text, 'question,answer\n"Cell, membrane",Transport');
    assert.equal(doc.count, null);
  }
  const doc = await openDocument(new File([new Uint8Array([255, 254, 65, 0, 66, 0])], 'unicode.txt'));
  assert.equal((await doc.read()).text, 'AB');
  await assert.rejects(openDocument(new File([new Uint8Array([0xff])], 'invalid.txt')), /UTF/);
  await assert.rejects(openDocument(new File(['old format'], 'old.ppt')), /PPTX/);
  await assert.rejects(openDocument({ name: 'huge.pdf', size: 31 * 1024 * 1024 }), /30 MB/);
  assert.throws(() => checkedSource('x'.repeat(60001)), /60,000/);
  assert.throws(() => checkedSource('\n\u200b'), /No readable/);
});

test('Office ZIP selection ignores scripts/images and rejects oversized XML before decompression', () => {
  const bytes = zipSync({ 'word/document.xml': strToU8('<document/>'), 'word/vbaProject.bin': strToU8('do not run'),
    'word/media/image.png': strToU8('not text') });
  assert.deepEqual(Object.keys(readOfficeArchive(bytes)), ['word/document.xml']);
  const large = zipSync({ 'word/document.xml': new Uint8Array(8 * 1024 * 1024 + 1) });
  assert.throws(() => readOfficeArchive(large), /too large/);
});

test('only explicitly selected Office media is inflated; paths, counts, and image sizes are bounded', () => {
  const bytes = zipSync({ 'word/media/a.png': strToU8('selected'), 'word/media/unused.jpg': strToU8('unused'),
    'word/vbaProject.bin': strToU8('macro'), 'word/_rels/document.xml.rels': strToU8('<Relationships/>') });
  assert.deepEqual(Object.keys(readOfficeImages(bytes, ['word/media/a.png'])), ['word/media/a.png']);
  assert.deepEqual(Object.keys(readOfficeImages(bytes, [])), []);
  assert.ok(readOfficeArchive(bytes)['word/_rels/document.xml.rels']);
  assert.throws(() => readOfficeImages(bytes, ['../outside.png']), /reference/);
  assert.throws(() => readOfficeImages(bytes, Array.from({ length: 51 }, (_, n) => `word/media/${n}.png`)), /50/);
  const large = zipSync({ 'word/media/big.png': new Uint8Array(16 * 1024 * 1024 + 1) });
  assert.throws(() => readOfficeImages(large, ['word/media/big.png']), /too large/);
  assert.deepEqual(Object.keys(readOfficeImages(large, ['word/media/other.png'])), []);
  assert.equal(resolveImageTarget('ppt/slides/slide2.xml', '../media/a.png'), 'ppt/media/a.png');
  assert.equal(resolveImageTarget('word/document.xml', 'media/a%20b.jpeg'), 'word/media/a b.jpeg');
  for (const target of ['https://example.com/a.png', '//example.com/a.png', '../../outside.png', '../ppt/media/a.png',
    'media/a.svg', 'media/a.png?url=1', 'media/%2e%2e/%2e%2e/outside.png', 'media/%5ca.png']) {
    assert.equal(resolveImageTarget('word/document.xml', target), null, target);
  }
});

function pngHeader(width = 200, height = 100) {
  const bytes = new Uint8Array(32);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10]); bytes.set(strToU8('IHDR'), 12);
  const view = new DataView(bytes.buffer); view.setUint32(16, width); view.setUint32(20, height);
  return bytes;
}

test('PNG, JPEG and WebP dimensions are checked before decoding or starting OCR', () => {
  assert.deepEqual(imageInfo(pngHeader()), { width: 200, height: 100, mime: 'image/png' });
  assert.deepEqual(imageInfo(new Uint8Array([255,216,255,192,0,8,8,0,100,0,200,1,255,217])),
    { width: 200, height: 100, mime: 'image/jpeg' });
  const webp = new Uint8Array(30);
  webp.set(strToU8('RIFF'), 0); webp.set(strToU8('WEBPVP8X'), 8); webp[24] = 199; webp[27] = 99;
  assert.deepEqual(imageInfo(webp), { width: 200, height: 100, mime: 'image/webp' });
  webp[20] = 2; assert.throws(() => imageInfo(webp), /Animated/);
  for (const bytes of [strToU8('<svg/>'), new Uint8Array(10), pngHeader(0, 100)]) assert.throws(() => imageInfo(bytes), /valid PNG/);
  assert.throws(() => imageInfo(pngHeader(10001, 1)), /too large/);
  assert.throws(() => imageInfo(pngHeader(5000, 5000)), /too large/);
});

test('canceling during image decode releases the bitmap before creating an OCR worker', async t => {
  const previous = globalThis.createImageBitmap;
  t.after(() => { globalThis.createImageBitmap = previous; });
  let finish, closed = 0;
  globalThis.createImageBitmap = () => new Promise(resolve => { finish = resolve; });
  const controller = new AbortController();
  const reader = createImageReader({ signal: controller.signal, asset: () => { throw new Error('OCR must not start'); } });
  const pending = reader.recognize(pngHeader());
  controller.abort(); finish({ width: 200, height: 100, close() { closed++; } });
  await assert.rejects(pending, /canceled/);
  assert.equal(closed, 1);
  reader.destroy();
});

test('document generation accepts optional guidance, keeps source separate, and rejects malformed sources', () => {
  assert.equal(requestSchema.parse({ ...base, source }).topic, '');
  assert.equal(requestSchema.safeParse(base).success, false);
  for (const bad of [{ ...source, text: '' }, { ...source, text: 'a'.repeat(60001) }, { ...source, path: '/secret' }, null]) {
    assert.equal(requestSchema.safeParse({ ...base, source: bad }).success, false);
  }
  const prompt = generationPrompt({ request: { ...base, topic: 'Focus on transport', source }, stage: 'cards', count: 3 });
  assert.match(prompt.system, /untrusted source material/);
  assert.match(prompt.system, /Do not invent unsupported facts/);
  assert.match(prompt.prompt, /Focus on transport/);
  assert.match(prompt.prompt, /A cell membrane regulates transport/);
});

test('attachment selection gates Generate, includes instructions, invalidates drafts, and clears on profile switch', async () => {
  const { document, elements } = panelDocument();
  const requests = [];
  let readingOptions;
  const engine = { discard() {}, active: null, async run(request) { requests.push(request); return { outcome: { count: 3, deckName: 'Cells' } }; } };
  const panel = new GenerationPanel(document, { async rpc() { return { decks: [{ deckId: 1, name: 'Cells' }] }; } }, engine, () => {}, {
    openDocument: async () => ({ kind: 'pages', count: 3, supportsImageOcr: true,
      async read(selection, options) { assert.equal(selection, '2'); readingOptions = options; return { text: source.text, ocrPages: [2] }; }, destroy() {} }),
  });
  panel.setState({ profileEpoch: 1, profileReady: true, configuredProvider: 'openai', canGenerate: true, state: 'ready' });
  await new Promise(resolve => setImmediate(resolve));
  elements['#destination'].value = '1';
  await panel.documents.attach(new File(['placeholder'], 'Lecture.pdf'));
  assert.equal(elements['#document-ocr-option'].hidden, false);
  assert.equal(elements['#document-language'].children.length, 13);
  assert.equal(elements['#document-local-hint'].hidden, false);
  assert.equal(panel.available(), false);
  elements['#document-pages'].value = '2';
  await panel.documents.read();
  assert.equal(panel.available(), true);
  assert.match(elements['#document-status'].textContent, /English OCR/);
  await panel.submit();
  assert.equal(requests[0].topic, '');
  assert.deepEqual(requests[0].source, { ...source, name: 'Lecture.pdf', selection: 'Pages 2' });
  elements['#document-ocr'].checked = true; elements['#document-ocr'].dispatch('change');
  assert.equal(panel.documents.source, null);
  assert.equal(panel.available(), false);
  await panel.documents.read();
  assert.equal(readingOptions.scanImages, true);
  assert.equal(readingOptions.forceOcr, true);
  elements['#document-language'].value = 'ara'; elements['#document-language'].dispatch('change');
  assert.equal(panel.documents.source, null);
  assert.equal(elements['#document-english-option'].hidden, false);
  elements['#document-english'].checked = true; elements['#document-english'].dispatch('change');
  await panel.documents.read();
  assert.deepEqual(readingOptions.ocrLanguages, ['ara', 'eng']);
  assert.equal(elements['#document-preview'].dir, 'rtl');
  elements['#document-pages'].dispatch('input');
  assert.equal(panel.documents.source, null);
  assert.equal(panel.available(), false);
  panel.setState({ profileEpoch: 2 });
  assert.equal(panel.documents.file, null);
  assert.equal(elements['#document-preview'].value, '');
  assert.equal(elements['#document-ocr-option'].hidden, true);
  assert.equal(elements['#document-local-hint'].hidden, true);
  assert.equal(elements['#document-language'].value, 'eng');
});

test('canceled document loads cannot restore a stale attachment', async () => {
  const { document } = panelDocument();
  let finish;
  let destroyed = 0;
  const engine = { discard() {}, active: null };
  const panel = new GenerationPanel(document, {}, engine, () => {}, {
    openDocument: () => new Promise(resolve => { finish = resolve; }),
  });
  const loading = panel.documents.attach(new File(['x'], 'late.pdf'));
  panel.documents.clear();
  finish({ count: 1, kind: 'pages', destroy() { destroyed++; } });
  await loading;
  assert.equal(panel.documents.file, null);
  assert.equal(panel.documents.source, null);
  assert.equal(destroyed, 1);
});

test('OCR can terminate during language initialization and rejects worker failures', async () => {
  class FakeWorker {
    postMessage(packet) { this.packet = packet; }
    terminate() { this.terminated = true; }
    finish(status) { this.onmessage({ data: { jobId: this.packet.jobId, status } }); }
  }
  const controller = new AbortController();
  const ocr = new LocalOcr(name => name, controller.signal, ['jpn', 'eng'], FakeWorker);
  const pending = ocr.initialize();
  ocr.worker.finish('resolve');
  await Promise.resolve();
  assert.equal(ocr.worker.packet.action, 'loadLanguage');
  assert.deepEqual(ocr.worker.packet.payload.langs, ['jpn', 'eng']);
  controller.abort();
  await assert.rejects(pending, /canceled/);
  assert.equal(ocr.worker.terminated, true);
  const failed = new LocalOcr(name => name, undefined, ['eng'], FakeWorker);
  const failure = failed.initialize();
  failed.worker.finish('resolve');
  await Promise.resolve();
  failed.worker.finish('reject');
  await assert.rejects(failure, /OCR could not/);
  assert.equal(failed.worker.terminated, true);
});

test('OCR catalog accepts bundled primary languages with optional English only', () => {
  assert.deepEqual(normalizeOcrLanguages(['ara', 'eng']), ['ara', 'eng']);
  for (const languages of [['eng', 'ara'], ['spa', 'fra'], ['unknown'], []]) {
    assert.throws(() => normalizeOcrLanguages(languages), /supported OCR language/);
  }
});
