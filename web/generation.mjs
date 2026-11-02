import { requestSchema, basicCardSchema, deckNamingResultSchema, validateGeneration } from './validation.mjs';
import { BridgeError } from './bridge.mjs';
import { classifyFailure, failureMessages, failureDiagnostic } from './failures.mjs';
import { createStructuredCaller } from './generation-provider.mjs';

const terminal = new Set(['authentication', 'credentials', 'model', 'quota', 'rate-limit', 'refusal', 'refused', 'redirect', 'local']);
const copy = card => ({ front: card.front, back: card.back });

export function createStartToken(crypto = globalThis.crypto) {
  if (typeof crypto?.randomUUID === 'function') return crypto.randomUUID();
  if (typeof crypto?.getRandomValues === 'function') {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  }
  throw new BridgeError('local', 'secure-random-unavailable');
}

export class GenerationError extends BridgeError {
  constructor(category, stage, acceptedCount = 0, exhausted = false, diagnostic = '') {
    super(category, diagnostic);
    this.name = 'GenerationError';
    this.message = failureMessages[category] ?? failureMessages.provider;
    this.stage = stage;
    this.acceptedCount = acceptedCount;
    this.exhausted = exhausted;
  }
}

export class GenerationEngine {
  constructor(client, { call = createStructuredCaller(client), onProgress = () => {},
    now = () => globalThis.performance.now(), setTimer = setTimeout, clearTimer = clearTimeout,
    token = createStartToken, timeout = 600000 } = {}) {
    Object.assign(this, { client, call, onProgress, now, setTimer, clearTimer, token, timeout });
    this.active = null;
    this._retained = null;
    this.lastFailure = null;
  }

  get retainedCount() { return this._retained?.batches.flat().length ?? 0; }

  discard({ notify = true } = {}) {
    const requestId = this.active?.requestId ?? this._retained?.requestId;
    this._retained = null;
    if (this.active) this.active.retain = false;
    if (notify && requestId) this.client.rpc('discard-retained', { requestId }).catch(() => {});
  }

  handleEvent(event) {
    if (event.event === 'closed') {
      this.discard({ notify: false });
      this._stop('canceled');
    } else if (event.event === 'owner-ended' && event.owner === this.active?.requestId) {
      if (event.category === 'canceled') this.discard({ notify: false });
      this._stop(event.category);
    }
  }

  _stop(category) {
    if (this.active && !this.active.controller.signal.aborted) {
      this.active.reason = category;
      this.active.controller.abort();
    }
  }

  cancel() {
    const active = this.active;
    if (!active || active.stage === 'adding') return false;
    this.discard({ notify: false });
    this._stop('canceled');
    this.client.rpc('cancel-start', { startToken: active.startToken }).catch(() => {});
    return true;
  }

  _ensure(active) {
    if (this.active !== active || active.controller.signal.aborted) throw new BridgeError(active.reason ?? 'canceled');
    if (active.stage !== 'adding' && this.now() >= active.deadline) {
      this._stop('timeout');
      throw new BridgeError('timeout');
    }
  }

  async _wait(promise, active) {
    try { this._ensure(active); }
    catch (error) { Promise.resolve(promise).catch(() => {}); throw error; }
    let abort;
    const stopped = new Promise((_, reject) => {
      abort = () => reject(new BridgeError(active.reason ?? 'canceled'));
      active.controller.signal.addEventListener('abort', abort, { once: true });
    });
    try {
      const result = await Promise.race([promise, stopped]);
      this._ensure(active);
      return result;
    } finally { active.controller.signal.removeEventListener('abort', abort); }
  }

  _rpc(op, payload, active) { return this._wait(this.client.rpc(op, payload, { signal: active.controller.signal }), active); }

  async _progress(active, stage, batch) {
    this._ensure(active);
    active.stage = stage;
    const acceptedCount = active.batches.flat().length;
    this.onProgress({ stage, batch, acceptedCount, cardCount: active.request.cardCount });
    await this._rpc('progress', { requestId: active.requestId, stage, batch, acceptedCount }, active);
  }

