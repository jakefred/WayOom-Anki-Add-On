import test from 'node:test';
import assert from 'node:assert/strict';
import { GenerationPanel } from '../../web/form.mjs';
import { panelDocument } from './panel-dom.mjs';

const ready = { state: 'ready', message: 'Ready', configuredProvider: 'openai', profileReady: true,
  profileEpoch: 1, configurationToken: 'settings-a', canGenerate: true };
const decks = [{ deckId: 1, name: 'Biology::Cells' }, { deckId: 2, name: '<script>inert</script>' }];
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };

async function harness({ response = { decks }, run } = {}) {
  const { document, elements } = panelDocument();
  const calls = [], commands = [], requests = [];
  const engine = { retainedCount: 0, active: null, lastFailure: null, discards: 0,
    discard() { this.discards++; this.retainedCount = 0; },
    cancel() { this.cancels = (this.cancels ?? 0) + 1; },
    async run(request) { requests.push(request); return run ? run(request) : { outcome: { count: request.cardCount, deckName: 'Biology' } }; } };
  const client = { async rpc(op) { calls.push(op); return typeof response === 'function' ? response() : response; } };
  const panel = new GenerationPanel(document, client, engine, command => commands.push(command));
  panel.setState({ ...ready }); await flush();
  elements['#destination'].value = '1'; elements['#topic'].value = 'Cell division';
  return { panel, engine, document, elements, calls, commands, requests };
}

test('deck IDs, full hierarchical/inert names, new-deck toggle and optional trimmed naming', async () => {
  const h = await harness();
  const options = h.elements['#destination'].children;
  assert.deepEqual(options.map(option => [option.value, option.textContent]), [['', 'Choose a deck'], ['new', 'Create new deck'], ['1', decks[0].name], ['2', decks[1].name]]);
  assert.equal(h.elements['#new-deck-fields'].hidden, true);
  h.elements['#destination'].value = 'new'; h.elements['#destination'].dispatch('change');
  assert.equal(h.elements['#new-deck-fields'].hidden, false);
  h.elements['#deck-name'].value = '   ';
  await h.panel.submit();
  assert.deepEqual(h.requests[0].destination, { mode: 'new', name: null });
  h.elements['#deck-name'].value = '  Supplied name  ';
  await h.panel.submit();
  assert.equal(h.requests[1].destination.name, 'Supplied name');
});

test('every invalid count, blank topic, and invalid supplied name has an inline error and starts no engine', async () => {
  const h = await harness();
  for (const count of ['0', '101', '1.5', '', 'NaN']) {
    h.elements['#card-count'].value = count;
    await h.panel.submit();
    assert.equal(h.elements['#card-count'].attributes['aria-invalid'], 'true');
    assert.match(h.elements['#card-count-error'].textContent, /whole number/);
    assert.equal(h.document.activeElement, h.elements['#card-count']);
  }
  h.elements['#card-count'].value = '10'; h.elements['#topic'].value = '  ';
  await h.panel.submit(); assert.match(h.elements['#topic-error'].textContent, /topic/);
  h.elements['#topic'].value = 'Cells'; h.elements['#destination'].value = 'new'; h.elements['#deck-name'].value = 'Invalid::Hierarchy';
  await h.panel.submit(); assert.match(h.elements['#deck-name-error'].textContent, /top-level|without/);
  assert.equal(h.requests.length, 0);
});

test('state pushes preserve typed values, focus, selection ranges, errors and existing option nodes', async () => {
  const h = await harness();
  h.elements['#topic'].value = 'Unfinished typing…'; h.elements['#topic'].selectionStart = 5; h.elements['#topic'].focus();
  h.elements['#card-count'].value = '1.5'; await h.panel.submit(); h.elements['#topic'].focus();
  const nodes = h.elements['#destination'].children;
  for (let i = 0; i < 20; i++) h.panel.setState({ ...ready });
  assert.equal(h.elements['#topic'].value, 'Unfinished typing…');
  assert.equal(h.elements['#card-count'].value, '1.5');
  assert.equal(h.elements['#card-count'].attributes['aria-invalid'], 'true');
  assert.equal(h.document.activeElement, h.elements['#topic']);
  assert.equal(h.elements['#topic'].selectionStart, 5);
  assert.equal(h.elements['#destination'].children, nodes);
  assert.equal(h.calls.length, 1);
});

test('Generate acknowledges synchronously, blocks double clicks, shows stages and locks Cancel only during insertion', async () => {
  const result = deferred(); const h = await harness({ run: () => result.promise });
  const pending = h.panel.submit();
  h.engine.active = { stage: 'cards' };
  assert.equal(h.elements['#generate'].disabled, true);
  assert.equal(h.elements['#cancel'].disabled, false);
  assert.match(h.elements['#message'].textContent, /Starting/);
  await h.panel.submit(); assert.equal(h.requests.length, 1);
  h.panel.setProgress({ stage: 'naming', acceptedCount: 0, cardCount: 10 });
  assert.match(h.elements['#message'].textContent, /Naming/);
  h.elements['#cancel'].click(); assert.equal(h.engine.cancels, 1);
  h.engine.active.stage = 'adding'; h.panel.setProgress({ stage: 'adding', acceptedCount: 10, cardCount: 10 });
  assert.equal(h.elements['#cancel'].disabled, true);
  assert.match(h.elements['#message'].textContent, /Undo/);
  h.elements['#cancel'].click(); assert.equal(h.engine.cancels, 1);
  h.engine.active = null; result.resolve({ outcome: { count: 10, deckName: '<img src=x onerror=alert(1)>' } });
  await pending;
  assert.equal(h.elements['#state-label'].textContent, 'Complete');
  assert.match(h.elements['#message'].textContent, /^Added 10 cards to <img/);
  h.elements['#view-cards'].click(); assert.equal(h.commands.at(-1), 'wayoom:v1:view-cards');
});

