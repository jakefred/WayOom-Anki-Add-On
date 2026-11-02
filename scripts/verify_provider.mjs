// Owner-only driver. The browser VM receives no Node globals or environment keys.
import { spawn } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { webcrypto } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import vm from 'node:vm';

export const qualityTopics = ['Cell structure and function', 'Newtonian mechanics', 'Causes and consequences of the French Revolution'];
export const difficulties = ['beginner', 'intermediate', 'advanced'];
const providers = ['openai', 'anthropic', 'google', 'xai', 'mistral'];

export function openVerificationPeer({ python, args, env = process.env }) {
  const child = spawn(python, args, { stdio: ['pipe', 'pipe', 'pipe'], env });
  let stderrSeen = false, localError = false, testResult, cancellationArmed = false, sent = false, lastCommit;
  child.stderr.on('data', () => { stderrSeen = true; });
  const widget = { addEventListener() {} };
  const context = vm.createContext({ window: {}, document: { querySelector: () => widget },
    pycmd: command => {
      const message = JSON.parse(command.slice('wayoom:v1:rpc:'.length));
      context.window.wayoomBridge.receive({ v: 1, id: message.id, ok: true, result: {} });
    }, TextEncoder, TextDecoder, URL, URLSearchParams, Headers, Request, Response,
    AbortController, AbortSignal, ReadableStream, TransformStream, setTimeout, clearTimeout,
    crypto: webcrypto, performance, btoa, atob,
  });
  vm.runInContext(readFileSync('web/dist/panel.js', 'utf8'), context);
  const { BridgeClient, GenerationEngine, runConnectionTest } = context.WayOomPanel;
  const client = new BridgeClient(command => {
    const message = JSON.parse(command.slice('wayoom:v1:rpc:'.length));
    if (message.op === 'commit') lastCommit = message.payload.result;
    child.stdin.write(`${JSON.stringify(message)}\n`);
  }, { onEvent: event => {
    engine.handleEvent(event);
    if (event.event === 'connection-test') void runConnectionTest(client, event);
    if (event.event === 'verification-test-result') testResult = event.category;
    if (event.event === 'verification-request-sent' && cancellationArmed) { sent = true; engine.cancel(); }
    if (event.event === 'verification-local-error') { localError = true; client.close('provider'); }
  } });
  const engine = new GenerationEngine(client);
  child.on('error', () => { localError = true; client.close('provider'); });
  child.stdin.on('error', () => client.close('provider'));
  child.on('exit', () => client.close('provider'));
  const lines = createInterface({ input: child.stdout });
  lines.on('line', line => {
    try { client.receive(JSON.parse(line)); }
    catch { localError = true; client.close('provider'); }
  });
  return { client, engine,
    classify: error => context.WayOomPanel.classifyFailure(error),
    async connectionTest() {
      testResult = undefined;
      await client.rpc('verification-test');
      const deadline = Date.now() + 35000;
      while (testResult === undefined && !client.closed && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 20));
      return testResult ?? 'timeout';
    },
    async generate(request) {
      lastCommit = null;
      const result = await engine.run(request);
      if (!lastCommit || result.outcome.count !== request.cardCount) throw new Error('Verification failed');
      return { result, cards: lastCommit.cards };
    },
    async cancelAfterSend(request) {
      cancellationArmed = true; sent = false;
      try { await engine.run(request); return false; }
      catch (error) { return sent && error.category === 'canceled' && engine.retainedCount === 0; }
      finally { cancellationArmed = false; }
    },
    async close() {
      client.close(); child.stdin.end(); lines.close();
      let forced = false;
      if (child.exitCode === null) await new Promise(resolve => {
        const timer = setTimeout(() => { forced = true; child.kill(); resolve(); }, 3000);
        child.once('exit', () => { clearTimeout(timer); resolve(); });
      });
      return !stderrSeen && !localError && !forced && child.exitCode === 0;
    },
  };
}

