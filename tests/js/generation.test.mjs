import test from 'node:test';
import assert from 'node:assert/strict';
import { GenerationEngine, createStartToken } from '../../web/generation.mjs';
import { APICallError } from 'ai';
import { BridgeError } from '../../web/bridge.mjs';
import { generationPrompt, cardsWireSchema } from '../../web/generation-provider.mjs';

const form = patch => ({ requestId: 'ignored', destination: { mode: 'existing', deckId: 1 }, cardCount: 1,
  topic: 'Biology', difficulty: 'intermediate', providerId: 'openai', ...patch });
const card = front => ({ front, back: 'A concise answer' });
const folded = front => front.normalize('NFKC').toLowerCase().replace(/ß/g, 'ss').replace(/\s+/gu, ' ').trim();
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const tickUntil = async predicate => { for (let i = 0; i < 100 && !predicate(); i++) await new Promise(done => setImmediate(done)); assert.ok(predicate()); };

function harness({ script, existing = [], options = {}, beforeCommit } = {}) {
  const h = { calls: [], messages: [], commits: [], existing, sequence: 0, token: 0, nextFront: 0, state: null, retainedName: null, canRetain: true };
  const client = { async rpc(op, payload) {
    h.messages.push({ op, payload: structuredClone(payload) });
    if (op === 'start') {
      if (['generating', 'validating', 'adding'].includes(h.state)) throw new BridgeError('refused');
      const canonical = JSON.stringify({ ...payload.request, requestId: null });
      const reusable = h.state === 'failed' && h.canRetain && canonical === h.canonical;
      const retryOf = reusable ? h.job.request.requestId : null;
      if (!reusable) h.retainedName = null;
      h.canonical = canonical; h.canRetain = true; h.state = 'generating';
      h.startToken = payload.startToken;
      h.job = { request: { ...payload.request, requestId: `job-${++h.sequence}` }, settings: { modelId: 'fixture', credentialRef: 'opaque' },
        state: h.state, deckName: h.retainedName, sessionId: 'profile-session', remainingSeconds: 600, retryOf };
      return structuredClone(h.job);
    }
    if (op === 'cancel-start') { if (payload.startToken === h.startToken) { h.state = 'canceled'; h.retainedName = null; } return {}; }
    if (op === 'discard-retained') { h.canRetain = false; return {}; }
    if (op === 'fail') { h.state = 'failed'; return {}; }
    if (op === 'progress') { h.state = payload.stage === 'validating' ? 'validating' : 'generating'; return {}; }
    if (op === 'naming-complete') { h.retainedName = payload.deckName; return { deckName: payload.deckName }; }
    if (op === 'duplicates') {
      const seen = new Set(h.existing.map(folded)); const conflicts = [];
      payload.fronts.forEach((front, index) => { const key = folded(front); if (seen.has(key)) conflicts.push(index); seen.add(key); });
      return { conflicts, avoidFronts: h.existing.slice(0, 200) };
    }
    if (op === 'job-status') return { state: h.state, commitState: h.commitState ?? null, outcome: h.outcome };
    if (op === 'commit') {
      assert.deepEqual(Object.keys(payload).sort(), ['requestId', 'result']);
      if (beforeCommit) await beforeCommit(h, payload);
      h.commits.push(structuredClone(payload)); h.state = 'complete'; h.commitState = 'committed';
      h.outcome = { requestId: payload.requestId, count: payload.result.cards.length, deckName: h.retainedName ?? 'Existing' };
      return { state: 'committed', outcome: h.outcome };
    }
    throw new Error(`Unexpected RPC ${op}`);
  } };
  h.client = client;
  h.engine = new GenerationEngine(client, { token: () => `start-${++h.token}`,
    call: async args => {
      h.calls.push({ ...args, avoidFronts: args.avoidFronts && [...args.avoidFronts] });
      if (script) return script(args, h);
      return args.stage === 'naming' ? { deckName: 'Biology::Cells' }
        : { cards: Array.from({ length: args.count }, () => card(`Question ${++h.nextFront}`)) };
    }, ...options });
  return h;
}

