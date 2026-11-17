import { openDocument, parsePageSelection, checkedSource } from './documents.mjs';
import { OCR_LANGUAGES, ocrLanguageLabel } from './ocr-languages.mjs';

export class DocumentPanel {
  constructor(document, onChange, load = openDocument) {
    Object.assign(this, { document, onChange, load });
    this.resource = this.file = this.source = null;
    this.sequence = 0; this.busy = false; this.disabled = false;
    const language = this.get('document-language');
    for (const [code, label] of Object.entries(OCR_LANGUAGES)) {
      const option = document.createElement('option'); option.value = code; option.textContent = label;
      language.append(option);
    }
    language.value = 'eng';
    language.addEventListener('change', () => { this.get('document-english').checked = false; });
    this.get('document-file').addEventListener('change', () => { void this.attach(this.get('document-file').files?.[0]); });
    this.get('document-read').addEventListener('click', () => { void this.read(); });
    this.get('document-remove').addEventListener('click', () => this.clear());
    this.get('document-cancel').addEventListener('click', () => this.clear('Document reading canceled.'));
    for (const [id, event] of [['document-pages', 'input'], ['document-ocr', 'change'],
      ['document-language', 'change'], ['document-english', 'change']]) {
      this.get(id).addEventListener(event, () => {
        this.source = null;
        this.get('document-preview').value = '';
        this.message('Selection changed. Read the selected content before generating.');
        this.onChange(); this.render();
      });
    }
    this.render();
  }
  get(id) { return this.document.querySelector(`#${id}`); }
  message(text, error = false) {
    this.get('document-status').textContent = text;
    this.get('document-error').textContent = error ? text : '';
    this.get('document-status').hidden = error;
  }
  get ready() { return !this.busy && (!this.file || !!this.source); }
  get ocrLanguages() {
    const primary = this.get('document-language').value;
    return primary !== 'eng' && this.get('document-english').checked ? [primary, 'eng'] : [primary];
  }
  resetLanguage() {
    this.get('document-language').value = 'eng';
    this.get('document-english').checked = false;
    this.render();
  }
  clear(message = '', notify = true, resetInput = true) {
    ++this.sequence;
    this.controller?.abort();
    Promise.resolve(this.resource?.destroy()).catch(() => {});
    this.controller = this.resource = this.file = this.source = null;
    this.busy = false;
    if (resetInput) this.get('document-file').value = '';
    this.get('document-pages').value = '';
    this.get('document-ocr').checked = false;
    this.get('document-preview').value = '';
    this.message(message);
    if (notify) this.onChange();
    this.render();
  }
  async attach(file) {
    if (this.disabled) return;
    this.clear('', false, false);
    if (!file) { this.onChange(); return; }
    this.file = file; this.busy = true;
    this.controller = new AbortController();
    const sequence = this.sequence;
    this.message('Opening document…'); this.onChange(); this.render();
    try {
      const resource = await this.load(file, { signal: this.controller.signal });
      if (sequence !== this.sequence) { await resource.destroy(); return; }
      this.resource = resource;
      this.busy = false;
      this.get('document-pages').value = '';
      if (resource.count !== null) {
        this.message(`${resource.count} ${resource.kind}. Choose a range, then read the selected content.`);
      } else if (resource.kind !== 'image') {
        await this.read();
      } else {
        this.message('Choose an OCR language, then read the image.');
      }
    } catch (error) {
      if (sequence === this.sequence) this.message(error instanceof Error ? error.message : 'This document could not be opened.', true);
    } finally {
      if (sequence === this.sequence) { this.busy = false; this.render(); this.onChange(); }
    }
  }
  async read() {
    if (this.disabled || this.busy || !this.resource) return;
    const sequence = this.sequence;
    this.busy = true; this.source = null;
    this.get('document-preview').value = '';
    this.message('Reading selected content…'); this.onChange(); this.render();
    try {
      const selection = this.get('document-pages').value;
      const numbers = this.resource.count === null ? null : parsePageSelection(selection, this.resource.count);
      const result = await this.resource.read(selection, { forceOcr: this.get('document-ocr').checked,
        scanImages: this.get('document-ocr').checked,
        ocrLanguages: this.ocrLanguages,
        onProgress: text => { if (sequence === this.sequence) this.message(text); } });
      if (sequence !== this.sequence) return;
      const text = checkedSource(result.text);
      const filename = [...this.file.name.normalize('NFC').replace(/[\x00-\x1f\x7f-\x9f]/gu, ' ')].slice(0, 255).join('').trim();
      this.source = { name: filename.replace(/[\s\p{Cf}]/gu, '') ? filename : 'Document',
        selection: numbers ? `${this.resource.kind === 'pages' ? 'Pages' : 'Slides'} ${numbers.join(', ')}`
          : this.resource.kind === 'image' ? 'Whole image' : 'Whole document', text };
      this.get('document-preview').value = text;
      this.get('document-preview').dir = this.ocrLanguages[0] === 'ara' ? 'rtl' : 'ltr';
      const label = ocrLanguageLabel(this.ocrLanguages);
      const ocr = result.ocrPages?.length ? ` ${label} OCR used on ${result.ocrPages.length} page(s); check the preview for errors.` : '';
      const images = result.ocrImages ? ` ${label} OCR scanned ${result.ocrImages} image(s); check the preview for errors.` : '';
      const skipped = result.skippedImages ? ` ${result.skippedImages} unsupported, linked, or missing image(s) skipped. Export to PDF to scan their rendered text.` : '';
      const blankImages = result.emptyImages ? ` ${result.emptyImages} image(s) had no recognized text.` : '';
      const noImages = this.get('document-ocr').checked && this.resource.kind !== 'pages' && !result.ocrImages && !result.skippedImages
        ? ' No supported embedded images were found.' : '';
      const empty = result.emptyPages?.length ? ` No text on ${this.resource.kind}: ${result.emptyPages.join(', ')}.` : '';
      this.message(`${[...text].length.toLocaleString()} characters ready.${ocr}${images}${skipped}${blankImages}${noImages}${empty}`);
    } catch (error) {
      if (sequence === this.sequence) this.message(error instanceof Error ? error.message : 'The selected content could not be read.', true);
    } finally {
      if (sequence === this.sequence) { this.busy = false; this.render(); this.onChange(); }
    }
  }
  render() {
    const paginated = this.resource?.count != null;
    this.get('document-name').textContent = this.file?.name ?? '';
    this.get('document-selection').hidden = !paginated;
    this.get('document-pages-label').textContent = this.resource?.kind === 'slides' ? 'Slides to include' : 'Pages to include';
    this.get('document-whole-hint').hidden = !this.resource || paginated;
    this.get('document-whole-hint').textContent = this.resource?.kind === 'document'
      ? 'Reads the main body. Export Word to PDF to select pages.' : this.resource?.kind === 'image'
        ? 'Extracts text from this image using the selected OCR language.' : 'Reads the whole file.';
    this.get('document-ocr-option').hidden = !this.resource?.supportsImageOcr;
    this.get('document-language-option').hidden = !this.resource || (!this.resource.supportsImageOcr && this.resource.kind !== 'image');
    this.get('document-english-option').hidden = this.get('document-language-option').hidden || this.get('document-language').value === 'eng';
    this.get('document-local-hint').hidden = !this.resource || (!this.resource.supportsImageOcr && this.resource.kind !== 'image');
    this.get('document-file').disabled = this.disabled || this.busy;
    this.get('document-pages').disabled = this.disabled || this.busy;
    this.get('document-ocr').disabled = this.disabled || this.busy;
    this.get('document-language').disabled = this.disabled || this.busy;
    this.get('document-english').disabled = this.disabled || this.busy;
    this.get('document-read').hidden = !this.resource;
    this.get('document-read').disabled = this.disabled || this.busy;
    this.get('document-remove').hidden = !this.file || this.busy;
    this.get('document-remove').disabled = this.disabled;
    this.get('document-cancel').hidden = !this.busy;
    this.get('document-cancel').disabled = this.disabled;
    this.get('document-preview-area').hidden = !this.source;
    this.get('topic').required = !this.source;
    this.get('topic-label').textContent = this.file ? 'Instructions (optional)' : 'Instructions';
    this.get('topic-hint').textContent = this.file
      ? 'Add a focus, or leave blank to cover the main points.'
      : 'What would you like to learn? Up to 2,000 characters.';
  }
}
