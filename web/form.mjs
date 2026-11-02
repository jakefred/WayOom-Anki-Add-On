import { requestSchema } from './validation.mjs';
import { classifyFailure, failureMessages, failureDiagnostic } from './failures.mjs';

const busyStates = new Set(['generating', 'validating', 'adding', 'starting']);
const stageNames = { starting: 'Starting', naming: 'Naming deck', cards: 'Generating cards',
  validating: 'Validating cards', adding: 'Adding cards' };
const providerNames = { openai: 'OpenAI', anthropic: 'Anthropic', google: 'Google Gemini', xai: 'xAI', mistral: 'Mistral AI' };

// The form owns its DOM for the webview lifetime. State pushes update status and
// enabled controls only; they never populate an input from a job snapshot.
export class GenerationPanel {
  constructor(document, client, engine, command) {
    Object.assign(this, { document, client, engine, command });
    this.state = {};
    this.decks = [];
    this.loaded = false;
    this.loading = false;
    this.refreshSequence = 0;
    this.running = false;
    this.closed = false;
    this.uncertain = false;
    this.terminal = null;
    this.progress = null;
    this.errors = {};
    this.recoveryKey = '';
    this.get('generation-form').addEventListener('submit', event => { event.preventDefault(); void this.submit(); });
    this.get('retry').addEventListener('click', () => { void this.submit(); });
    this.get('refresh-decks').addEventListener('click', () => { void this.refreshDecks(); });
    for (const id of ['destination', 'deck-name', 'card-count', 'topic', 'difficulty']) {
      this.get(id).addEventListener(['destination', 'difficulty'].includes(id) ? 'change' : 'input', () => {
        this.engine.discard();
        this.terminal = null;
        this.clearErrors();
        this.render();
      });
    }
    this.get('cancel').addEventListener('click', () => {
      if (this.engine.active) {
        this.engine.cancel();
        this.render();
      } else this.command('wayoom:v1:cancel');
    });
    for (const [id, action] of [['settings', 'settings'], ['view-cards', 'view-cards'], ['repair-note-type', 'repair-note-type']]) {
      this.get(id).addEventListener('click', () => this.command(`wayoom:v1:${action}`));
    }
  }

  get(id) { return this.document.querySelector(`#${id}`); }
  text(id, value) {
    const element = this.get(id);
    if (element.textContent !== value) element.textContent = value;
  }

  clearErrors() {
    this.errors = {};
    for (const id of ['destination', 'deck-name', 'card-count', 'topic', 'difficulty']) {
      this.text(`${id}-error`, '');
      this.get(id).setAttribute('aria-invalid', 'false');
    }
  }

  validate() {
    this.clearErrors();
    const destination = this.get('destination').value;
    const count = this.get('card-count').value.trim();
    const input = { requestId: 'form',
      destination: destination === 'new' ? { mode: 'new', name: this.get('deck-name').value }
        : { mode: 'existing', deckId: Number(destination) },
      cardCount: count === '' ? NaN : Number(count), topic: this.get('topic').value,
      difficulty: this.get('difficulty').value, providerId: this.state.configuredProvider };
    const result = requestSchema.safeParse(input);
    if (!result.success) {
      for (const issue of result.error.issues) {
        const field = { destination: issue.path[1] === 'name' ? 'deck-name' : 'destination',
          cardCount: 'card-count', topic: 'topic', difficulty: 'difficulty' }[issue.path[0]];
        if (field) this.errors[field] = {
          destination: 'Choose an available normal deck or Create new deck.',
          'deck-name': 'Use up to 100 characters, without control characters or ::. Leave blank for AI naming.',
          'card-count': 'Enter a whole number from 1 to 100.',
          topic: 'Enter a topic of 1–2,000 characters.', difficulty: 'Choose a difficulty.',
        }[field];
      }
    }
    if (destination !== 'new' && !this.decks.some(deck => String(deck.deckId) === destination)) {
      this.errors.destination = 'Choose an available normal deck or Create new deck.';
    }
    for (const [id, message] of Object.entries(this.errors)) {
      this.text(`${id}-error`, message);
      this.get(id).setAttribute('aria-invalid', 'true');
    }
    if (Object.keys(this.errors).length || !result.success) {
      const first = Object.keys(this.errors)[0];
      if (first) this.get(first).focus();
      return null;
    }
    return result.data;
  }