for (const count of [1, 100]) test(`assemble exactly ${count} cards before one commit with sequential batches`, async () => {
  const h = harness();
  const result = await h.engine.run(form({ cardCount: count }));
  assert.equal(result.outcome.count, count);
  assert.deepEqual(h.calls.map(call => call.count), count === 1 ? [1] : [25, 25, 25, 25]);
  assert.equal(h.commits.length, 1);
  assert.equal(h.commits[0].result.cards.length, count);
  assert.equal(h.engine.retainedCount, 0);
});

test('AI naming is a dedicated call before cards, repairs hierarchy, and supplied names skip it', async () => {
  const h = harness();
  await h.engine.run(form({ destination: { mode: 'new', name: null } }));
  assert.deepEqual(h.calls.map(call => call.stage), ['naming', 'cards']);
  assert.equal(h.commits[0].result.deckName, 'Biology — Cells');
  const supplied = harness();
  await supplied.engine.run(form({ destination: { mode: 'new', name: '  User title  ' } }));
  assert.deepEqual(supplied.calls.map(call => call.stage), ['cards']);
  assert.equal(supplied.commits[0].result.deckName, null);
});

test('invalid names exhaust three naming attempts without generating cards or committing', async () => {
  const h = harness({ script: () => ({ deckName: ' ' }) });
  await assert.rejects(h.engine.run(form({ destination: { mode: 'new', name: null } })), error => error.stage === 'naming' && error.exhausted);
  assert.equal(h.calls.length, 3);
  assert.equal(h.commits.length, 0);
});

test('valid siblings survive invalid, missing, and full-fold duplicate items; replacements request only the deficit', async () => {
  const h = harness({ script: (args, h) => h.calls.length === 1
    ? { cards: [card('Straße?'), card('STRASSE?'), { front: 'Invalid', back: '' }] }
    : { cards: Array.from({ length: args.count }, (_, index) => card(`Replacement ${index}`)) } });
  await h.engine.run(form({ cardCount: 3 }));
  assert.deepEqual(h.calls.map(call => call.count), [3, 2]);
  assert.deepEqual(h.calls[1].avoidFronts, ['Straße?']);
  assert.equal(h.commits[0].result.cards.length, 3);
});

test('short response retains accepted cards and asks for only missing cards', async () => {
  const h = harness({ script: (args, h) => h.calls.length === 1 ? { cards: [card('First')] }
    : { cards: Array.from({ length: args.count }, (_, index) => card(`Rest ${index}`)) } });
  await h.engine.run(form({ cardCount: 4 }));
  assert.deepEqual(h.calls.map(call => call.count), [4, 3]);
});

test('excess output is regenerated without truncation or acceptance of its first cards', async () => {
  const h = harness({ script: (args, h) => h.calls.length === 1 ? { cards: [card('Excess 1'), card('Excess 2'), card('Excess 3')] }
    : { cards: [card('Correct 1'), card('Correct 2')] } });
  await h.engine.run(form({ cardCount: 2 }));
  assert.deepEqual(h.calls.map(call => call.count), [2, 2]);
  assert.deepEqual(h.commits[0].result.cards.map(card => card.front), ['Correct 1', 'Correct 2']);
  assert.ok(h.calls[1].feedback.includes('Too many'));
});

for (const [label, response] of [
  ['malformed', null], ['empty', { cards: [] }], ['oversized field', { cards: [card('x'.repeat(501))] }],
  ['extra fields', { cards: [card('Q')], deckName: 'Injected' }],
]) test(`${label} output exhausts exactly three original-batch attempts and writes nothing`, async () => {
  const h = harness({ script: () => response });
  await assert.rejects(h.engine.run(form()), error => error.category === 'malformed' && error.exhausted);
  assert.equal(h.calls.length, 3);
  assert.deepEqual(h.calls.map(call => call.batch), [0, 0, 0]);
  assert.equal(h.commits.length, 0);
});

