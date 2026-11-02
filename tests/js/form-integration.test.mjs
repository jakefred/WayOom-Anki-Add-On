import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { webcrypto } from 'node:crypto';
import vm from 'node:vm';
import { panelDocument } from './panel-dom.mjs';

const until = async predicate => {
  for (let i = 0; i < 500 && !predicate(); i++) await new Promise(resolve => setTimeout(resolve, 10));
  assert.ok(predicate(), 'expected UI transition');
};

test('production form events → real engine/SDK/bridge → disposable Anki; double-click, naming, failure and retained Retry', { timeout: 30000 }, async t => {
  const python = process.env.WAYOOM_TEST_PYTHON ?? (existsSync('.venv/Scripts/python.exe') ? '.venv/Scripts/python.exe' : existsSync('.venv/bin/python') ? '.venv/bin/python' : 'python');
  const child = spawn(python, ['-u', 'tests/integration/bridge_peer.py', '--generation'], { stdio: ['pipe', 'pipe', 'pipe'] });
  let stderr = ''; child.stderr.on('data', data => { stderr += data; });
  const { document, elements } = panelDocument();
  const messages = [], commands = [], received = [];
  const context = vm.createContext({ window: {}, document,
    pycmd: command => {
      if (!command.startsWith('wayoom:v1:rpc:')) { commands.push(command); return; }
      const message = JSON.parse(command.slice('wayoom:v1:rpc:'.length)); messages.push(message);
      child.stdin.write(`${JSON.stringify(message)}\n`);
    },
    TextEncoder, TextDecoder, URL, URLSearchParams, Headers, Request, Response, AbortController, AbortSignal,
    // Older embedded browsers can expose getRandomValues without randomUUID.
    ReadableStream, TransformStream, setTimeout, clearTimeout,
    crypto: {getRandomValues: bytes => webcrypto.getRandomValues(bytes)}, performance, btoa, atob,
  });
  const lines = createInterface({ input: child.stdout });
  lines.on('line', line => { const message = JSON.parse(line); received.push(message); context.window.wayoomBridge.receive(message); });
  vm.runInContext(readFileSync('web/dist/panel.js', 'utf8'), context);
  const client = context.window.wayoomBridge, panel = context.window.wayoomPanel;
  child.stdin.on('error', () => client.close('provider'));
  t.after(async () => {
    client.close(); child.stdin.end(); lines.close();
    if (child.exitCode === null) await new Promise(resolve => { child.once('exit', resolve); setTimeout(() => { child.kill(); resolve(); }, 1500).unref(); });
    assert.equal(stderr, '');
  });
  const sync = async () => { panel.setState(await client.rpc('fixture-panel-state')); await until(() => !panel.loading); };
  const enter = (id, value, type = 'input') => { elements[`#${id}`].value = value; elements[`#${id}`].dispatch(type); };
  const submit = async () => {
    elements['#generation-form'].dispatch('submit');
    await until(() => !panel.running);
  };
  const existing = await client.rpc('fixture-engine-setup', { provider: 'openai', cardCount: 10, destination: { mode: 'existing' } });
  await sync();
  assert.ok(panel.decks.some(deck => deck.deckId === existing.request.destination.deckId));
  enter('destination', String(existing.request.destination.deckId), 'change'); enter('topic', '  Cell division  ');
  // Both events arrive synchronously, before even the start RPC can reply.
  elements['#generation-form'].dispatch('submit'); elements['#generation-form'].dispatch('submit');
  assert.equal(elements['#generate'].disabled, true);
  await until(() => !panel.running); await sync();
  let stats = await client.rpc('fixture-engine-stats');
  assert.equal(stats.count, 10); assert.equal(stats.job.callsUsed, 1);
  assert.equal(messages.filter(message => message.op === 'start').length, 1);
  assert.equal(elements['#topic'].value, '  Cell division  ');
  assert.equal(elements['#state-label'].textContent, 'Complete');
  elements['#view-cards'].click(); assert.equal(commands.at(-1), 'wayoom:v1:view-cards');

  await client.rpc('fixture-engine-setup', { provider: 'openai' }); await sync();
  enter('destination', 'new', 'change'); enter('deck-name', '  Panel deck  '); enter('card-count', '1');
  await submit(); await sync(); stats = await client.rpc('fixture-engine-stats');
  assert.equal(stats.count, 11); assert.equal(stats.job.callsUsed, 1);
  assert.ok(stats.decks.includes('Panel deck'));
  assert.ok(panel.decks.some(deck => deck.name === 'Panel deck'), 'new deck appears after completion refresh');
  assert.equal(elements['#destination'].value, 'new');

  await client.rpc('fixture-engine-setup', { provider: 'openai', script: [
    { auto: true }, { auto: true }, { httpError: 500 }, { httpError: 500 }, { httpError: 500 },
  ] }); await sync();
  enter('deck-name', '   '); enter('card-count', '30');
  await submit(); await sync(); stats = await client.rpc('fixture-engine-stats');
  assert.equal(stats.count, 11);
  assert.equal(context.window.wayoomGeneration.retainedCount, 25);
  assert.equal(elements['#card-count'].value, '30'); assert.equal(elements['#deck-name'].value, '   ');
  assert.equal(elements['#retry'].hidden, false);
  assert.match(elements['#retained'].textContent, /25 validated drafts/);
  const previous = stats.job.request.requestId, calls = stats.records.length;
  await client.rpc('fixture-engine-script', { script: [] });
  elements['#retry'].click(); await until(() => !panel.running); await sync();
  stats = await client.rpc('fixture-engine-stats');
  assert.equal(stats.count, 41); assert.equal(stats.job.retryOf, previous);
  assert.notEqual(stats.job.request.requestId, previous);
  assert.deepEqual(stats.records.slice(calls).map(call => [call.stage, call.count]), [['cards', 5]]);
  assert.equal(context.window.wayoomGeneration.retainedCount, 0);
  assert.match(elements['#message'].textContent, /Added 30 cards to Biology — Cells/);

  // A held provider reply lets the actual Cancel button race a late response.
  await client.rpc('fixture-engine-setup', { provider: 'openai' }); await sync();
  await client.rpc('fixture-mode', { delay: true });
  enter('destination', String(existing.request.destination.deckId), 'change'); enter('card-count', '1');
  elements['#generation-form'].dispatch('submit');
  await until(() => messages.some(message => message.op === 'fetch' && message.payload.owner === context.window.wayoomGeneration.active?.requestId));
  elements['#cancel'].click(); await until(() => !panel.running);
  await client.rpc('fixture-mode', { delay: false }); await sync();
  assert.equal((await client.rpc('fixture-engine-stats')).count, 41);
  assert.equal(elements['#state-label'].textContent, 'Canceled');
  assert.equal(messages.filter(message => message.op === 'commit').length, 3);
  assert.ok(!JSON.stringify(received).includes('fake-python-only-credential-123'));
});