test('failures preserve raw form fields, hide raw errors, show retained Retry and discard on any input edit', async () => {
  const h = await harness({ run: () => { h.engine.retainedCount = 5; h.engine.lastFailure = { retryable: true };
    throw Object.assign(new Error('secret-provider-body'), { category: 'network', stage: 'cards' }); } });
  const input = h.elements['#topic'].value;
  await h.panel.submit();
  assert.equal(h.elements['#topic'].value, input);
  assert.equal(h.elements['#retry'].hidden, false);
  assert.match(h.elements['#retained'].textContent, /5 validated drafts/);
  assert.doesNotMatch(h.elements['#message'].textContent, /secret-provider-body/);
  h.elements['#retry'].click(); await flush(); assert.equal(h.requests.length, 2);
  for (const id of ['destination', 'difficulty', 'deck-name', 'card-count', 'topic']) {
    h.engine.retainedCount = 5;
    h.elements[`#${id}`].dispatch(['destination', 'difficulty'].includes(id) ? 'change' : 'input');
    assert.equal(h.engine.retainedCount, 0, id);
  }
});

test('Cancel shows immediate feedback while native state catches up, then releases controls', async () => {
  const waiting = deferred();
  const h = await harness({run: () => waiting.promise});
  const pending = h.panel.submit();
  h.engine.active = {stage:'cards'};
  h.engine.cancel = () => { h.engine.active.reason = 'canceled'; };
  h.elements['#cancel'].click();
  assert.equal(h.elements['#state-label'].textContent, 'Canceling');
  assert.equal(h.elements['#cancel'].disabled, true);
  h.panel.setState({...ready,state:'generating',canGenerate:false});
  h.engine.active = null;
  waiting.reject(Object.assign(new Error(), {category:'canceled',stage:'cards'}));
  await pending;
  assert.equal(h.elements['#state-label'].textContent, 'Canceling');
  h.panel.setState({...ready,state:'canceled'});
  assert.equal(h.elements['#state-label'].textContent, 'Canceled');
  assert.equal(h.elements['#cancel'].hidden, true);
  assert.equal(h.elements['#generate'].disabled, false);
  assert.equal(h.elements['#settings'].disabled, false);
});

test('local startup errors and safe HTTP details are visible without raw exception text', async () => {
  const h = await harness({run: () => { throw new TypeError('private-topic-or-key'); }});
  await h.panel.submit();
  assert.match(h.elements['#message'].textContent, /local application error/);
  assert.match(h.elements['#message'].textContent, /Details: TypeError/);
  assert.doesNotMatch(h.elements['#message'].textContent, /private-topic-or-key/);
  h.engine.run = async () => { throw Object.assign(new Error('private-body'),
    {category:'provider',stage:'cards',diagnostic:'HTTP 400; unsupported_parameter; private-body'}); };
  await h.panel.submit();
  assert.match(h.elements['#message'].textContent, /Generating cards failed/);
  assert.match(h.elements['#message'].textContent, /HTTP 400; unsupported_parameter/);
  assert.doesNotMatch(h.elements['#message'].textContent, /private-body/);
});

test('settings changes invalidate drafts, unchanged state does not, recovery and repair actions gate generation', async () => {
  const h = await harness();
  const previous = h.engine.discards;
  h.panel.setState({ ...ready }); assert.equal(h.engine.discards, previous);
  h.panel.setState({ ...ready, configurationToken: 'new-model-or-key' }); assert.equal(h.engine.discards, previous + 1);
  h.panel.setState({ ...ready, state: 'failed', canGenerate: false, action: 'inspect', recoveryJobs: ['job-1'] });
  assert.equal(h.elements['#generate'].disabled, true);
  const row = h.elements['#recovery-jobs'].children[0]; row.children[1].focus();
  h.panel.setState({ ...h.panel.state });
  assert.equal(h.elements['#recovery-jobs'].children[0], row);
  assert.equal(h.document.activeElement, row.children[1]);
  row.children[1].click(); row.children[2].click();
  assert.deepEqual(h.commands, ['wayoom:v1:inspect:job-1', 'wayoom:v1:acknowledge:job-1']);
  h.panel.setState({ ...ready, state: 'failed', canGenerate: false, action: 'repair-note-type' });
  assert.equal(h.elements['#repair-note-type'].hidden, false);
  assert.equal(h.elements['#retry'].hidden, true);
  h.elements['#repair-note-type'].click(); assert.equal(h.commands.at(-1), 'wayoom:v1:repair-note-type');
});