test('100-card failure retains drafts and title; unchanged retry rechecks conflicts and fills original batch deficits', async () => {
  let fail = true;
  const h = harness({ script: (args, h) => {
    if (args.stage === 'naming') return { deckName: 'Cells' };
    if (args.batch === 1 && fail) throw new BridgeError('network');
    return { cards: Array.from({ length: args.count }, () => card(`Q${++h.nextFront}`)) };
  } });
  const request = form({ cardCount: 100, destination: { mode: 'new', name: null } });
  await assert.rejects(h.engine.run(request));
  assert.equal(h.calls.length, 5); // naming, batch 0, three failed batch 1 calls
  assert.equal(h.engine.retainedCount, 25);
  assert.equal(h.commits.length, 0);
  const previousId = h.job.request.requestId;
  h.existing.push('Q1'); fail = false;
  await h.engine.run(request);
  assert.notEqual(h.job.request.requestId, previousId);
  assert.equal(h.calls.filter(call => call.stage === 'naming').length, 1);
  assert.deepEqual(h.calls.slice(5).map(call => [call.batch, call.count]), [[0, 1], [1, 25], [2, 25], [3, 25]]);
  assert.equal(h.commits[0].result.cards.length, 100);
  assert.ok(!h.commits[0].result.cards.some(card => card.front === 'Q1'));
});

test('deck checks use full authority beyond the 200-front prompt hint', async () => {
  const existing = [...Array.from({ length: 200 }, (_, index) => `Existing ${index}`), 'Straße?'];
  const h = harness({ existing, script: (args, h) => ({ cards: [card(h.calls.length === 1 ? 'STRASSE?' : 'New question')] }) });
  await h.engine.run(form());
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[0].avoidFronts.length, 200);
  assert.ok(!h.calls[0].avoidFronts.includes('Straße?'));
  assert.equal(h.commits[0].result.cards[0].front, 'New question');
});

test('avoid list accumulates across batches and replacements retain the original batch budget', async () => {
  const h = harness({ script: (args, h) => {
    const cards = Array.from({ length: args.count }, () => card(`Q${++h.nextFront}`));
    if (h.calls.length === 2) cards[0] = card('q1');
    return { cards };
  } });
  await h.engine.run(form({ cardCount: 100 }));
  assert.deepEqual(h.calls.map(call => [call.batch, call.count]), [[0, 25], [1, 25], [1, 1], [2, 25], [3, 25]]);
  assert.equal(h.calls[1].avoidFronts.length, 25);
  assert.equal(h.calls[2].avoidFronts.length, 49);
});

test('cancel rejects a provider that ignores AbortSignal; a late response cannot commit', async () => {
  const waiting = deferred(); const h = harness({ script: () => waiting.promise });
  const pending = h.engine.run(form());
  const rejected = assert.rejects(pending, error => error.category === 'canceled');
  await tickUntil(() => h.calls.length === 1);
  assert.equal(h.engine.cancel(), true);
  await rejected;
  waiting.resolve({ cards: [card('Late')] });
  await new Promise(done => setImmediate(done));
  assert.equal(h.commits.length, 0);
  assert.equal(h.engine.retainedCount, 0);
  assert.equal(h.state, 'canceled');
});

test('cancel remains responsive while failure reporting is awaiting a native reply', async () => {
  const reporting = deferred();
  const h = harness({script: () => { throw new BridgeError('authentication'); }});
  const rpc = h.client.rpc;
  let waiting = false, settled = false;
  h.client.rpc = (op, payload) => {
    const result = rpc(op, payload);
    if (op === 'fail') { waiting = true; return reporting.promise; }
    return result;
  };
  const result = h.engine.run(form()).catch(error => { settled = true; return error; });
  await tickUntil(() => waiting);
  try {
    assert.equal(h.engine.cancel(), true);
    await new Promise(done => setImmediate(done));
    assert.equal(settled, true, 'Cancel must not wait for the failure-report RPC');
    assert.equal(h.engine.active, null);
    assert.equal((await result).category, 'canceled');
    assert.equal(h.engine.retainedCount, 0);
    assert.equal(h.canRetain, false, 'Cancel also discards any native retained deck title');
  } finally {
    reporting.resolve({});
    await result;
  }
  assert.equal(h.commits.length, 0);
});

