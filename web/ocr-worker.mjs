import { normalizeOcrLanguages } from './ocr-languages.mjs';

// Tesseract.js 7's pinned worker protocol. Owning the Worker directly lets Cancel
// terminate it even while WASM/language data is loading, before initialization.
export class LocalOcr {
  constructor(asset, signal, languages = ['eng'], WorkerClass = Worker) {
    this.languages = normalizeOcrLanguages(languages);
    this.languageKey = this.languages.join('+');
    this.worker = new WorkerClass(asset('ocr/worker.min.js'));
    this.pending = new Map(); this.sequence = 0; this.closed = false;
    this.signal = signal;
    this.abort = () => this.close();
    signal?.addEventListener('abort', this.abort, { once: true });
    this.worker.onerror = () => this.close(new Error('OCR could not load. Reinstall the add-on and try again.'));
    this.worker.onmessage = ({ data }) => {
      const entry = this.pending.get(data.jobId);
      if (!entry || data.status === 'progress') return;
      clearTimeout(entry.timer); this.pending.delete(data.jobId);
      if (data.status === 'resolve') entry.resolve(data.data);
      else entry.reject(new Error('OCR could not read this content. Try a clearer image or scan.'));
    };
    this.asset = asset;
    if (signal?.aborted) this.close();
  }
  send(action, payload) {
    if (this.closed) return Promise.reject(new Error('Document reading canceled.'));
    return new Promise((resolve, reject) => {
      const jobId = `ocr-${++this.sequence}`;
      const timer = setTimeout(() => this.close(new Error('OCR timed out. Try fewer or clearer pages.')), 90000);
      this.pending.set(jobId, { resolve, reject, timer });
      this.worker.postMessage({ workerId: 'wayoom-ocr', jobId, action, payload });
    });
  }
  async initialize() {
    try {
      await this.send('load', { options: { lstmOnly: true, corePath: this.asset('ocr/'), logging: false } });
      await this.send('loadLanguage', { langs: this.languages, options: { langPath: this.asset('ocr/'), cacheMethod: 'none', gzip: true, lstmOnly: true } });
      await this.send('initialize', { langs: this.languages, oem: 1, config: {} });
    } catch (error) { this.close(error); throw error; }
  }
  async recognize(canvas) {
    const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
    if (!blob) throw new Error('This page could not be rendered for OCR.');
    const image = new Uint8Array(await blob.arrayBuffer());
    const result = await this.send('recognize', { image, options: { rotateAuto: true }, output: { text: true } });
    return result.text;
  }
  close(error = new Error('Document reading canceled.')) {
    if (this.closed) return;
    this.closed = true;
    this.signal?.removeEventListener('abort', this.abort);
    this.worker.terminate();
    for (const { reject, timer } of this.pending.values()) { clearTimeout(timer); reject(error); }
    this.pending.clear();
  }
}
