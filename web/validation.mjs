import { z } from 'zod';

// Syntax that Anki interprets outside HTML rendering, even on Basic cards.
const reservedSyntax = /\{\{c\d+::|\[(?:sound:|video:|latex\]|\${1,2}\])/iu;
const controlCharacters = /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/u;

function text(maximum, cardField = false) {
  return z.string().transform(value => value.normalize('NFC').trim()).superRefine((value, ctx) => {
    if (!value.replace(/[\s\p{Cf}]/gu, '').length || [...value].length > maximum || !value.isWellFormed()) {
      ctx.addIssue({ code: 'custom', message: `Expected 1–${maximum} valid text characters` });
    }
    if (controlCharacters.test(value) || (cardField && reservedSyntax.test(value))) {
      ctx.addIssue({ code: 'custom', message: 'Unsupported control character or Anki card syntax' });
    }
  });
}

const deckName = text(100)
  .refine(value => !/[\x00-\x1f\x7f-\x9f]/u.test(value), 'Deck names cannot contain control characters')
  .refine(value => !value.includes('::'), 'Use a top-level deck name');
// Repair model-proposed hierarchies before checking the final name. Supplied
// names use deckName directly so user input is never silently rewritten.
export const generatedDeckNameSchema = z.string()
  .transform(value => value.replace(/ *:{2,} */gu, ' — '))
  .pipe(deckName);
export const deckNamingResultSchema = z.strictObject({ deckName: generatedDeckNameSchema });
const newDeckName = z.union([z.string(), z.null()])
  .transform(value => value === null || !value.trim() ? null : value)
  .pipe(deckName.nullable());

export const requestSchema = z.strictObject({
  requestId: text(128),
  destination: z.discriminatedUnion('mode', [
    z.strictObject({ mode: z.literal('existing'), deckId: z.number().int().positive().max(Number.MAX_SAFE_INTEGER) }),
    z.strictObject({ mode: z.literal('new'), name: newDeckName }),
  ]),
  cardCount: z.number().int().min(1).max(100),
  topic: text(2000),
  difficulty: z.enum(['beginner', 'intermediate', 'advanced']),
  providerId: z.enum(['openai', 'anthropic', 'google', 'xai', 'mistral']),
});

export const basicCardSchema = z.strictObject({
  front: text(500, true),
  back: text(2000, true),
});

// Local acceptance schema. A provider adapter needs a simpler JSON wire schema;
// arbitrary transformations/refinements cannot be enforced by the provider.
export function resultSchema(request) {
  const parsedRequest = requestSchema.parse(request);
  const needsName = parsedRequest.destination.mode === 'new' && parsedRequest.destination.name === null;
  return z.strictObject({
    deckName: needsName ? generatedDeckNameSchema : z.null(),
    cards: z.array(basicCardSchema).length(parsedRequest.cardCount),
  }).superRefine((result, ctx) => {
    const seen = new Set();
    result.cards.forEach((card, index) => {
      // Fast browser check; Python additionally performs full Unicode case folding.
      const front = card.front.normalize('NFKC').toLowerCase().replace(/\s+/gu, ' ').trim();
      if (seen.has(front)) {
        ctx.addIssue({ code: 'custom', path: ['cards', index, 'front'], message: 'Duplicate question' });
      }
      seen.add(front);
    });
  });
}

export function validateGeneration(request, result) {
  return resultSchema(request).parse(result);
}
