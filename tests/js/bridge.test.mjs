import test from 'node:test';
import assert from 'node:assert/strict';
import { BridgeClient, BridgeError } from '../../web/bridge.mjs';
import { classifyFailure, failureMessages, failureDiagnostic } from '../../web/failures.mjs';
import { APICallError, NoObjectGeneratedError } from 'ai';

test('bridge fetch forwards a serialized request and resolves a buffered response', async () => {
  const client = new BridgeClient(command => {
    const message = JSON.parse(command.slice('wayoom:v1:rpc:'.length));
    assert.equal(message.op, 'fetch');
    assert.equal(message.payload.owner, 'job');
    assert.equal(message.payload.request.headers.authorization, 'Bearer wayoom-bridge-placeholder');
    client.receive({ v: 1, id: message.id, ok: true, result: { status: 200, headers: { 'content-type': 'application/json' }, body: '{"ok":true}' } });
  });
  const response = await client.fetch({ owner: 'job', kind: 'job', stage: 'cards', batch: 0 })(
    'https://api.openai.com/v1/responses', { method: 'POST', headers: { authorization: 'Bearer wayoom-bridge-placeholder' }, body: '{}' });
  assert.deepEqual(await response.json(), { ok: true });
  assert.equal(client.pending.size, 0);
});

test('Python owner cancellation rejects pending calls and ignores late responses', async () => {
  let message;
  const client = new BridgeClient(command => { message = JSON.parse(command.slice('wayoom:v1:rpc:'.length)); });
  const pending = client.rpc('duplicates', { requestId: 'job', fronts: [] });
  client.receive({ v: 1, event: 'owner-ended', owner: 'job', category: 'timeout' });
  await assert.rejects(pending, error => error.category === 'timeout');
  client.receive({ v: 1, id: message.id, ok: true, result: {} });
  assert.equal(client.pending.size, 0);
  client.close();
});

test('AbortSignal sends a matching abort and rejects locally without waiting for Python', async () => {
  const messages = [];
  const client = new BridgeClient(command => {
    const message = JSON.parse(command.slice('wayoom:v1:rpc:'.length));
    messages.push(message);
    if (message.op === 'abort') client.receive({ v: 1, id: message.id, ok: true, result: {} });
  });
  const controller = new AbortController();
  const pending = client.rpc('fetch', {}, { owner: 'job', signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, error => error.category === 'canceled');
  assert.equal(messages[1].payload.id, messages[0].id);
  client.close();
});

test('shared classifier keeps auth, quota, rate, model, timeout, refusal and malformed distinct', () => {
  for (const [status, data, category] of [
    [401, {}, 'authentication'], [403, {}, 'authentication'], [429, {}, 'rate-limit'],
    [429, { error: { code: 'insufficient_quota' } }, 'quota'], [404, {}, 'model'],
    [400, { error: { code: 'model_not_found' } }, 'model'], [408, {}, 'timeout'],
    [400, { error: { type: 'content_filter' } }, 'refusal'],
    [200, {}, 'malformed'],
  ]) {
    const error = new APICallError({ message: 'Do not display raw details', url: 'https://example.invalid', requestBodyValues: {}, statusCode: status, data });
    assert.equal(classifyFailure(error), category);
  }
  assert.equal(classifyFailure({ cause: new BridgeError('network') }), 'network');
  assert.equal(classifyFailure(new NoObjectGeneratedError({ message: 'Malformed' })), 'malformed');
  assert.equal(classifyFailure({ finishReason: 'content-filter' }), 'refusal');
  assert.ok(!Object.values(failureMessages).join().includes('Do not display raw details'));
});

test('diagnostics expose only fixed labels and status, even through wrappers and cyclic causes', () => {
  const error = new APICallError({message: 'private-secret', url: 'https://example.invalid',
    requestBodyValues: {}, statusCode: 400,
    data: {error: {code: 'private-secret', param: 'private-topic', type: 'invalid_request_error'}}});
  const wrapper = new BridgeError('provider', failureDiagnostic(error));
  wrapper.cause = wrapper;
  assert.match(failureDiagnostic(wrapper), /HTTP 400/);
  assert.match(failureDiagnostic(wrapper), /invalid_request_error/);
  assert.doesNotMatch(failureDiagnostic(wrapper), /private/);
  assert.equal(failureDiagnostic({name:'private-secret', diagnostic:'private-topic'}), '');
  assert.equal(classifyFailure(new TypeError('private-secret')), 'local');
  assert.equal(failureDiagnostic(new TypeError('private-secret')), 'TypeError');
});
