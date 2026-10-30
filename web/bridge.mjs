export class BridgeError extends Error {
  constructor(category) { super(`WayOom ${category}`); this.name = 'BridgeError'; this.category = category; }
}

export class BridgeClient {
  constructor(send, { timeout = 65000, onEvent = () => {} } = {}) {
    this.send = send;
    this.timeout = timeout;
    this.onEvent = onEvent;
    this.nextId = 0;
    this.pending = new Map();
    this.closed = false;
  }

  rpc(op, payload = {}, { owner, signal } = {}) {
    owner ??= payload.owner ?? payload.requestId;
    if (this.closed || signal?.aborted) return Promise.reject(new BridgeError('canceled'));
    const id = `rpc-${++this.nextId}`;
    return new Promise((resolve, reject) => {
      const finish = (error, result) => {
        const entry = this.pending.get(id);
        if (!entry) return;
        this.pending.delete(id);
        clearTimeout(entry.timer);
        signal?.removeEventListener('abort', abort);
        error ? reject(error) : resolve(result);
      };
      const abort = () => {
        finish(new BridgeError('canceled'));
        this.rpc('abort', { id }).catch(() => {});
      };
      const timer = setTimeout(() => {
        finish(new BridgeError('timeout'));
        if (op !== 'abort') this.rpc('abort', { id }).catch(() => {});
      }, this.timeout);
      this.pending.set(id, { finish, timer, owner });
      signal?.addEventListener('abort', abort, { once: true });
      try { this.send(`wayoom:v1:rpc:${JSON.stringify({ v: 1, id, op, payload })}`); }
      catch { finish(new BridgeError('refused')); }
    });
  }

  receive(message) {
    if (message?.v !== 1) return;
    if (message.event) {
      if (message.event === 'closed') this.close(message.category);
      if (message.event === 'owner-ended') {
        for (const entry of [...this.pending.values()]) {
          if (entry.owner === message.owner) entry.finish(new BridgeError(message.category));
        }
      }
      this.onEvent(message);
      return;
    }
    const entry = this.pending.get(message.id);
    if (entry) entry.finish(message.ok ? null : new BridgeError(message.error?.category ?? 'refused'), message.result);
  }

  fetch(context) {
    return async (input, init) => {
      const request = new Request(input, init);
      const body = await request.text();
      if (new TextEncoder().encode(body).length > 1024 * 1024) throw new BridgeError('oversized');
      const response = await this.rpc('fetch', {
        ...context,
        request: { url: request.url, method: request.method, headers: Object.fromEntries(request.headers), body },
      }, { owner: context.owner, signal: request.signal });
      return new Response(response.body, { status: response.status, headers: response.headers });
    };
  }

  close(category = 'canceled') {
    this.closed = true;
    for (const entry of [...this.pending.values()]) entry.finish(new BridgeError(category));
  }
}
