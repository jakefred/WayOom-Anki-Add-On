export { createProviderModel, generateText, Output, jsonSchema } from './sdk.mjs';
export { BridgeClient } from './bridge.mjs';
export { classifyFailure } from './failures.mjs';
export { runConnectionTest } from './connection-test.mjs';
export { GenerationEngine } from './generation.mjs';
export { generationPrompt, cardsWireSchema, namingWireSchema } from './generation-provider.mjs';
import { BridgeClient } from './bridge.mjs';
import { runConnectionTest } from './connection-test.mjs';
import { GenerationEngine } from './generation.mjs';

const client = new BridgeClient(command => pycmd(command), {
  onEvent: event => {
    if (event.event === 'connection-test') runConnectionTest(client, event).catch(() => {});
    else engine.handleEvent(event);
  },
});
window.wayoomBridge = client;
const engine = new GenerationEngine(client);
window.wayoomGeneration = engine;
client.rpc('ready').catch(() => {});

let recoveryKey = '';
window.wayoomSetState = state => {
  document.querySelector('#message').textContent = state.message;
  const provider = state.provider ?? state.configuredProvider;
  document.querySelector('#provider').textContent = provider ? `Provider: ${provider}` : '';
  document.querySelector('#cancel').hidden = !['generating', 'validating', 'adding'].includes(state.state);
  document.querySelector('#cancel').disabled = state.state === 'adding';
  document.querySelector('#view-cards').hidden = !state.canView;
  document.querySelector('#repair-note-type').hidden = state.action !== 'repair-note-type';
  document.querySelector('#failure-action').textContent = {
    retry: 'Retry generation with the same inputs.',
    'reopen-profile': 'Reopen this profile to check the saved job before retrying.',
    'repair-note-type': 'Create a replacement, then retry. Existing notes and customizations will be preserved.',
  }[state.action] ?? '';
  const jobs = state.recoveryJobs ?? [];
  const key = JSON.stringify(jobs);
  if (key !== recoveryKey) {
    recoveryKey = key;
    const rows = jobs.map(requestId => {
      const row = document.createElement('p');
      const label = document.createElement('span');
      label.textContent = `Job ${requestId} `;
      const inspect = document.createElement('button');
      inspect.textContent = 'View tagged cards';
      inspect.addEventListener('click', () => pycmd(`wayoom:v1:inspect:${requestId}`));
      const acknowledge = document.createElement('button');
      acknowledge.textContent = 'Acknowledge — keep cards';
      acknowledge.addEventListener('click', () => pycmd(`wayoom:v1:acknowledge:${requestId}`));
      row.append(label, inspect, acknowledge);
      return row;
    });
    document.querySelector('#recovery-jobs').replaceChildren(...rows);
  }
};
document.querySelector('#cancel').addEventListener('click', () => pycmd('wayoom:v1:cancel'));
document.querySelector('#view-cards').addEventListener('click', () => pycmd('wayoom:v1:view-cards'));
document.querySelector('#repair-note-type').addEventListener('click', () => pycmd('wayoom:v1:repair-note-type'));
document.querySelector('#settings').addEventListener('click', () => pycmd('wayoom:v1:settings'));