for (const phase of ['naming', 'duplicate-check', 'second-batch']) {
  test(`cancel during ${phase} discards drafts and ignores late results`, async () => {
    const waiting = deferred();
    let entered = false;
    const h = harness({script: args => {
      if (phase === 'naming' && args.stage === 'naming' || phase === 'second-batch' && args.batch === 1) {
        entered = true;
        return waiting.promise;
      }
      return {cards:Array.from({length:args.count}, (_, i) => card(`Question ${i}`))};
    }});
    const rpc = h.client.rpc;
    if (phase === 'duplicate-check') h.client.rpc = (op, payload) => {
      if (op === 'duplicates' && payload.fronts.length) { entered = true; return waiting.promise; }
      return rpc(op, payload);
    };
    const pending = h.engine.run(form({cardCount:30,
      ...(phase === 'naming' ? {destination:{mode:'new',name:null}} : {})}));
    const rejected = assert.rejects(pending, error => error.category === 'canceled');
    await tickUntil(() => entered);
    if (phase === 'second-batch') assert.equal(h.engine.retainedCount, 25);
    h.engine.cancel();
    await rejected;
    waiting.resolve(phase === 'naming' ? {deckName:'Late title'}
      : phase === 'duplicate-check' ? {conflicts:[],avoidFronts:[]}
        : {cards:Array.from({length:5}, (_, i) => card(`Late ${i}`))});
    await new Promise(done => setImmediate(done));
    assert.equal(h.engine.active, null);
    assert.equal(h.engine.retainedCount, 0);
    assert.equal(h.commits.length, 0);
    assert.equal(h.canRetain, false);
  });
}

test('overall timeout preserves earlier drafts and stops an unresponsive later batch', async () => {
  let now = 0; let timer;
  const waiting = deferred();
  const h = harness({ options: { now: () => now, setTimer: callback => { timer = callback; return 1; }, clearTimer() {} },
    script: (args, h) => h.calls.length === 1 ? { cards: Array.from({ length: 25 }, (_, index) => card(`Q${index}`)) } : waiting.promise });
  const pending = h.engine.run(form({ cardCount: 100 }));
  const rejected = assert.rejects(pending, error => error.category === 'timeout');
  await tickUntil(() => h.calls.length === 2);
  now = 600001; timer();
  await rejected;
  assert.equal(h.engine.retainedCount, 25);
  assert.equal(h.commits.length, 0);
  assert.equal(h.calls.length, 2);
  waiting.resolve({ cards: [] });
});

test('profile closure discards retained data and rejects pending generation', async () => {
  const waiting = deferred(); const h = harness({ script: () => waiting.promise });
  const pending = h.engine.run(form()); const rejected = assert.rejects(pending, error => error.category === 'canceled');
  await tickUntil(() => h.calls.length === 1);
  h.engine.handleEvent({ event: 'closed' });
  await rejected; waiting.resolve({ cards: [card('Late')] });
  assert.equal(h.engine.retainedCount, 0); assert.equal(h.commits.length, 0);
});

test('missing randomUUID falls back to secure bytes; missing crypto fails locally without requests', async () => {
  const token = createStartToken({getRandomValues: bytes => { bytes.fill(171); return bytes; }});
  assert.equal(token, 'ab'.repeat(16));
  const h = harness({ options: { token: () => createStartToken({}) } });
  await assert.rejects(h.engine.run(form()), error => error.category === 'local'
    && error.stage === 'starting' && error.diagnostic === 'secure-random-unavailable');
  assert.equal(h.messages.length, 0);
  assert.equal(h.calls.length, 0);
  assert.equal(h.engine.active, null);
});

