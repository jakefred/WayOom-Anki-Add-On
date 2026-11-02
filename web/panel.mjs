export { createProviderModel, generateText, Output, jsonSchema } from './sdk.mjs';
export { BridgeClient } from './bridge.mjs';
export { classifyFailure } from './failures.mjs';
export { runConnectionTest } from './connection-test.mjs';
export { GenerationEngine } from './generation.mjs';
export { GenerationPanel } from './form.mjs';
export { generationPrompt, cardsWireSchema, namingWireSchema } from './generation-provider.mjs';
import { BridgeClient } from './bridge.mjs';
import { runConnectionTest } from './connection-test.mjs';
import { GenerationEngine } from './generation.mjs';
import { GenerationPanel } from './form.mjs';

function mountPanel() {
  const client = new BridgeClient(command => pycmd(command), {
    onEvent: event => {
      if (event.event === 'connection-test') runConnectionTest(client, event).catch(() => {});
      else {
        engine.handleEvent(event);
        if (event.event === 'closed') panel.close();
      }
    },
  });
  window.wayoomBridge = client;
  const engine = new GenerationEngine(client, { onProgress: progress => panel.setProgress(progress) });
  window.wayoomGeneration = engine;
  const panel = new GenerationPanel(document, client, engine, command => pycmd(command));
  window.wayoomPanel = panel;
  window.wayoomSetState = state => panel.setState(state);
  let connecting = false;
  // Anki installs pycmd asynchronously through QWebChannel after DocumentReady.
  // Native evals queued until domDone (and the status timer) retry this handshake.
  window.wayoomConnect = () => {
    if (connecting || client.closed || typeof pycmd !== 'function') return;
    connecting = true;
    client.rpc('ready').then(result => {
      if (result?.state) panel.setState(result.state);
    }).catch(() => {}).finally(() => { connecting = false; });
  };
  window.wayoomConnect();
}

// Anki's stdHtml places scripts before the body. Bind only after the form exists.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', mountPanel, { once: true });
} else mountPanel();
