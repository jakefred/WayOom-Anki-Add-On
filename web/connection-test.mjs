import { createProviderModel, generateText, Output, jsonSchema } from './sdk.mjs';
import { classifyFailure } from './failures.mjs';

export async function runConnectionTest(client, event) {
  let category = 'success';
  try {
    const model = createProviderModel(event.providerId, event.modelId,
      client.fetch({ owner: event.owner, kind: 'test', stage: 'test', batch: 0 }));
    const result = await generateText({
      model, maxRetries: 0, maxOutputTokens: 1024,
      prompt: 'Reply with the JSON object {"ok":true}. This is a connection test.',
      output: Output.object({ schema: jsonSchema({ type: 'object', properties: { ok: { type: 'boolean' } }, required: ['ok'], additionalProperties: false }) }),
    });
    if (result.finishReason === 'content-filter') category = 'refusal';
    else if (result.output?.ok !== true) category = 'malformed';
  } catch (error) { category = classifyFailure(error); }
  // Only the category is returned. SDK exceptions may contain user content.
  await client.rpc('test-result', { owner: event.owner, category }).catch(() => {});
  return category;
}