test('default timers use the global receiver through a complete 30-card generation', async t => {
  const schedule = globalThis.setTimeout, clear = globalThis.clearTimeout;
  let scheduled = 0, cleared = 0;
  t.mock.method(globalThis, 'setTimeout', function (...args) {
    assert.equal(this, globalThis, 'browser setTimeout requires its global receiver');
    scheduled++;
    return schedule(...args);
  });
  t.mock.method(globalThis, 'clearTimeout', function (...args) {
    assert.equal(this, globalThis, 'browser clearTimeout requires its global receiver');
    cleared++;
    return clear(...args);
  });
  const h = harness();
  const result = await h.engine.run(form({cardCount:30}));
  assert.equal(result.outcome.count, 30);
  assert.deepEqual(h.calls.map(call => call.count), [25, 5]);
  assert.ok(scheduled >= 2 && cleared >= 2);
  assert.equal(h.engine.active, null);
});

test('timer setup failure is labeled starting and does not strand an active job', async () => {
  const h = harness({options:{setTimer: () => { throw new TypeError('fixture timer failure'); }}});
  await assert.rejects(h.engine.run(form()), error => error.category === 'local' && error.stage === 'starting');
  assert.equal(h.engine.active, null);
  assert.equal(h.messages.length, 0);
});

test('HTTP diagnostic survives retries and engine wrapping without raw provider content', async () => {
  const h = harness({script: () => { throw new APICallError({message: 'private topic and key',
    url: 'https://example.invalid', requestBodyValues: {}, statusCode: 400,
    data: {error: {code: 'unsupported_parameter', param: 'max_output_tokens', message: 'private'}}}); }});
  await assert.rejects(h.engine.run(form()), error => error.category === 'provider'
    && error.diagnostic.includes('HTTP 400') && error.diagnostic.includes('max_output_tokens')
    && !JSON.stringify(error).includes('private'));
  assert.equal(h.calls.length, 3);
});

test('authentication failure is terminal and never silently retried', async () => {
  const h = harness({ script: () => { throw new BridgeError('authentication'); } });
  await assert.rejects(h.engine.run(form()), error => error.category === 'authentication');
  assert.equal(h.calls.length, 1); assert.equal(h.commits.length, 0);
});

test('an invalid existing destination is rejected during prefetch before any provider call', async () => {
  const h = harness(); const rpc = h.client.rpc;
  h.client.rpc = (op, payload) => op === 'duplicates' ? Promise.reject(new BridgeError('refused')) : rpc(op, payload);
  await assert.rejects(h.engine.run(form()), error => error.category === 'refused');
  assert.equal(h.calls.length, 0); assert.equal(h.commits.length, 0);
});

test('a definitive commit failure retains a full result and retry needs no provider call', async () => {
  let fail = true;
  const h = harness({ beforeCommit: h => { if (fail) { h.state = 'failed'; h.commitState = 'not_committed'; throw new BridgeError('refused'); } } });
  await assert.rejects(h.engine.run(form()));
  assert.equal(h.engine.retainedCount, 1);
  fail = false;
  await h.engine.run(form());
  assert.equal(h.calls.length, 1); assert.equal(h.commits.length, 1);
});

test('uncertain commit has no automatic retry and no retained result', async () => {
  const h = harness({ beforeCommit: h => { h.state = 'adding'; h.commitState = 'pending'; throw new BridgeError('timeout'); } });
  await assert.rejects(h.engine.run(form()), error => error.category === 'uncertain');
  assert.equal(h.engine.lastFailure.retryable, false); assert.equal(h.engine.retainedCount, 0);
  assert.equal(h.calls.length, 1);
});

test('changed inputs or explicit discard prevent retained-title and card reuse even after restoring inputs', async () => {
  for (const explicit of [false, true]) {
    let fail = true;
    const h = harness({ script: (args, h) => {
      if (args.stage === 'naming') return { deckName: 'Retained name' };
      if (fail) return { cards: [card('Partial')] };
      return { cards: Array.from({ length: args.count }, () => card(`New ${++h.nextFront}`)) };
    } });
    const request = form({ cardCount: 2, destination: { mode: 'new', name: null } });
    await assert.rejects(h.engine.run(request));
    assert.equal(h.engine.retainedCount, 1);
    if (explicit) h.engine.discard();
    fail = false;
    await h.engine.run(explicit ? request : { ...request, topic: 'Changed' });
    assert.equal(h.calls.filter(call => call.stage === 'naming').length, 2);
    assert.ok(!h.commits[0].result.cards.some(card => card.front === 'Partial'));
  }
});