test('deck refresh preserves selected IDs and typing; removed selections never fall back to a different deck', async () => {
  let response = { decks }; const h = await harness({ response: () => response });
  h.elements['#topic'].focus(); const topic = h.elements['#topic'].value;
  response = { decks: [{ deckId: 1, name: 'Renamed::Deck' }] };
  await h.panel.refreshDecks();
  assert.equal(h.elements['#destination'].value, '1'); assert.equal(h.elements['#topic'].value, topic);
  assert.equal(h.document.activeElement, h.elements['#topic']);
  response = { decks: [] }; await h.panel.refreshDecks();
  assert.equal(h.elements['#destination'].value, '1');
  await h.panel.submit(); assert.equal(h.requests.length, 0);
  assert.match(h.elements['#destination-error'].textContent, /available/);
});

test('refresh failure is actionable, does not flood every state push, and manual refresh recovers', async () => {
  let failed = true; const h = await harness({ response: () => { if (failed) throw new Error('hidden'); return { decks }; } });
  assert.match(h.elements['#deck-status'].textContent, /could not/);
  for (let i = 0; i < 10; i++) h.panel.setState({ ...ready });
  assert.equal(h.calls.length, 1);
  failed = false; h.elements['#refresh-decks'].click(); await flush();
  assert.equal(h.elements['#deck-status'].textContent, '');
  assert.equal(h.calls.length, 2);
});

test('out-of-order deck results and closed-profile results are ignored', async () => {
  const late = deferred(); let response = { decks }; const h = await harness({ response: () => response });
  response = late.promise; const first = h.panel.refreshDecks();
  response = { decks: [{ deckId: 7, name: 'Current' }] }; await h.panel.refreshDecks();
  late.resolve({ decks: [{ deckId: 8, name: 'Stale' }] }); await first;
  assert.deepEqual(h.panel.decks.map(deck => deck.deckId), [7]);
  const closing = deferred(); response = closing.promise; const pending = h.panel.refreshDecks();
  h.panel.close(); closing.resolve({ decks }); await pending;
  assert.deepEqual(h.panel.decks.map(deck => deck.deckId), [7]);
  assert.equal(h.elements['#generate'].disabled, true);
});

test('profile changes reset fields and discard drafts; late generation cannot display another profile outcome', async () => {
  const result = deferred(); const h = await harness({ run: () => result.promise });
  const pending = h.panel.submit();
  h.panel.setState({ ...ready, profileEpoch: 2 });
  assert.equal(h.elements['#topic'].value, '');
  result.resolve({ outcome: { count: 10, deckName: 'Old profile' } }); await pending;
  assert.doesNotMatch(h.elements['#message'].textContent, /Old profile/);
  assert.equal(h.engine.discards, 1);
});

test('uncertain commits block Retry and Generate across input edits until the profile changes', async () => {
  const h = await harness({ run: () => { throw Object.assign(new Error(), { category: 'uncertain', stage: 'adding' }); } });
  await h.panel.submit();
  assert.equal(h.elements['#generate'].disabled, true); assert.equal(h.elements['#retry'].hidden, true);
  h.elements['#topic'].dispatch('input'); assert.equal(h.elements['#generate'].disabled, true);
  assert.equal(h.elements['#state-label'].textContent, 'Failed');
  h.panel.setState({ ...ready, profileEpoch: 2 }); await flush();
  assert.equal(h.elements['#generate'].disabled, false);
});

test('the form shows the newly active provider after canceled work from another provider', async () => {
  const h = await harness();
  h.panel.setState({ ...ready, state: 'canceled', provider: 'anthropic', configuredProvider: 'openai' });
  assert.equal(h.elements['#provider'].textContent, 'Provider: OpenAI');
  const pending = h.panel.submit();
  assert.equal(h.elements['#provider'].textContent, 'Provider: OpenAI');
  await pending;
});

test('commit failures show the backend destination error and repair progress takes precedence over the old failure', async () => {
  const h = await harness({ run: () => { throw Object.assign(new Error(), { category: 'refused', stage: 'adding' }); } });
  await h.panel.submit();
  h.panel.setState({ ...ready, state: 'failed', action: 'retry', message: 'Destination was deleted. Choose another deck.' });
  assert.match(h.elements['#message'].textContent, /Destination was deleted/);
  h.panel.setState({ ...ready, state: 'repairing', canGenerate: false, message: 'Creating replacement…' });
  assert.equal(h.elements['#state-label'].textContent, 'Repairing note type');
  assert.equal(h.elements['#generate'].disabled, true);
});

test('count boundaries and all difficulty choices reach the engine through the same form contract', async () => {
  const h = await harness();
  for (const count of ['1', '100']) {
    for (const level of ['beginner', 'intermediate', 'advanced']) {
      h.elements['#card-count'].value = count; h.elements['#difficulty'].value = level;
      await h.panel.submit();
      assert.equal(h.requests.at(-1).cardCount, Number(count));
      assert.equal(h.requests.at(-1).difficulty, level);
    }
  }
});
