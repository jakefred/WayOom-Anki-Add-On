import { unzipSync, strFromU8 } from 'fflate';

export const MAX_SOURCE_CHARACTERS = 60000;
export const MAX_FILE_BYTES = 30 * 1024 * 1024;
export const MAX_SELECTED_PAGES = 50;
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
  // Never decompress images, macros, embedded documents, or unrelated ZIP entries.
  return unzipSync(bytes, { filter: file => {
    if (++entries > 10000) throw new Error('The document contains too many archive entries.');
    if (!/^(word\/document\.xml|ppt\/presentation\.xml|ppt\/_rels\/presentation\.xml\.rels|ppt\/slides\/slide\d+\.xml)$/.test(file.name)) return false;
    total += file.originalSize;
    if (file.originalSize > 8 * 1024 * 1024 || total > 24 * 1024 * 1024) throw new Error('The document text is too large to open.');
    return true;
  } });
}

export async function openDocument(file, { signal } = {}) {
  const ext = file.name.split('.').pop().toLowerCase();
  if (!['pdf', 'docx', 'pptx', 'txt', 'md', 'csv', 'tsv'].includes(ext)) {
    throw new Error('Choose PDF, DOCX, PPTX, TXT, Markdown, CSV, or TSV. Export older DOC/PPT files to DOCX/PPTX or PDF first.');
  }
  if (!file.size || file.size > MAX_FILE_BYTES) throw new Error('Choose a nonempty document of 30 MB or less.');
  const bytes = new Uint8Array(await file.arrayBuffer());
  checkCanceled(signal);
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
    if (ext === 'docx') {
      const text = paragraphs(read('word/document.xml'));
      return { kind: 'document', count: null, read: async () => ({ text: checkedSource(text), ocrPages: [] }), destroy() {} };
    }
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
    return { kind: 'slides', count: slides.length, async read(selection, _options = {}) {
      const pages = parsePageSelection(selection, slides.length);
      const content = pages.map(n => ({ n, text: cleanDocumentText(paragraphs(read(slides[n - 1]))) }));
      if (!content.some(page => page.text.replace(/[\s\p{Cf}]/gu, ''))) throw new Error('The selected slides have no readable text. For image-only slides, export to PDF and use OCR.');
      return { text: checkedSource(content.map(page => `[Slide ${page.n}]\n${page.text}`).join('\n\n')), ocrPages: [],
        emptyPages: content.filter(page => !page.text).map(page => page.n) };
    }, destroy() {} };
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
