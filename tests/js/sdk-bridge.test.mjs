import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';

const loopback = process.env.WAYOOM_LOOPBACK_FAKE === '1';
test(`five bundled SDK adapters: structured output, connection tests, cancellation (${loopback ? 'loopback HTTP' : 'socket-free fake'})`, { timeout: 20000 }, async t => {
  const python = process.env.WAYOOM_TEST_PYTHON ?? (existsSync('.venv/Scripts/python.exe') ? '.venv/Scripts/python.exe' : existsSync('.venv/bin/python') ? '.venv/bin/python' : 'python');
  const child = spawn(python, ['-u', 'tests/integration/bridge_peer.py', ...(loopback ? ['--loopback'] : [])], { stdio: ['pipe', 'pipe', 'pipe'] });
  let stderr = '';
  child.stderr.on('data', data => { stderr += data; });
  const requests = [];
  const responses = [];
  const nativeResults = [];
  const element = { addEventListener() {} };
  const context = vm.createContext({
    window: {}, document: { querySelector: () => element },
    pycmd: command => {
      const message = JSON.parse(command.slice('wayoom:v1:rpc:'.length));
      context.window.wayoomBridge.receive({ v: 1, id: message.id, ok: true, result: {} });
    },
    TextEncoder, TextDecoder, URL, URLSearchParams, Headers, Request, Response,
    AbortController, AbortSignal, ReadableStream, TransformStream, setTimeout, clearTimeout,
    crypto: webcrypto, performance, btoa, atob,
  });
  vm.runInContext(readFileSync('web/dist/panel.js', 'utf8'), context);
  const { BridgeClient, createProviderModel, generateText, Output, jsonSchema, runConnectionTest, classifyFailure } = context.WayOomPanel;
  assert.equal(vm.runInContext('typeof process', context), 'undefined');
  const client = new BridgeClient(command => {
    const message = JSON.parse(command.slice('wayoom:v1:rpc:'.length));
    requests.push(message);
    child.stdin.write(`${JSON.stringify(message)}\n`);
  }, { timeout: 8000, onEvent: event => {
    if (event.event === 'connection-test') runConnectionTest(client, event).catch(() => {});
    if (event.event === 'fixture-native-result') nativeResults.push(event.category);
  } });
  const lines = createInterface({ input: child.stdout });
  lines.on('line', line => {
    const response = JSON.parse(line);
    responses.push(response);
    client.receive(response);
  });
  t.after(async () => {
    client.close();
    child.stdin.end();
    lines.close();
    if (child.exitCode === null) {
      await new Promise(resolve => { child.once('exit', resolve); setTimeout(() => { child.kill(); resolve(); }, 1500).unref(); });
    }
    assert.equal(stderr, '');
  });
  await client.rpc('ready');
  const schema = jsonSchema({ type: 'object', properties: { ok: { type: 'boolean' } }, required: ['ok'], additionalProperties: false });
  const routes = {
    openai: ['https://api.openai.com/v1/responses', 'authorization'],
    anthropic: ['https://api.anthropic.com/v1/messages', 'x-api-key'],
    google: ['https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent', 'x-goog-api-key'],
    xai: ['https://api.x.ai/v1/responses', 'authorization'],
    mistral: ['https://api.mistral.ai/v1/chat/completions', 'authorization'],
  };
  for (const [provider, [url, header]] of Object.entries(routes)) {
    const job = await client.rpc('fixture-start', { provider });
    const model = createProviderModel(provider, job.settings.modelId,
      client.fetch({ owner: job.request.requestId, kind: 'job', stage: 'cards', batch: 0 }));
    const result = await generateText({ model, maxRetries: 0, maxOutputTokens: 128, prompt: 'Return {"ok":true}', output: Output.object({ schema }) });
    assert.equal(JSON.stringify(result.output), '{"ok":true}', provider);
    const fetches = requests.filter(request => request.op === 'fetch' && request.payload.owner === job.request.requestId);
    assert.equal(fetches.length, 1, provider);
    assert.equal(fetches[0].payload.request.url, url);
    assert.ok(fetches[0].payload.request.headers[header].includes('wayoom-bridge-placeholder'));
  }
  for (const provider of Object.keys(routes)) {
    const before = nativeResults.length;
    await client.rpc('fixture-test', { provider });
    const deadline = Date.now() + 3000;
    while (nativeResults.length === before && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(nativeResults.at(before), 'success', provider);
  }
  for (const provider of Object.keys(routes)) {
    const job = await client.rpc('fixture-start', { provider });
    await client.rpc('fixture-mode', { delay: true });
    const before = (await client.rpc('fixture-stats')).records.length;
    const controller = new AbortController();
    const model = createProviderModel(provider, job.settings.modelId,
      client.fetch({ owner: job.request.requestId, kind: 'job', stage: 'cards', batch: 0 }));
    const pending = generateText({ model, maxRetries: 0, maxOutputTokens: 128, prompt: 'Return {"ok":true}',
      output: Output.object({ schema }), abortSignal: controller.signal }).catch(error => classifyFailure(error));
    const deadline = Date.now() + 3000;
    let count = before;
    while (count === before && Date.now() < deadline) {
      count = (await client.rpc('fixture-stats')).records.length;
      if (count === before) await new Promise(resolve => setTimeout(resolve, 10));
    }
    assert.equal(count, before + 1, provider);
    controller.abort();
    assert.equal(await pending, 'canceled', provider);
    await client.rpc('fixture-mode', { delay: false });
    const fetches = requests.filter(request => request.op === 'fetch' && request.payload.owner === job.request.requestId);
    assert.equal(fetches.length, 1, `${provider} cancellation must not retry`);
  }
  for (const [error, expected] of [[401, 'authentication'], [404, 'model'], [429, 'rate-limit'], [null, 'success']]) {
    const before = nativeResults.length;
    await client.rpc('fixture-mode', { error });
    await client.rpc('fixture-test', { provider: 'openai' });
    const deadline = Date.now() + 3000;
    while (nativeResults.length === before && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(nativeResults.at(before), expected);
  }
  const beforeNetwork = nativeResults.length;
  await client.rpc('fixture-mode', { network: true });
  await client.rpc('fixture-test', { provider: 'openai' });
  const deadline = Date.now() + 3000;
  while (nativeResults.length === beforeNetwork && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(nativeResults.at(beforeNetwork), 'network');
  const stats = await client.rpc('fixture-stats');
  assert.equal(stats.records.length, 19);
  for (const record of stats.records) assert.equal(record.authNames.length, 1);
  assert.equal(stats.settings.providers.openai.connectionStatus, 'verified', 'network failure preserves last successful verification');
  assert.ok(!JSON.stringify(requests).includes('fake-python-only-credential-123'));
  assert.ok(!JSON.stringify(responses).includes('fake-python-only-credential-123'));
});
