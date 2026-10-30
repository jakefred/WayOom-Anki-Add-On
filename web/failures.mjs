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
  return 'provider';
}