  async _recheck(active) {
    const flat = active.batches.flat();
    const result = await this._rpc('duplicates', { requestId: active.requestId, fronts: flat.map(card => card.front) }, active);
    const rejected = new Set(result.conflicts);
    let index = 0;
    active.batches = active.batches.map(batch => batch.filter(() => !rejected.has(index++)));
    active.cache.batches = active.batches;
    active.avoid = result.avoidFronts;
  }

  async _name(active, job) {
    if (active.request.destination.mode !== 'new' || active.request.destination.name !== null) return null;
    if (typeof job.deckName === 'string') return job.deckName;
    let category = 'malformed';
    let diagnostic = '';
    for (let attempt = 0; attempt < 3; attempt++) {
      await this._progress(active, 'naming', 0);
      try {
        const output = await this._wait(this.call({ job, request: active.request, stage: 'naming', batch: 0,
          feedback: attempt ? 'Return a nonblank title of at most 100 characters, with no control characters.' : null,
          signal: active.controller.signal }), active);
        diagnostic = '';
        const parsed = deckNamingResultSchema.safeParse(output);
        if (!parsed.success) { category = 'malformed'; continue; }
        const retained = await this._rpc('naming-complete', { requestId: active.requestId, deckName: parsed.data.deckName }, active);
        return retained.deckName;
      } catch (error) {
        this._ensure(active);
        category = classifyFailure(error);
        diagnostic = failureDiagnostic(error);
        if (terminal.has(category)) throw new GenerationError(category, 'naming', this.retainedCount, false, diagnostic);
      }
    }
    throw new GenerationError(category, 'naming', this.retainedCount, true, diagnostic);
  }

  async _batch(active, job, batch) {
    const target = Math.min(25, active.request.cardCount - batch * 25);
    let feedback = null;
    let category = 'malformed';
    let diagnostic = '';
    while (active.attempts[batch] < 3 && active.batches[batch].length < target) {
      await this._recheck(active);
      const count = target - active.batches[batch].length;
      if (count === 0) break;
      await this._progress(active, 'cards', batch);
      active.attempts[batch]++;
      let output;
      try {
        output = await this._wait(this.call({ job, request: active.request, stage: 'cards', batch, count,
          avoidFronts: [...active.avoid, ...active.batches.flat().map(card => card.front)], feedback,
          signal: active.controller.signal }), active);
      } catch (error) {
        this._ensure(active);
        category = classifyFailure(error);
        diagnostic = failureDiagnostic(error);
        if (terminal.has(category)) throw new GenerationError(category, 'cards', this.retainedCount, false, diagnostic);
        feedback = 'The previous call failed. Return the requested JSON object with exactly the missing cards.';
        continue;
      }
      diagnostic = '';
      await this._progress(active, 'validating', batch);
      if (!output || typeof output !== 'object' || Array.isArray(output)
          || Object.keys(output).length !== 1 || !Object.hasOwn(output, 'cards') || !Array.isArray(output.cards)) {
        category = 'malformed'; feedback = 'Return only an object with a cards array, without additional fields.'; continue;
      }
      // An excess response is repaired as a whole. Never truncate it to succeed.
      if (output.cards.length > count) {
        category = 'malformed'; feedback = `Too many cards were returned. Return exactly ${count} new cards, no extras.`; continue;
      }
      const candidates = output.cards.map(card => basicCardSchema.safeParse(card)).filter(parsed => parsed.success).map(parsed => parsed.data);
      const accepted = active.batches.flat();
      const checked = await this._rpc('duplicates', { requestId: active.requestId,
        fronts: [...accepted, ...candidates].map(card => card.front) }, active);
      const conflicts = new Set(checked.conflicts);
      // Existing drafts can acquire conflicts while a provider call is running.
      let index = 0;
      active.batches = active.batches.map(items => items.filter(() => !conflicts.has(index++)));
      active.cache.batches = active.batches;
      candidates.forEach((card, candidate) => {
        if (!conflicts.has(accepted.length + candidate)) active.batches[batch].push(copy(card));
      });
      active.avoid = checked.avoidFronts;
      category = 'malformed';
      feedback = 'Some cards were invalid, missing, or duplicate. Generate only the remaining missing cards, avoiding all accepted fronts.';
    }
    if (active.batches[batch].length !== target) throw new GenerationError(category, 'cards', this.retainedCount, true, diagnostic);
  }