  setState(state) {
    if (this.closed) return;
    if (this.state.profileEpoch !== undefined && state.profileEpoch !== undefined && state.profileEpoch !== this.state.profileEpoch) {
      this.engine.discard({ notify: false });
      this.terminal = this.progress = null;
      this.uncertain = false;
      this.loaded = false;
      this.refreshSequence++;
      this.loading = false;
      this.decks = [];
      this.get('generation-form').reset();
      this.clearErrors();
    } else if (this.state.configurationToken !== undefined && state.configurationToken !== this.state.configurationToken) {
      this.engine.discard();
    }
    this.state = state;
    this.render();
    if (state.profileReady && !this.loaded && !this.loading) void this.refreshDecks();
  }

  setProgress(progress) {
    this.progress = progress;
    this.render();
  }

  close() {
    this.closed = true;
    this.refreshSequence++;
    this.terminal = this.progress = null;
    this.render();
  }

  async refreshDecks() {
    if (this.closed || !this.state.profileReady) return;
    const sequence = ++this.refreshSequence;
    this.loading = true;
    this.text('deck-status', 'Refreshing decks…');
    this.render();
    try {
      const result = await this.client.rpc('destinations');
      if (sequence !== this.refreshSequence || this.closed) return;
      const selected = this.get('destination').value;
      const decks = result.decks;
      if (JSON.stringify(decks) !== JSON.stringify(this.decks) || !this.loaded) {
        this.decks = decks;
        const option = (value, label, disabled = false) => {
          const element = this.document.createElement('option');
          element.value = value; element.textContent = label; element.disabled = disabled;
          return element;
        };
        const options = [option('', 'Choose a deck'), option('new', 'Create new deck'),
          ...decks.map(deck => option(String(deck.deckId), deck.name))];
        if (selected && selected !== 'new' && !decks.some(deck => String(deck.deckId) === selected)) {
          options.push(option(selected, 'Unavailable deck — choose another destination', true));
          this.engine.discard();
        }
        this.get('destination').replaceChildren(...options);
        this.get('destination').value = selected;
      }
      this.loaded = true;
      this.text('deck-status', decks.length ? '' : 'No normal decks found. Create a new deck.');
    } catch {
      if (sequence === this.refreshSequence && !this.closed) {
        // A failed refresh must not restart on every 500 ms state push.
        this.loaded = true;
        this.text('deck-status', 'Decks could not be refreshed. Try Refresh decks again.');
      }
    } finally {
      if (sequence === this.refreshSequence) { this.loading = false; this.render(); }
    }
  }

  async submit() {
    if (!this.available()) return;
    const request = this.validate();
    if (!request) { this.engine.discard(); return; }
    this.running = true;
    this.terminal = null;
    this.progress = { stage: 'starting' };
    this.render(); // Synchronous acknowledgement also guards a second click.
    const epoch = this.state.profileEpoch;
    try {
      const result = await this.engine.run(request);
      if (this.closed || epoch !== this.state.profileEpoch) return;
      this.terminal = { state: 'complete', canView: true,
        message: `Added ${result.outcome.count} cards to ${result.outcome.deckName}` };
      void this.refreshDecks();
    } catch (error) {
      if (this.closed || epoch !== this.state.profileEpoch) return;
      const category = classifyFailure(error);
      const diagnostic = failureDiagnostic(error);
      this.uncertain = category === 'uncertain';
      this.terminal = category === 'canceled' ? { state: 'canceled', message: 'Canceled. No cards were added.' }
        : { state: 'failed', stage: error.stage,
          message: `${stageNames[error.stage] ?? 'Generation'} failed. ${failureMessages[category] ?? failureMessages.provider}${diagnostic ? ` Details: ${diagnostic}.` : ''}` };
    } finally {
      this.running = false;
      this.progress = null;
      this.render();
    }
  }

  available() {
    return !this.closed && !this.running && !this.uncertain && !this.loading && this.state.canGenerate === true
      && !busyStates.has(this.state.state) && !this.engine.active;
  }

