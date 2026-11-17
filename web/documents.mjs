import { unzipSync, strFromU8 } from 'fflate';

export const MAX_SOURCE_CHARACTERS = 60000;
export const MAX_FILE_BYTES = 30 * 1024 * 1024;
export const MAX_SELECTED_PAGES = 50;
export const MAX_OCR_IMAGES = 50;
let assetBase;
export function configureDocumentAssets(base) { assetBase = base; }
export function documentAsset(name) {
  if (!assetBase) throw new Error('Document tools are not available. Reopen WayOom.');
  return new URL(name, assetBase).href;
}
export function checkCanceled(signal) {
  if (signal?.aborted) throw new Error('Document reading canceled.');
}
export function cleanDocumentText(text) {
  return text.normalize('NFC').replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/gu, ' ').trim();
}
export function checkedSource(text) {
  text = cleanDocumentText(text);
  if (!text.replace(/[\s\p{Cf}]/gu, '')) throw new Error('No readable text was found in the selected content.');
  if ([...text].length > MAX_SOURCE_CHARACTERS) throw new Error('Selected content exceeds 60,000 characters. Choose fewer pages or use a smaller document.');
  return text;
}
export function parsePageSelection(value, count) {
  if (!Number.isInteger(count) || count < 1) throw new Error('This document has no pages or slides.');
  value = value.trim();
  if (value.length > 256) throw new Error('The page selection is too long.');
  const selected = new Set();
  const pieces = value ? value.split(',') : [`1-${count}`];
  for (const piece of pieces) {
    const match = /^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$/.exec(piece);
    if (!match) throw new Error('Use page numbers or ranges, such as 1-3, 7, 10-12.');
    const first = Number(match[1]), last = Number(match[2] ?? match[1]);
    if (first < 1 || last < first || last > count) throw new Error(`Choose numbers from 1 to ${count}, with ranges in ascending order.`);
    if (last - first + 1 > MAX_SELECTED_PAGES) throw new Error('Select at most 50 pages or slides at a time.');
    for (let n = first; n <= last; n++) selected.add(n);
    if (selected.size > MAX_SELECTED_PAGES) throw new Error('Select at most 50 pages or slides at a time.');
  }
  return [...selected].sort((a, b) => a - b);
}

function xml(text) {
  if (/<!\s*(?:DOCTYPE|ENTITY)/iu.test(text)) throw new Error('This document contains unsupported XML declarations.');
  const doc = new DOMParser().parseFromString(text, 'application/xml');
  if (doc.getElementsByTagName('parsererror').length) throw new Error('The document XML could not be read.');
  return doc;
}
const tags = (node, name) => [...node.getElementsByTagNameNS('*', name)];
function paragraphs(doc) {
  return tags(doc, 'p').map(p => {
    const walk = node => [...node.childNodes].map(child => {
      if (child.nodeType !== 1) return '';
      if (child.localName === 't') return child.textContent;
      if (['br', 'cr'].includes(child.localName)) return '\n';
      if (child.localName === 'tab') return '\t';
      if (child.localName === 'p') return ''; // A nested paragraph is read separately.
      return walk(child);
    }).join('');
    return walk(p);
  }).join('\n');
}

export function readOfficeArchive(bytes) {
  let total = 0, entries = 0;
  // Images are extracted separately, only after opt-in and page/slide selection.
  return unzipSync(bytes, { filter: file => {
    if (++entries > 10000) throw new Error('The document contains too many archive entries.');
    if (!/^(word\/document\.xml|word\/_rels\/document\.xml\.rels|ppt\/presentation\.xml|ppt\/_rels\/presentation\.xml\.rels|ppt\/slides\/slide\d+\.xml|ppt\/slides\/_rels\/slide\d+\.xml\.rels)$/.test(file.name)) return false;
    total += file.originalSize;
    if (file.originalSize > 8 * 1024 * 1024 || total > 24 * 1024 * 1024) throw new Error('The document text is too large to open.');
    return true;
  } });
}