test('a new conflict in an earlier batch uses only that original batch’s remaining attempts', async () => {
  const h = harness({ script: (args, h) => {
    if (h.calls.length === 2) h.existing.push('Q1');
    return { cards: Array.from({ length: args.count }, () => card(`Q${++h.nextFront}`)) };
  } });
  await h.engine.run(form({ cardCount: 100 }));
  assert.deepEqual(h.calls.map(call => [call.batch, call.count]), [[0, 25], [1, 25], [0, 1], [2, 25], [3, 25]]);
});

test('canceling while start is pending cannot strand a job or launch a late provider call', async () => {
  const h = harness(); const started = deferred();
  const rpc = h.client.rpc;
  h.client.rpc = async (op, payload) => {
    const result = await rpc(op, payload);
    if (op === 'start') { await started.promise; return result; }
    return result;
  };
  const pending = h.engine.run(form()); const rejected = assert.rejects(pending, error => error.category === 'canceled');
  await tickUntil(() => h.state === 'generating');
  h.engine.cancel(); await rejected;
  started.resolve(); await new Promise(done => setImmediate(done));
  assert.equal(h.state, 'canceled'); assert.equal(h.calls.length, 0); assert.equal(h.commits.length, 0);
});

test('insertion finishes despite the generation deadline and cannot be canceled through the engine', async () => {
  let now = 0; const inserting = deferred();
  const h = harness({ options: { now: () => now }, beforeCommit: h => { h.state = 'adding'; return inserting.promise; } });
  const pending = h.engine.run(form());
  await tickUntil(() => h.state === 'adding');
  assert.equal(h.engine.cancel(), false);
  now = 600001; inserting.resolve();
  assert.equal((await pending).outcome.count, 1);
});

test('a lost commit response recovers a durable success without reinsertion', async () => {
  const h = harness({ beforeCommit: (h, payload) => {
    h.state = 'complete'; h.commitState = 'committed'; h.outcome = { requestId: payload.requestId, count: 1 };
    throw new BridgeError('timeout');
  } });
  assert.equal((await h.engine.run(form())).outcome.count, 1);
  assert.equal(h.messages.filter(message => message.op === 'commit').length, 1);
  assert.equal(h.engine.retainedCount, 0);
});

test('invalid inputs contact neither Python start nor provider', async () => {
  const h = harness();
  for (const patch of [{ cardCount: 0 }, { cardCount: 101 }, { cardCount: 1.5 }, { topic: ' ' }]) {
    await assert.rejects(h.engine.run(form(patch)), error => error.category === 'validation');
  }
  assert.equal(h.messages.length, 0); assert.equal(h.calls.length, 0);
});

test('difficulty, language, topic, exact count, and inert avoid data are in prompts; wire schema stays simple', () => {
  for (const [level, learning] of [['beginner', 'facts and definitions'], ['intermediate', 'Relationships and application'], ['advanced', 'multi-step reasoning']]) {
    const prompt = generationPrompt({ request: form({ difficulty: level, topic: 'Biología' }), stage: 'cards', count: 7, avoidFronts: ['Existing front'] });
    assert.ok(prompt.prompt.includes(learning)); assert.ok(prompt.prompt.includes('exactly 7'));
    assert.ok(prompt.prompt.includes('Biología')); assert.ok(prompt.prompt.includes('Existing front'));
    assert.ok(prompt.system.includes('Follow the language of the topic unless'));
    assert.ok(prompt.system.includes('single learning objective'));
  }
  assert.ok(!JSON.stringify(cardsWireSchema).includes('minItems'));
  assert.ok(!JSON.stringify(cardsWireSchema).includes('maxLength'));
});
