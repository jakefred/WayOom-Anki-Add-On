export { createProviderModel, generateText, Output, jsonSchema } from './sdk.mjs';

window.wayoomSetState = state => {
  document.querySelector('#message').textContent = state.message;
  document.querySelector('#provider').textContent = state.provider ? `Provider: ${state.provider}` : '';
  document.querySelector('#cancel').hidden = !['generating', 'validating', 'adding'].includes(state.state);
  document.querySelector('#cancel').disabled = state.state === 'adding';
  document.querySelector('#view-cards').hidden = !state.canView;
};
document.querySelector('#cancel').addEventListener('click', () => pycmd('wayoom:v1:cancel'));
document.querySelector('#view-cards').addEventListener('click', () => pycmd('wayoom:v1:view-cards'));
