import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';

test('bundled generation engine → five real adapters → Python authority → disposable real Anki', { timeout: 30000 }, async t => {
  const python = process.env.WAYOOM_TEST_PYTHON ?? (existsSync('.venv/Scripts/python.exe') ? '.venv/Scripts/python.exe' : existsSync('.venv/bin/python') ? '.venv/bin/python' : 'python');
  const child = spawn(python, ['-u', 'tests/integration/bridge_peer.py', '--generation'], { stdio: ['pipe', 'pipe', 'pipe'] });
  let stderr = ''; child.stderr.on('data', data => { stderr += data; });
  const widget = { addEventListener() {} };
  const context = vm.createContext({ window: {}, document: { querySelector: () => widget },
    pycmd: command => { const message = JSON.parse(command.slice('wayoom:v1:rpc:'.length)); context.window.wayoomBridge.receive({ v: 1, id: message.id, ok: true, result: {} }); },
    TextEncoder, TextDecoder, URL, URLSearchParams, Headers, Request, Response, AbortController, AbortSignal,
    ReadableStream, TransformStream, setTimeout, clearTimeout, crypto: webcrypto, performance, btoa, atob,
  });
  vm.runInContext(readFileSync('web/dist/panel.js', 'utf8'), context);
  const { BridgeClient, GenerationEngine } = context.WayOomPanel;
  const messages = [];
  const client = new BridgeClient(command => {
    const message = JSON.parse(command.slice('wayoom:v1:rpc:'.length));
    messages.push(message); child.stdin.write(`${JSON.stringify(message)}\n`);
  }, { timeout: 8000, onEvent: event => engine.handleEvent(event) });
  const engine = new GenerationEngine(client);
  child.stdin.on('error', () => client.close('provider'));
  const lines = createInterface({ input: child.stdout });
  const received = [];
  lines.on('line', line => { const message = JSON.parse(line); received.push(message); client.receive(message); });
  t.after(async () => {
    client.close(); child.stdin.end(); lines.close();
    if (child.exitCode === null) await new Promise(resolve => { child.once('exit', resolve); setTimeout(() => { child.kill(); resolve(); }, 1500).unref(); });
    assert.equal(stderr, '');
  });
  await client.rpc('ready');
  let total = 0;
  const defaults = JSON.parse(readFileSync('wayoom/default_models.json', 'utf8'));
  for (const provider of Object.keys(defaults)) {
    const setup = await client.rpc('fixture-engine-setup', { provider });
    const result = await engine.run(setup.request);
    assert.equal(result.outcome.count, 100, provider);
    total += 100;
    const stats = await client.rpc('fixture-engine-stats');
    assert.equal(stats.count, total);
    assert.equal(stats.job.settings.modelId, defaults[provider].modelId);
    assert.equal(stats.job.callsUsed, 5);
    const calls = stats.records.slice(setup.records);
    assert.equal(calls.length, 5, provider);
    assert.equal(calls[0].stage, 'naming');
    assert.deepEqual(calls.slice(1).map(call => call.count), [25, 25, 25, 25]);
    assert.equal(defaults[provider].liveStatus, 'untested');
  }
  const single = await client.rpc('fixture-engine-setup', { provider: 'openai', cardCount: 1,
    destination: { mode: 'new', name: 'Supplied name' } });
  assert.equal((await engine.run(single.request)).outcome.count, 1);
  total++;
  const singleStats = await client.rpc('fixture-engine-stats');
  assert.equal(singleStats.records.length - single.records, 1);
  assert.ok(singleStats.decks.includes('Supplied name'));
  // Wire-schema failure with a valid sibling must be salvaged locally, then replaced.
  const repair = await client.rpc('fixture-engine-setup', { provider: 'openai', cardCount: 2, destination: { mode: 'existing' },
    script: [{ rawText: '{malformed' }, { cards: [{ front: 'Valid sibling', back: 'Answer' }, { front: 'Bad sibling', back: 7 }] },
      { cards: [{ front: 'Replacement', back: 'Answer' }] }] });
  await engine.run(repair.request);
  total += 2;
  let stats = await client.rpc('fixture-engine-stats');
  assert.equal(stats.count, total);
  assert.deepEqual(stats.records.slice(repair.records).map(call => call.count), [2, 2, 1]);
  // A failure after the first full batch must leave both collection and deck list unchanged.
  const retry = await client.rpc('fixture-engine-setup', { provider: 'openai', script: [
    { auto: true }, { auto: true }, { httpError: 500 }, { httpError: 500 }, { httpError: 500 },
  ] });
  const before = await client.rpc('fixture-engine-stats');
  await assert.rejects(engine.run(retry.request));
  stats = await client.rpc('fixture-engine-stats');
  assert.equal(stats.count, total);
  assert.deepEqual(stats.decks, before.decks);
  assert.equal(stats.job.callsUsed, 5);
  assert.equal(engine.retainedCount, 25);
  const previousId = stats.job.request.requestId;
  const previousCalls = stats.records.length;
  await client.rpc('fixture-engine-script', { script: [] });
  await engine.run(retry.request);
  total += 100;
  stats = await client.rpc('fixture-engine-stats');
  assert.equal(stats.count, total);
  assert.notEqual(stats.job.request.requestId, previousId);
  assert.equal(stats.job.retryOf, previousId);
  assert.equal(stats.job.callsUsed, 3);
  assert.deepEqual(stats.records.slice(previousCalls).map(call => [call.stage, call.count]), [['cards', 25], ['cards', 25], ['cards', 25]]);
  assert.ok(!JSON.stringify(received).includes('fake-python-only-credential-123'));
  assert.equal(messages.filter(message => message.op === 'commit').length, 8);
});