export function readOfficeImages(bytes, paths) {
  const wanted = new Set(paths);
  if (wanted.size > MAX_OCR_IMAGES) throw new Error('Scan at most 50 embedded images at a time. Choose fewer slides or export selected pages to PDF.');
  if ([...wanted].some(path => !/^(word|ppt)\/media\/[^/\\]+\.(png|jpe?g|webp)$/i.test(path))) {
    throw new Error('Unsupported embedded image reference.');
  }
  let total = 0, entries = 0;
  return unzipSync(bytes, { filter: file => {
    if (++entries > 10000) throw new Error('The document contains too many archive entries.');
    if (!wanted.has(file.name)) return false;
    total += file.originalSize;
    if (file.originalSize > 16 * 1024 * 1024 || total > 48 * 1024 * 1024) throw new Error('Embedded images are too large to scan. Export fewer pages to PDF.');
    return true;
  } });
}

export function resolveImageTarget(part, target) {
  if (!target || /[\\:#?\x00-\x1f]/u.test(target)) return null;
  try { target = decodeURIComponent(target); } catch { return null; }
  if (/[\\:#?\x00-\x1f]/u.test(target)) return null;
  const path = target.startsWith('/') ? [] : part.split('/').slice(0, -1);
  for (const piece of target.split('/')) {
    if (piece === '.' || piece === '') continue;
    if (piece === '..') { if (!path.length) return null; path.pop(); }
    else path.push(piece);
  }
  const name = path.join('/');
  return /^(word|ppt)\/media\/[^/]+\.(png|jpe?g|webp)$/i.test(name) && name.startsWith(part.split('/')[0] + '/') ? name : null;
}

function imageReferences(doc, part, files) {
  const relationPath = part.replace(/([^/]+)$/, '_rels/$1.rels');
  const relations = files[relationPath] ? tags(xml(strFromU8(files[relationPath])), 'Relationship') : [];
  const byId = new Map(relations.map(rel => [rel.getAttribute('Id'), rel]));
  let skipped = 0;
  const paths = [];
  for (const node of [...tags(doc, 'blip'), ...tags(doc, 'imagedata')]) {
    const ns = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
    const id = node.getAttributeNS(ns, 'embed') || node.getAttributeNS(ns, 'id');
    const rel = byId.get(id);
    const target = rel?.getAttribute('TargetMode') !== 'External' && /\/image$/.test(rel?.getAttribute('Type') ?? '')
      ? resolveImageTarget(part, rel.getAttribute('Target')) : null;
    if (target) paths.push(target); else skipped++;
  }
  return { paths: [...new Set(paths)], skipped };
}

export async function openDocument(file, { signal } = {}) {
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['pdf', 'docx', 'pptx', 'txt', 'md', 'csv', 'tsv', 'png', 'jpg', 'jpeg', 'webp'].includes(ext)) {
    throw new Error('Choose PDF, DOCX, PPTX, text, PNG, JPEG, or WebP. Export older DOC/PPT files to DOCX/PPTX or PDF first.');
  }
  if (!file.size || file.size > MAX_FILE_BYTES) throw new Error('Choose a nonempty document of 30 MB or less.');
  const bytes = new Uint8Array(await file.arrayBuffer());
  checkCanceled(signal);
  const imageOptions = { signal, asset: name => documentAsset(`documents/${name}`) };
  if (['png', 'jpg', 'jpeg', 'webp'].includes(ext)) {
    const { openImage } = await import(documentAsset('documents-images.js'));
    checkCanceled(signal);
    return openImage(bytes, imageOptions);
  }
  if (ext === 'pdf') {
    const { openPdf } = await import(documentAsset('documents-pdf.js'));
    checkCanceled(signal);
    return openPdf(bytes, { signal, asset: name => documentAsset(`documents/${name}`) });
  }
  if (['docx', 'pptx'].includes(ext)) {
    const files = readOfficeArchive(bytes);
    const read = name => {
      if (!files[name]) throw new Error('The document is missing required content. Export it again and retry.');
      return xml(strFromU8(files[name]));
    };
    let imageReader, closed = false;
    const destroy = () => { closed = true; imageReader?.destroy(); };
    const normalized = text => text.toLowerCase().replace(/\p{Cf}/gu, '').replace(/\s+/gu, ' ').trim();
    const readParts = async (parts, { scanImages = false, ocrLanguages = ['eng'], onProgress = () => {} } = {}) => {
      const { normalizeOcrLanguages, ocrLanguageLabel } = await import('./ocr-languages.mjs');
      const languages = normalizeOcrLanguages(ocrLanguages);
      checkCanceled(signal);
      if (closed) throw new Error('Document reading canceled.');
      const content = parts.map(({ name, n }) => {
        const doc = read(name);
        return { n, text: cleanDocumentText(paragraphs(doc)),
          ...(scanImages ? imageReferences(doc, name, files) : { paths: [], skipped: 0 }) };
      });
      const paths = [...new Set(content.flatMap(part => part.paths))];
      const images = paths.length ? readOfficeImages(bytes, paths) : {};
      let ocrImages = 0, emptyImages = 0, skippedImages = content.reduce((sum, part) => sum + part.skipped, 0);
      const cache = new Map(), seen = new Set(), blocks = [], emptyPages = [];
      for (const part of content) {
        checkCanceled(signal);
        const pieces = part.text ? [part.text] : [];
        for (const path of part.paths) {
          checkCanceled(signal);
          if (!images[path]) { skippedImages++; continue; }
          if (!cache.has(path)) {
            onProgress(`Scanning image ${ocrImages + 1} of ${paths.length} with ${ocrLanguageLabel(languages)} OCR…`);
            if (!imageReader) {
              const { createImageReader } = await import(documentAsset('documents-images.js'));
              checkCanceled(signal);
              if (closed) throw new Error('Document reading canceled.');
              imageReader = createImageReader(imageOptions);
            }
            const text = await imageReader.recognize(images[path], languages);
            cache.set(path, text); ocrImages++;
            if (!normalized(text)) emptyImages++;
          }
          const text = cache.get(path), key = normalized(text);
          if (key && !normalized(part.text).includes(key) && !seen.has(key)) {
            pieces.push(`[Image ${seen.size + 1}]\n${text}`); seen.add(key);
          }
        }
        const text = pieces.join('\n\n');
        if (!text.replace(/[\s\p{Cf}]/gu, '')) emptyPages.push(part.n);
        blocks.push(ext === 'pptx' ? `[Slide ${part.n}]\n${text}` : text);
        if ([...blocks.join('\n\n')].length > MAX_SOURCE_CHARACTERS) throw new Error('Selected content exceeds 60,000 characters. Choose fewer slides or use a smaller document.');
      }
      checkCanceled(signal);
      if (emptyPages.length === content.length) {
        throw new Error(scanImages ? `No readable text was found.${skippedImages ? ` ${skippedImages} unsupported, linked, or missing image(s) could not be scanned.` : ''} Try clearer images or export the selected content to PDF.`
          : 'No readable text was found. If this document contains images, enable image scanning and read it again.');
      }
      return { text: checkedSource(blocks.join('\n\n')), ocrImages, emptyImages, skippedImages,
        emptyPages: ext === 'pptx' ? emptyPages : [] };
    };
    if (ext === 'docx') return { kind: 'document', count: null, supportsImageOcr: true, destroy,
      read: async (_selection, options) => readParts([{ name: 'word/document.xml', n: 1 }], options) };
    const relationships = new Map(tags(read('ppt/_rels/presentation.xml.rels'), 'Relationship')
      .filter(rel => rel.getAttribute('TargetMode') !== 'External')
      .map(rel => [rel.getAttribute('Id'), rel.getAttribute('Target')]));
    const slides = tags(read('ppt/presentation.xml'), 'sldId').map(slide => {
      const id = slide.getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships', 'id');
      const target = relationships.get(id)?.replace(/^\/ppt\//, '').replace(/^\.\//, '');
      if (!/^slides\/slide\d+\.xml$/.test(target ?? '')) throw new Error('The presentation contains an unsupported slide reference.');
      return `ppt/${target}`;
    });
    if (!slides.length || slides.length > 500) throw new Error('Choose a presentation with 1–500 slides.');
    return { kind: 'slides', count: slides.length, supportsImageOcr: true, destroy, async read(selection, options = {}) {
      const pages = parsePageSelection(selection, slides.length);
      return readParts(pages.map(n => ({ name: slides[n - 1], n })), options);
    } };
  }
  let encoding = 'utf-8';
  if (bytes[0] === 255 && bytes[1] === 254) encoding = 'utf-16le';
  else if (bytes[0] === 254 && bytes[1] === 255) encoding = 'utf-16be';
  let text;
  try { text = new TextDecoder(encoding, { fatal: true }).decode(bytes); }
  catch { throw new Error('Save this text file as UTF-8 or UTF-16 and try again.'); }
  return { kind: ['csv', 'tsv'].includes(ext) ? 'table' : 'text', count: null,
    read: async () => ({ text: checkedSource(text), ocrPages: [] }), destroy() {} };
}
