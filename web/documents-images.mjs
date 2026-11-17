import { LocalOcr } from './ocr-worker.mjs';
import { normalizeOcrLanguages, ocrLanguageLabel } from './ocr-languages.mjs';
import { checkCanceled, checkedSource, cleanDocumentText } from './documents.mjs';

// Check dimensions before asking the browser to decode untrusted image bytes.
export function imageInfo(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const word = (offset, n) => String.fromCharCode(...bytes.slice(offset, offset + n));
  let width, height, mime;
  if (bytes.length >= 24 && bytes[0] === 137 && word(1, 7) === 'PNG\r\n\x1a\n' && word(12, 4) === 'IHDR') {
    width = view.getUint32(16); height = view.getUint32(20); mime = 'image/png';
  } else if (bytes.length >= 10 && bytes[0] === 255 && bytes[1] === 216) {
    for (let p = 2; p + 8 < bytes.length;) {
      if (bytes[p++] !== 255) break;
      while (bytes[p] === 255) p++;
      const marker = bytes[p++];
      if (marker === 217 || marker === 218) break;
      if (marker === 1 || marker >= 208 && marker <= 215) continue;
      if (p + 2 > bytes.length) break;
      const size = view.getUint16(p);
      if (size < 2 || p + size > bytes.length) break;
      if ([192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207].includes(marker) && size >= 8) {
        height = view.getUint16(p + 3); width = view.getUint16(p + 5); mime = 'image/jpeg'; break;
      }
      p += size;
    }
  } else if (bytes.length >= 30 && word(0, 4) === 'RIFF' && word(8, 4) === 'WEBP') {
    const type = word(12, 4);
    if (type === 'VP8X') {
      if (bytes[20] & 2) throw new Error('Animated images are not supported. Use a static PNG, JPEG, or WebP.');
      width = 1 + bytes[24] + (bytes[25] << 8) + (bytes[26] << 16);
      height = 1 + bytes[27] + (bytes[28] << 8) + (bytes[29] << 16);
    } else if (type === 'VP8L' && bytes[20] === 47) {
      width = 1 + ((bytes[21] | bytes[22] << 8) & 16383);
      height = 1 + ((bytes[22] >> 6 | bytes[23] << 2 | bytes[24] << 10) & 16383);
    } else if (type === 'VP8 ' && bytes[23] === 157 && bytes[24] === 1 && bytes[25] === 42) {
      width = view.getUint16(26, true) & 16383; height = view.getUint16(28, true) & 16383;
    }
    mime = 'image/webp';
  }
  if (!width || !height || !mime) throw new Error('This image could not be read. Use a valid PNG, JPEG, or WebP file.');
  if (width > 10000 || height > 10000 || width * height > 16000000) {
    throw new Error('This image is too large to scan. Resize it to at most 16 megapixels and 10,000 pixels per side.');
  }
  return { width, height, mime };
}

export function createImageReader({ signal, asset }) {
  let worker, closed = false;
  return {
    destroy() { closed = true; worker?.close(); },
    async recognize(bytes, ocrLanguages = ['eng']) {
      const languages = normalizeOcrLanguages(ocrLanguages);
      checkCanceled(signal);
      if (closed) throw new Error('Document reading canceled.');
      const info = imageInfo(bytes);
      let bitmap;
      try { bitmap = await createImageBitmap(new Blob([bytes], { type: info.mime })); }
      catch { checkCanceled(signal); throw new Error('This image could not be decoded. Export it as PNG or JPEG and try again.'); }
      let canvas;
      try {
        checkCanceled(signal);
        if (closed) throw new Error('Document reading canceled.');
        const scale = Math.min(2, Math.sqrt(4000000 / (bitmap.width * bitmap.height)));
        canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.floor(bitmap.width * scale));
        canvas.height = Math.max(1, Math.floor(bitmap.height * scale));
        const context = canvas.getContext('2d');
        context.fillStyle = '#fff'; context.fillRect(0, 0, canvas.width, canvas.height);
        context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        bitmap.close(); bitmap = null;
        if (worker && worker.languageKey !== languages.join('+')) { worker.close(); worker = null; }
        if (!worker || worker.closed) { worker = new LocalOcr(asset, signal, languages); await worker.initialize(); }
        checkCanceled(signal);
        const text = cleanDocumentText(await worker.recognize(canvas));
        checkCanceled(signal);
        return text;
      } finally { bitmap?.close(); if (canvas) canvas.width = canvas.height = 0; }
    },
  };
}

export function openImage(bytes, options) {
  imageInfo(bytes);
  const reader = createImageReader(options);
  return { kind: 'image', count: null, destroy: () => reader.destroy(),
    async read(_selection, { ocrLanguages = ['eng'], onProgress = () => {} } = {}) {
      const languages = normalizeOcrLanguages(ocrLanguages);
      onProgress(`Recognizing ${ocrLanguageLabel(languages)} text in image…`);
      return { text: checkedSource(await reader.recognize(bytes, languages)), ocrImages: 1 };
    },
  };
}
