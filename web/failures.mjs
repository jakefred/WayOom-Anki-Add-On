import { APICallError, NoObjectGeneratedError } from 'ai';

export const failureMessages = Object.freeze({
  success: 'Connection succeeded.',
  authentication: 'Credentials were rejected. Open settings to replace the key.',
  credentials: 'The saved key is unavailable. Open settings.',
  model: 'The selected model is unavailable or unsupported.',
  quota: 'The provider reports exhausted quota or insufficient credits.',
  'rate-limit': 'The provider is rate limiting requests. Try again later.',
  timeout: 'The request timed out.', network: 'The provider could not be reached. The saved key was not marked invalid.',
  refusal: 'The provider refused the request.', malformed: 'The provider returned malformed output.',
  refused: 'WayOom refused an unauthorized or unsupported request.',
  canceled: 'The request was canceled.', redirect: 'The provider returned a redirect; WayOom did not follow it.',
  oversized: 'The provider request or response exceeded the size limit.', provider: 'The provider request failed.',
  validation: 'Check the generation inputs before trying again.',
  local: 'WayOom encountered a local application error. This is not a reported provider rejection.',
  uncertain: 'The commit could not be confirmed. Reopen the profile to inspect its saved result.',
});

export function classifyFailure(error) {
  const seen = new Set();
  for (let current = error; current && !seen.has(current); current = current.cause) {
    seen.add(current);
    if (current.category && Object.hasOwn(failureMessages, current.category)) return current.category;
    if (current.name === 'AbortError') return 'canceled';
    if (current.name === 'TimeoutError') return 'timeout';
    if (current.finishReason === 'content-filter') return 'refusal';
    if (APICallError.isInstance(current)) {
      // The SDK supplies normalized status and parsed error data. Never display
      // raw messages, request bodies, response bodies, or exception objects.
      const status = current.statusCode;
      let code = '';
      const data = current.data;
      if (data && typeof data === 'object') {
        const value = data.error ?? data;
        code = `${value.code ?? ''} ${value.type ?? ''} ${value.status ?? ''}`.toLowerCase();
      }
      if (status === 401 || status === 403 && !/quota|credit|billing/.test(code)) return 'authentication';
      if (/quota|credit|billing/.test(code) || status === 402) return 'quota';
      if (status === 429) return 'rate-limit';
      if (status === 404 || /model.*not|not.*model|unsupported.*model/.test(code)) return 'model';
      if (/refus|content_filter|safety/.test(code)) return 'refusal';
      if (status === 408 || status === 504) return 'timeout';
      if (status >= 200 && status < 300) return 'malformed';
      if (status == null) continue;
      return 'provider';
    }
  }
  if (/UnsupportedFunctionality/.test(error?.name ?? '')) return 'model';
  if (NoObjectGeneratedError.isInstance(error) || /JSON|TypeValidation|NoOutputGenerated/.test(error?.name ?? '')) return 'malformed';
  if (['TypeError', 'ReferenceError', 'RangeError'].includes(error?.name)) return 'local';
  return 'provider';
}

// Fixed labels only: never display exception messages, bodies or headers.
const diagnosticLabels = new Set([
  'Error', 'TypeError', 'ReferenceError', 'RangeError', 'AI_APICallError', 'AI_NoObjectGeneratedError',
  'AI_InvalidArgumentError', 'AI_InvalidPromptError', 'AI_RetryError',
  'AI_NoOutputGeneratedError', 'AI_TypeValidationError', 'AI_JSONParseError',
  'unsupported_parameter', 'unsupported_value', 'invalid_request_error', 'invalid_value',
  'invalid_json_schema', 'model_not_found', 'server_error', 'context_length_exceeded',
  'max_output_tokens', 'max_completion_tokens', 'temperature', 'reasoning', 'text.format',
  'response_format', 'model', 'secure-random-unavailable',
]);
const safeLabel = value => typeof value === 'string'
  && (diagnosticLabels.has(value) || /^HTTP [1-5][0-9]{2}$/.test(value));

export function failureDiagnostic(error) {
  const labels = new Set(), seen = new Set();
  for (let current = error; current && !seen.has(current); current = current.cause) {
    seen.add(current);
    for (const label of typeof current.diagnostic === 'string' ? current.diagnostic.split('; ') : []) {
      if (safeLabel(label)) labels.add(label);
    }
    if (APICallError.isInstance(current)) {
      if (Number.isInteger(current.statusCode) && current.statusCode >= 100 && current.statusCode <= 599)
        labels.add(`HTTP ${current.statusCode}`);
      const data = current.data?.error ?? current.data;
      for (const value of [data?.code, data?.type, data?.param]) {
        if (diagnosticLabels.has(value)) labels.add(value);
      }
    }
    if (diagnosticLabels.has(current.name)) labels.add(current.name);
  }
  return [...labels].join('; ');
}
