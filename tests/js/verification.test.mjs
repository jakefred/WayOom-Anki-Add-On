import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { openVerificationPeer, options, verifyProvider } from '../../scripts/verify_provider.mjs';

const python = process.env.WAYOOM_TEST_PYTHON ?? (existsSync('.venv/Scripts/python.exe') ? '.venv/Scripts/python.exe' : existsSync('.venv/bin/python') ? '.venv/bin/python' : 'python');

test('live tools require explicit opt-in before reading keys or starting a peer', () => {
  assert.throws(() => options(['--provider', 'openai']));
  assert.throws(() => options(['--live', '--provider', 'unknown']));
  assert.throws(() => options(['--live', '--provider', 'openai', '--unexpected']));
  assert.equal(options(['--help']).help, true);
  const denied = spawnSync(process.execPath, ['scripts/verify_provider.mjs', '--provider', 'openai'], { encoding: 'utf8' });
  assert.equal(denied.status, 1);
  const peerDenied = spawnSync(python, ['scripts/verification_peer.py', '--provider', 'openai'], { encoding: 'utf8' });
  assert.notEqual(peerDenied.status, 0);
  assert.match(peerDenied.stderr, /--live is required/);
});

test('owner verification driver exercises real SDK/bridge with socket-free fake: naming, cancellation and all quality cases', { timeout: 30000 }, async () => {
  const peer = openVerificationPeer({ python, args: ['-u', 'tests/integration/bridge_peer.py', '--generation'] });
  try {
    await peer.client.rpc('fixture-engine-setup', { provider: 'openai' });
    const { report, runs } = await verifyProvider(peer, { provider: 'openai', quality: true });
    assert.equal(report.connection, 'success');
    assert.equal(report.structuredGeneration, true);
    assert.equal(report.cancellationAfterSend, true);
    assert.equal(report.networkMode, 'socket-free fake');
    assert.equal(runs.length, 9); assert.equal(report.qualityRuns, 9);
    assert.ok(runs.every(run => run.cards.length === 5));
    assert.equal(new Set(runs.map(run => run.topic)).size, 3);
    assert.equal(new Set(runs.map(run => run.difficulty)).size, 3);
    assert.equal((await peer.client.rpc('fixture-engine-stats')).count, 48);
    assert.ok(!JSON.stringify({ report, runs }).includes('fake-python-only-credential-123'));
  } finally { assert.equal(await peer.close(), true); }
});

test('rejected connection stops generation and failed cancellation stops quality sampling', async () => {
  const peer = { client: { rpc: async () => ({ providerId: 'openai' }) }, connectionTest: async () => 'authentication',
    generate: () => { throw new Error('Must not generate after failed test'); } };
  assert.equal((await verifyProvider(peer, { provider: 'openai' })).report.structuredGeneration, false);
  peer.connectionTest = async () => 'success';
  let generated = 0; peer.generate = async () => { generated++; return {}; }; peer.cancelAfterSend = async () => false;
  const result = await verifyProvider(peer, { provider: 'openai', quality: true });
  assert.equal(generated, 1); assert.equal(result.runs.length, 0);
});

test('actual owner Python peer uses environment/session credentials and disposable Anki with an injected socket-free HTTPS fixture', { timeout: 30000 }, async () => {
  const peer = openVerificationPeer({ python, args: ['-u', 'tests/integration/verification_peer_fixture.py'] });
  try {
    const { report, runs } = await verifyProvider(peer, { provider: 'openai', quality: true });
    assert.equal(report.modelId, 'fixture');
    assert.equal(report.connection, 'success');
    assert.equal(report.structuredGeneration, true);
    assert.equal(report.cancellationAfterSend, true);
    assert.equal(runs.length, 9);
    assert.equal((await peer.client.rpc('verification-status')).cardCount, 48);
    assert.ok(!JSON.stringify({ report, runs }).includes('fake-environment-key-for-owner-peer'));
  } finally { assert.equal(await peer.close(), true); }
});
