import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { panelDocument } from './panel-dom.mjs';

test('DOM ready before Anki QWebChannel: native handshake retry connects once without remounting', async () => {
  const { document } = panelDocument();
  const context = vm.createContext({ window: {}, document,
    TextEncoder, TextDecoder, URL, URLSearchParams, Headers, Request, Response,
    AbortController, AbortSignal, ReadableStream, TransformStream, setTimeout, clearTimeout });
  vm.runInContext(readFileSync(new URL('../../web/dist/panel.js', import.meta.url), 'utf8'), context);
  const panel = context.window.wayoomPanel;
  assert.ok(panel);
  assert.equal(context.window.wayoomBridge.pending.size, 0, 'no failed early RPC');
  context.window.wayoomConnect(); // Native eval before pycmd must also be harmless.
  const commands = [];
  context.pycmd = command => commands.push(JSON.parse(command.slice('wayoom:v1:rpc:'.length)));
  context.window.wayoomConnect();
  context.window.wayoomConnect();
  assert.equal(commands.length, 1, 'only one handshake in flight');
  assert.equal(commands[0].op, 'ready');
  context.window.wayoomBridge.receive({v: 1, id: commands[0].id, ok: true, result: {}});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(context.window.wayoomPanel, panel, 'typed state and event handlers survive');
  assert.equal(context.window.wayoomBridge.pending.size, 0);
});

test('Anki scripts may load before form HTML: mount only on DOMContentLoaded', () => {
  const { document } = panelDocument();
  document.readyState = 'loading';
  let ready, calls = 0;
  document.addEventListener = (event, callback) => { assert.equal(event, 'DOMContentLoaded'); ready = callback; };
  const query = document.querySelector.bind(document);
  document.querySelector = selector => { assert.notEqual(document.readyState, 'loading'); return query(selector); };
  const context = vm.createContext({ window: {}, document,
    pycmd: command => { calls++; const m = JSON.parse(command.slice('wayoom:v1:rpc:'.length));
      context.window.wayoomBridge.receive({v: 1, id: m.id, ok: true, result: {}}); },
    TextEncoder, TextDecoder, URL, URLSearchParams, Headers, Request, Response,
    AbortController, AbortSignal, ReadableStream, TransformStream, setTimeout, clearTimeout });
  vm.runInContext(readFileSync(new URL('../../web/dist/panel.js', import.meta.url), 'utf8'), context);
  assert.equal(context.window.wayoomPanel, undefined);
  assert.equal(calls, 0);
  document.readyState = 'interactive'; ready();
  assert.equal(typeof context.window.wayoomPanel.submit, 'function');
  assert.equal(calls, 1);
});

test('self-contained browser bundle loads SDK and updates the panel without Node globals', () => {
  const { document, elements } = panelDocument();
  const commands = [];
  const context = vm.createContext({
    window: {}, document,
    pycmd: command => {
      if (command.startsWith('wayoom:v1:rpc:')) {
        const message = JSON.parse(command.slice('wayoom:v1:rpc:'.length));
        assert.equal(message.op, 'ready');
        context.window.wayoomBridge.receive({ v: 1, id: message.id, ok: true, result: {} });
      } else commands.push(command);
    },
    TextEncoder, TextDecoder, URL, URLSearchParams, Headers, Request, Response,
    AbortController, AbortSignal, ReadableStream, TransformStream, setTimeout, clearTimeout,
  });
  vm.runInContext(readFileSync(new URL('../../web/dist/panel.js', import.meta.url), 'utf8'), context);
  assert.equal(typeof context.WayOomPanel.generateText, 'function');
  assert.equal(typeof context.WayOomPanel.Output.object, 'function');
  for (const provider of ['openai', 'anthropic', 'google', 'xai', 'mistral']) {
    assert.throws(() => context.WayOomPanel.createProviderModel(provider, 'test'), /Bridge fetch/);
    const model = context.WayOomPanel.createProviderModel(provider, 'test', () => {
      throw new Error('No transport calls during initialization');
    });
    assert.equal(model.modelId, 'test');
  }
  context.window.wayoomSetState({ state: 'adding', message: '<b>Adding</b>', provider: 'openai', canView: false });
  assert.equal(elements['#message'].textContent, '<b>Adding</b>');
  assert.equal(elements['#cancel'].disabled, true);
  assert.equal(elements['#cancel'].hidden, false);
  assert.equal(elements['#view-cards'].hidden, true);
  context.window.wayoomSetState({ state: 'complete', message: 'Done', canView: true });
  assert.equal(elements['#cancel'].hidden, true);
  assert.equal(elements['#view-cards'].hidden, false);
  // Cancellation uses the engine while a job is running. A native fallback
  // remains for Python-owned jobs; insertion's disabled button sends nothing.
  context.window.wayoomSetState({ state: 'generating', message: 'Working' });
  elements['#cancel'].click();
  context.window.wayoomSetState({ state: 'complete', message: 'Done', canView: true });
  elements['#view-cards'].click();
  assert.deepEqual(commands, ['wayoom:v1:cancel', 'wayoom:v1:view-cards']);
  context.window.wayoomSetState({ state: 'failed', message: 'Inspect', recoveryJobs: ['job-1'] });
  const row = elements['#recovery-jobs'].children[0];
  row.children[1].click();
  row.children[2].click();
  assert.deepEqual(commands.slice(-2), ['wayoom:v1:inspect:job-1', 'wayoom:v1:acknowledge:job-1']);
  context.window.wayoomSetState({ state: 'failed', message: 'Inspect', recoveryJobs: ['job-1'] });
  assert.equal(elements['#recovery-jobs'].children[0], row, 'state poll preserves button focus');
  context.window.wayoomSetState({ state: 'failed', message: 'Repair', action: 'repair-note-type' });
  assert.equal(elements['#repair-note-type'].hidden, false);
  assert.equal(elements['#recovery-jobs'].children.length, 0);
  elements['#repair-note-type'].click();
  assert.equal(commands.at(-1), 'wayoom:v1:repair-note-type');
  elements['#settings'].click();
  assert.equal(commands.at(-1), 'wayoom:v1:settings');
});
