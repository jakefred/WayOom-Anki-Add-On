import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { deckNamingResultSchema, requestSchema, validateGeneration } from '../../web/validation.mjs';

const fixtures = JSON.parse(readFileSync(new URL('../fixtures/validation.json', import.meta.url), 'utf8'));
const makeRequest = patch => ({ ...structuredClone(fixtures.request), ...patch });
const makeResult = count => ({ deckName: null, cards: Array.from({ length: count }, (_, i) => ({ front: `Question ${i}?`, back: `Answer ${i}` })) });

test('dedicated naming result repairs its title without requiring card output', () => {
  assert.deepEqual(deckNamingResultSchema.parse({ deckName: 'Biology::Cells' }), { deckName: 'Biology — Cells' });
  assert.throws(() => deckNamingResultSchema.parse({ deckName: ' ' }));
  assert.throws(() => deckNamingResultSchema.parse({ deckName: 'Biology', cards: [] }));
});

for (const fixture of fixtures.cases) {
  test(`shared contract: ${fixture.name}`, () => {
    const values = structuredClone({ request: fixtures.request, result: fixtures.result });
    for (const edit of fixture.edits) {
      let target = values[edit.target];
      for (const key of edit.path.slice(0, -1)) target = target[key];
      const key = edit.path.at(-1);
      if (edit.delete) delete target[key];
      else target[key] = edit.value;
    }
    const action = () => validateGeneration(values.request, values.result);
    if (fixture.valid) {
      const result = action();
      if (Object.hasOwn(fixture, 'expectedDeckName')) {
        assert.equal(result.deckName, fixture.expectedDeckName);
        assert.equal(validateGeneration(values.request, result).deckName, result.deckName);
      }
    }
    else assert.throws(action, { name: 'ZodError' });
  });
}

for (const count of [1, 2, 100]) {
  test(`accept exactly ${count} Basic cards`, () => {
    assert.equal(validateGeneration(makeRequest({ cardCount: count }), makeResult(count)).cards.length, count);
  });
}

for (const [field, limit] of [['front', 500], ['back', 2000]]) {
  for (const char of ['x', '😀']) {
    test(`${field}: ${limit} Unicode code points accepted; ${limit + 1} rejected (${char})`, () => {
      const result = makeResult(1);
      result.cards[0][field] = char.repeat(limit);
      assert.doesNotThrow(() => validateGeneration(makeRequest(), result));
      result.cards[0][field] += char;
      assert.throws(() => validateGeneration(makeRequest(), result));
    });
  }
}

test('topic length boundary', () => {
  assert.doesNotThrow(() => requestSchema.parse(makeRequest({ topic: 'x'.repeat(2000) })));
  assert.throws(() => requestSchema.parse(makeRequest({ topic: 'x'.repeat(2001) })));
});

test('all provider and difficulty combinations use the same contract', () => {
  for (const providerId of ['openai', 'anthropic', 'google', 'xai', 'mistral']) {
    for (const difficulty of ['beginner', 'intermediate', 'advanced']) {
      assert.doesNotThrow(() => validateGeneration(makeRequest({ providerId, difficulty }), makeResult(1)));
    }
  }
});

test('normalizes NFC and whitespace without mutating the original', () => {
  const result = makeResult(1);
  result.cards[0].front = '  Cafe\u0301  ';
  assert.equal(validateGeneration(makeRequest(), result).cards[0].front, 'Café');
  assert.equal(result.cards[0].front, '  Cafe\u0301  ');
});

test('reject duplicate canonical and compatibility Unicode questions', () => {
  for (const [a, b] of [['Café?', 'Cafe\u0301?'], ['DNA?', 'ＤＮＡ?']]) {
    const result = { deckName: null, cards: [{ front: a, back: 'A' }, { front: b, back: 'B' }] };
    assert.throws(() => validateGeneration(makeRequest({ cardCount: 2 }), result));
  }
});
