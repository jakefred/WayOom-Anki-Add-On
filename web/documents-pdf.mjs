import { getDocument, GlobalWorkerOptions } from 'pdfjs-dist/legacy/build/pdf.mjs';
import { EnglishOcr } from './ocr-worker.mjs';
import { parsePageSelection, checkedSource, cleanDocumentText, checkCanceled } from './documents.mjs';

export async function openPdf(bytes, { signal, asset }) {
  GlobalWorkerOptions.workerSrc = asset('pdf.worker.mjs');
  const task = getDocument({ data: bytes, isEvalSupported: false, enableXfa: false,
    cMapUrl: asset('cmaps/'), cMapPacked: true, standardFontDataUrl: asset('fonts/'),
    wasmUrl: asset('wasm/'), useSystemFonts: false, verbosity: 0, stopAtErrors: true,
    maxImageSize: 16000000 });
  let pdf, worker, renderTask;
  let destroyed = false;
  const destroy = async () => {
    if (destroyed) return;
    destroyed = true;
    renderTask?.cancel();
    worker?.close();
    await task.destroy().catch(() => {});
  };
  const abort = () => { void destroy(); };
  signal?.addEventListener('abort', abort, { once: true });
  task.onPassword = () => { void destroy(); };
  try {
    pdf = await task.promise;
    checkCanceled(signal);
    if (!pdf.numPages || pdf.numPages > 500) throw new Error('Choose a PDF with 1–500 pages.');
  } catch (error) {
    await destroy();
    checkCanceled(signal);
    throw new Error(error.message?.startsWith('Choose') ? error.message : 'This PDF could not be opened. Use an unencrypted PDF and try exporting it again.');
  }
  return { kind: 'pages', count: pdf.numPages, destroy,
    async read(selection, { forceOcr = false, onProgress = () => {} } = {}) {
      const pages = parsePageSelection(selection, pdf.numPages);
      const content = [], ocrPages = [], emptyPages = [];
      for (const n of pages) {
        checkCanceled(signal);
        onProgress(`Reading page ${n} (${content.length + 1} of ${pages.length})…`);
        const page = await pdf.getPage(n);
        let text = '';
        if (!forceOcr) {
          try {
            const result = await page.getTextContent();
            text = cleanDocumentText(result.items.map(item => typeof item.str === 'string' ? `${item.str}${item.hasEOL ? '\n' : ' '}` : '').join(''));
          } catch { checkCanceled(signal); } // A broken text layer may still render for OCR.
        }
        if (forceOcr || !text.replace(/[\s\p{Cf}]/gu, '')) {
          onProgress(`Recognizing English text on page ${n}…`);
          if (worker?.closed) worker = null;
          if (!worker) {
            worker = new EnglishOcr(asset, signal);
            await worker.initialize();
            checkCanceled(signal);
          }
          const size = page.getViewport({ scale: 1 });
          const scale = Math.min(2.5, Math.sqrt(4000000 / (size.width * size.height)));
          const viewport = page.getViewport({ scale });
          const canvas = document.createElement('canvas');
          canvas.width = Math.max(1, Math.floor(viewport.width)); canvas.height = Math.max(1, Math.floor(viewport.height));
          try {
            renderTask = page.render({ canvasContext: canvas.getContext('2d'), viewport });
            await renderTask.promise;
            text = cleanDocumentText(await worker.recognize(canvas));
            ocrPages.push(n);
          } finally { canvas.width = canvas.height = 0; renderTask = null; }
        }
        checkCanceled(signal);
        if (!text.replace(/[\s\p{Cf}]/gu, '')) emptyPages.push(n);
        content.push(`[Page ${n}]\n${text}`);
        if ([...content.join('\n\n')].length > 60000) throw new Error('Selected content exceeds 60,000 characters. Choose fewer pages.');
        page.cleanup();
      }
      if (emptyPages.length === pages.length) throw new Error('No text was recognized. Try a clearer scan or a different document.');
      return { text: checkedSource(content.join('\n\n')), ocrPages, emptyPages };
    },
  };
}