  render() {
    const adding = this.engine.active?.stage === 'adding' || this.state.state === 'adding' || this.progress?.stage === 'adding';
    const busy = this.running || !!this.engine.active || busyStates.has(this.state.state);
    const blockedAction = ['inspect', 'reopen-profile', 'repair-note-type'].includes(this.state.action);
    const authoritative = blockedAction || ['repairing', 'recovering', 'setup'].includes(this.state.state)
      || !this.running && busyStates.has(this.state.state)
      || this.terminal?.stage === 'adding' && this.state.state === 'failed';
    const display = authoritative ? this.state : this.uncertain
      ? { state: 'failed', message: failureMessages.uncertain } : this.terminal ?? this.state;
    let label = { ready: 'Ready', setup: 'Setup required', recovering: 'Checking previous jobs', repairing: 'Repairing note type',
      generating: 'Generating', validating: 'Validating', adding: 'Adding cards', complete: 'Complete',
      canceled: 'Canceled', failed: 'Failed' }[display.state] ?? 'Ready';
    let message = display.message ?? 'Loading profile…';
    if (this.running) {
      const progress = this.progress;
      label = adding ? 'Adding cards' : progress?.stage === 'validating' ? 'Validating' : 'Generating';
      message = adding ? 'Finishing addition. Cancel is unavailable; use Anki Undo after completion.'
        : progress?.stage === 'starting' ? 'Starting generation…'
          : `${stageNames[progress?.stage] ?? 'Generating'}${progress?.stage !== 'naming' && progress?.cardCount
            ? ` — batch ${progress.batch + 1} of ${Math.ceil(progress.cardCount / 25)}; ${progress.acceptedCount} validated drafts` : ''}`;
    }
    if (this.closed) { label = 'Canceled'; message = 'This profile has closed.'; }
    this.text('state-label', label);
    this.text('message', message);
    const provider = this.engine.active?.request?.providerId ?? (this.running ? this.state.configuredProvider
      : busy ? this.state.provider ?? this.state.configuredProvider : this.state.configuredProvider ?? this.state.provider);
    this.text('provider', provider ? `Provider: ${providerNames[provider] ?? provider}` : 'Provider: not configured');
    this.get('form-fields').disabled = busy || this.closed;
    this.get('new-deck-fields').hidden = this.get('destination').value !== 'new';
    this.get('generate').disabled = !this.available();
    this.get('settings').disabled = busy || this.closed;
    this.get('refresh-decks').disabled = busy || this.loading || this.closed || !this.state.profileReady;
    this.get('cancel').hidden = !busy;
    this.get('cancel').disabled = adding || this.closed;
    this.get('view-cards').hidden = !display.canView || busy || this.closed;
    this.get('repair-note-type').hidden = this.state.action !== 'repair-note-type';
    this.get('repair-note-type').disabled = busy || this.closed || this.state.state === 'repairing';
    this.get('retry').hidden = busy || display.state !== 'failed' || blockedAction || this.uncertain
      || this.engine.lastFailure?.retryable === false;
    this.get('retry').disabled = !this.available();
    this.text('retained', display.state === 'failed' && !this.uncertain
      ? `${this.engine.retainedCount} validated drafts retained. Retry with unchanged inputs to generate only missing cards.` : '');
    this.text('failure-action', this.uncertain ? failureMessages.uncertain : {
      settings: 'Open Settings to check the provider, key, model, or billing before retrying.',
      retry: 'Check the inputs, then Retry. Changing an input discards retained drafts.',
      'reopen-profile': 'Reopen this profile to check the saved job before retrying.',
      'repair-note-type': 'Create a replacement, then retry. Existing notes and customizations will be preserved.',
    }[this.state.action] ?? '');
    const jobs = this.state.recoveryJobs ?? [];
    const key = JSON.stringify(jobs);
    if (key !== this.recoveryKey) {
      this.recoveryKey = key;
      const rows = jobs.map(requestId => {
        const row = this.document.createElement('p');
        const label = this.document.createElement('span');
        label.textContent = `Job ${requestId} `;
        const buttons = [['View tagged cards', 'inspect'], ['Acknowledge — keep cards', 'acknowledge']].map(([text, action]) => {
          const button = this.document.createElement('button'); button.type = 'button'; button.textContent = text;
          button.addEventListener('click', () => this.command(`wayoom:v1:${action}:${requestId}`));
          return button;
        });
        row.append(label, ...buttons); return row;
      });
      this.get('recovery-jobs').replaceChildren(...rows);
    }
  }
}
