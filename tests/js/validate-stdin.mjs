// Integration-test entry point: the real Zod validator, with no provider calls.
import { readFileSync } from 'node:fs';
import { requestSchema, validateGeneration } from '../../web/validation.mjs';

const input = JSON.parse(readFileSync(0, 'utf8'));
const request = requestSchema.parse(input.request);
const result = validateGeneration(request, input.result);
process.stdout.write(JSON.stringify({ request, result }));
