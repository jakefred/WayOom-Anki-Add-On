import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

test('self-contained browser bundle loads SDK and updates the panel without Node globals', () => {
  const makeElement = () => ({
    addEventListener(event, callback) { this[event] = callback; },
    replaceChildren(...children) { this.children = children; },
    append(...children) { this.children = children; },
  });
  const elements = Object.fromEntries(['message', 'provider', 'cancel', 'view-cards',
    'repair-note-type', 'failure-action', 'recovery-jobs', 'settings'].map(id => [
    `#${id}`, makeElement(),
  ]));
  const commands = [];
  const context = vm.createContext({
    window: {}, document: { querySelector: selector => elements[selector], createElement: makeElement },
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
  elements['#cancel'].click();
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