export async function verifyProvider(peer, { provider, quality = false } = {}) {
  await peer.client.rpc('ready');
  const info = await peer.client.rpc('verification-info');
  if (info.providerId !== provider) throw new Error('Provider mismatch');
  const report = { ...info, structuredGeneration: false, cancellationAfterSend: false,
    credentialHandling: 'Python session-only environment key; OS stores remain manual',
    boundary: 'Browser VM, real bridge/proxy and disposable Anki; Qt scheduling doubled', qualityRuns: 0 };
  report.connection = await peer.connectionTest();
  if (report.connection !== 'success') return { report, runs: [] };
  const request = (topic, difficulty, count, name) => ({ requestId: 'verification', providerId: provider,
    destination: { mode: 'new', name }, cardCount: count, topic, difficulty });
  // Dedicated AI naming plus a real structured generation and insertion.
  await peer.generate(request(qualityTopics[0], 'intermediate', 3, null));
  report.structuredGeneration = true;
  const beforeCancel = await peer.client.rpc('verification-status');
  const canceled = await peer.cancelAfterSend(request(qualityTopics[1], 'beginner', 1, 'Cancellation probe'));
  const afterCancel = await peer.client.rpc('verification-status');
  report.cancellationAfterSend = canceled && afterCancel.state === 'canceled'
    && Number.isInteger(beforeCancel.cardCount) && beforeCancel.cardCount === afterCancel.cardCount;
  const runs = [];
  if (quality && report.cancellationAfterSend) {
    for (const topic of qualityTopics) for (const difficulty of difficulties) {
      const { cards } = await peer.generate(request(topic, difficulty, 5, `Quality sample ${runs.length + 1}`));
      runs.push({ providerId: provider, modelId: info.modelId, topic, difficulty, cards });
    }
  }
  report.qualityRuns = runs.length;
  return { report, runs };
}

export function options(argv) {
  const result = {};
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (['--live', '--help'].includes(arg)) result[arg.slice(2)] = true;
    else if (['--provider', '--model', '--quality-output'].includes(arg) && argv[i + 1] && !argv[i + 1].startsWith('--')) result[arg.slice(2)] = argv[++i];
    else throw new Error('Invalid options');
  }
  if (!result.help && (!result.live || !providers.includes(result.provider))) throw new Error('Explicit --live and --provider are required');
  return result;
}

async function main() {
  let peer;
  try {
    const args = options(process.argv.slice(2));
    if (args.help) {
      process.stdout.write('Usage: node scripts/verify_provider.mjs --live --provider openai|anthropic|google|xai|mistral [--model ID] [--quality-output FILE]\nUses WAYOOM_<PROVIDER>_API_KEY in Python only. Billable calls; disposable collection. Run npm run build first.\n');
      return;
    }
    if (args['quality-output'] && existsSync(args['quality-output'])) throw new Error('Output already exists');
    const python = process.env.WAYOOM_TEST_PYTHON ?? (existsSync('.venv/Scripts/python.exe') ? '.venv/Scripts/python.exe' : existsSync('.venv/bin/python') ? '.venv/bin/python' : 'python');
    const childEnv = { ...process.env };
    for (const provider of providers) if (provider !== args.provider) delete childEnv[`WAYOOM_${provider.toUpperCase()}_API_KEY`];
    peer = openVerificationPeer({ python, env: childEnv,
      args: ['-u', 'scripts/verification_peer.py', '--live', '--provider', args.provider, ...(args.model ? ['--model', args.model] : [])] });
    const { report, runs } = await verifyProvider(peer, { provider: args.provider, quality: !!args['quality-output'] });
    if (args['quality-output'] && runs.length === 9) writeFileSync(args['quality-output'], JSON.stringify({ schemaVersion: 1, runs }, null, 2) + '\n', { flag: 'wx' });
    process.stdout.write(JSON.stringify(report, null, 2) + '\n');
    if (report.connection !== 'success' || !report.structuredGeneration || !report.cancellationAfterSend) process.exitCode = 1;
  } catch (error) {
    const category = peer ? peer.classify(error) : 'local';
    process.stderr.write(`Verification failed (${category}). Check explicit --live/--provider, environment key, model, bundle, output path and dependencies. Raw provider errors are suppressed.\n`);
    process.exitCode = 1;
  } finally {
    if (peer && !await peer.close()) process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
