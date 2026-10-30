// Use generateText({ output: Output.object({ schema }), maxRetries: 0 })
// for structured calls (https://ai-sdk.dev/docs/ai-sdk-core/generating-structured-data).
// Transport and job authorization are added in milestone 3. This boundary
// never reads environment credentials or uses a provider's default fetch.
export { generateText, Output, jsonSchema } from 'ai';
import { createOpenAI } from '@ai-sdk/openai';
import { createAnthropic } from '@ai-sdk/anthropic';
import { createGoogleGenerativeAI } from '@ai-sdk/google';
import { createXai } from '@ai-sdk/xai';
import { createMistral } from '@ai-sdk/mistral';

const factories = Object.freeze({
  openai: createOpenAI,
  anthropic: createAnthropic,
  google: createGoogleGenerativeAI,
  xai: createXai,
  mistral: createMistral,
});

export function createProviderModel(providerId, modelId, bridgeFetch) {
  if (!Object.hasOwn(factories, providerId)) throw new Error('Unknown provider');
  if (typeof bridgeFetch !== 'function') throw new Error('Bridge fetch is required');
  if (typeof modelId !== 'string' || !modelId.trim()) throw new Error('Model ID is required');
  return factories[providerId]({ apiKey: 'wayoom-bridge-placeholder', fetch: bridgeFetch })(modelId);
}
