import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

test('self-contained browser bundle loads SDK and updates the panel without Node globals', () => {
  const elements = Object.fromEntries(['message', 'provider', 'cancel', 'view-cards'].map(id => [
    `#${id}`, { addEventListener(event, callback) { this[event] = callback; } },
  ]));
  const commands = [];
  const context = vm.createContext({
    window: {}, document: { querySelector: selector => elements[selector] },
    pycmd: command => commands.push(command),
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
});
