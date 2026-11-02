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
client.rpc('ready').catch(() => {});