  async run(input) {
    if (this.active) throw new GenerationError('refused', 'starting');
    const parsed = requestSchema.safeParse({ ...input, requestId: 'new-job' });
    if (!parsed.success) { this.discard(); throw new GenerationError('validation', 'starting'); }
    const active = { startToken: null, controller: new AbortController(), reason: null,
      stage: 'starting', retain: true, deadline: this.now() + this.timeout, batches: [] };
    this.active = active;
    this.lastFailure = null;
    const deadlineReached = () => { if (this.active === active) this._stop('timeout'); };
    let timer = this.setTimer(deadlineReached, this.timeout);
    try {
      // Start cancellation is token-scoped so a late start response cannot strand a job.
      active.startToken = this.token();
      const job = await this._wait(this.client.rpc('start', { request: parsed.data, startToken: active.startToken }), active);
      active.request = requestSchema.parse(job.request);
      active.requestId = active.request.requestId;
      active.deadline = Math.min(active.deadline, this.now() + job.remainingSeconds * 1000);
      this.clearTimer(timer);
      timer = this.setTimer(deadlineReached, Math.max(0, active.deadline - this.now()));
      const previous = this._retained;
      active.cache = previous && job.retryOf === previous.requestId && job.sessionId === previous.sessionId
        ? previous : { batches: Array.from({ length: Math.ceil(active.request.cardCount / 25) }, () => []), sessionId: job.sessionId };
      active.cache.requestId = active.requestId;
      active.batches = active.cache.batches;
      active.attempts = active.batches.map(() => 0);
      if (active.retain) this._retained = active.cache;
      const deckName = await this._name(active, job);
      await this._recheck(active);
      let batch;
      while ((batch = active.batches.findIndex((cards, index) => cards.length < Math.min(25, active.request.cardCount - index * 25))) !== -1) {
        await this._batch(active, job, batch);
      }
      await this._progress(active, 'validating', active.batches.length - 1);
      const result = validateGeneration(active.request, { deckName, cards: active.batches.flat().map(copy) });
      // The authorized commit independently repeats all validation/conflict checks.
      this._ensure(active);
      active.stage = 'adding';
      this.clearTimer(timer);
      this.onProgress({ stage: 'adding', batch: active.batches.length - 1,
        acceptedCount: active.batches.flat().length, cardCount: active.request.cardCount });
      const completed = await this._wait(this.client.rpc('commit', { requestId: active.requestId, result }), active);
      this._retained = null;
      return completed;
    } catch (error) {
      let category = active.reason ?? classifyFailure(error);
      if (active.stage === 'adding') {
        const status = await this.client.rpc('job-status', { requestId: active.requestId }).catch(() => null);
        if (status?.state === 'complete' && status.commitState === 'committed') {
          this._retained = null;
          return { state: 'committed', outcome: status.outcome };
        }
        if (!status || ['adding', 'generating', 'validating'].includes(status.state)
            || ['pending', 'uncertain'].includes(status.commitState)) category = 'uncertain';
      } else if (active.requestId && category !== 'canceled') {
        await this.client.rpc('fail', { requestId: active.requestId, category, stage: active.stage }).catch(() => {});
      } else if (active.startToken) {
        this.client.rpc('cancel-start', { startToken: active.startToken }).catch(() => {});
      }
      if (category === 'canceled' || category === 'uncertain' || !active.retain) this._retained = null;
      const failure = new GenerationError(category, active.stage, this.retainedCount, error?.exhausted ?? false, failureDiagnostic(error));
      this.lastFailure = { category, stage: failure.stage, acceptedCount: failure.acceptedCount,
        exhausted: failure.exhausted, retryable: category !== 'uncertain' && category !== 'canceled' };
      throw failure;
    } finally {
      this.clearTimer(timer);
      if (this.active === active) this.active = null;
    }
  }
}
