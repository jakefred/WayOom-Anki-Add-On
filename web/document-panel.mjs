import { openDocument, parsePageSelection, checkedSource } from './documents.mjs';

export class DocumentPanel {
  constructor(document, onChange, load = openDocument) {
    Object.assign(this, { document, onChange, load });
    this.resource = this.file = this.source = null;
    this.sequence = 0; this.busy = false; this.disabled = false;
    this.get('document-file').addEventListener('change', () => { void this.attach(this.get('document-file').files?.[0]); });
    this.get('document-read').addEventListener('click', () => { void this.read(); });
    this.get('document-remove').addEventListener('click', () => this.clear());
    this.get('document-cancel').addEventListener('click', () => this.clear('Document reading canceled.'));
    for (const [id, event] of [['document-pages', 'input'], ['document-ocr', 'change']]) {
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
      } else {
        await this.read();
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
        onProgress: text => { if (sequence === this.sequence) this.message(text); } });
      if (sequence !== this.sequence) return;
      const text = checkedSource(result.text);
      const filename = [...this.file.name.normalize('NFC').replace(/[\x00-\x1f\x7f-\x9f]/gu, ' ')].slice(0, 255).join('').trim();
      this.source = { name: filename.replace(/[\s\p{Cf}]/gu, '') ? filename : 'Document',
        selection: numbers ? `${this.resource.kind === 'pages' ? 'Pages' : 'Slides'} ${numbers.join(', ')}` : 'Whole document', text };
      this.get('document-preview').value = text;
      const ocr = result.ocrPages?.length ? ` English OCR used on ${result.ocrPages.length} page(s); check the preview for errors.` : '';
      const empty = result.emptyPages?.length ? ` No text on ${this.resource.kind}: ${result.emptyPages.join(', ')}.` : '';
      this.message(`${[...text].length.toLocaleString()} characters ready.${ocr}${empty}`);
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
    this.get('document-ocr-option').hidden = this.resource?.kind !== 'pages';
    this.get('document-file').disabled = this.disabled || this.busy;
    this.get('document-pages').disabled = this.disabled || this.busy;
    this.get('document-ocr').disabled = this.disabled || this.busy;
    this.get('document-read').hidden = !this.resource;
    this.get('document-read').disabled = this.disabled || this.busy;
    this.get('document-remove').hidden = !this.file || this.busy;
    this.get('document-remove').disabled = this.disabled;
    this.get('document-cancel').hidden = !this.busy;
    this.get('document-cancel').disabled = this.disabled;
    this.get('document-preview-area').hidden = !this.source;
    this.get('topic').required = !this.source;
    this.get('topic-label').textContent = this.file ? 'Instructions (optional)' : 'Topic';
    this.get('topic-hint').textContent = this.file
      ? 'Add a focus, or leave blank to cover the main points.'
      : 'What would you like to learn? Up to 2,000 characters.';
  }
}
