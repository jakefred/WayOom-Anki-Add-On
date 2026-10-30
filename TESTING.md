# Testing WayOom

## What these tests establish

The project has local validation, profile-bound job authorization, nonsecret persistence, recoverable Anki insertion, native settings, and an SDK-to-Python provider bridge. The sidebar opens a runtime that reconciles saved jobs before permitting generation. The generation engine and form are not built yet.

| Suite | What runs | What it checks |
| --- | --- | --- |
| JavaScript | Node's test runner and real Zod | Strict request/result types, required and unexpected fields, count limits, exact batch size, field lengths, Unicode, naming rules, duplicate fronts, supported provider/difficulty values, unsupported card syntax |
| Python unit | Standard-library unittest | Independent validation, job snapshots and call budgets, concurrent attempt reservations, timeout/cancellation, atomic settings writes, profile isolation, queued writes, runtime recovery gates, sidebar toggling and saved visibility |
| Anki integration | Real `anki` Python/Rust backend in fresh temporary collections | Actual card counts and rendering, existing/subdeck placement, collision-safe new decks, batch/deck/type Undo and Redo, interrupted commits, job-tag reconciliation, rollback and artifact compensation, duplicate checks, unsafe-text escaping, Zod-to-Anki round trip |

The GUI scheduling and sidebar unit tests use explicit Qt/`aqt` test doubles. They do **not** claim to exercise a real Qt window. The backend integration suite does **not** mock card creation, rendering, persistence, or Undo. Fault-injection tests deliberately corrupt one backend request or alter a returned count to test failure handling.

The sidebar tests verify automatic initial opening, Tools-menu toggling, per-profile visibility persistence, preservation of running jobs while hidden, profile-close invalidation, and restriction of panel commands to the current webview. Recovery inspection and acknowledgment commands accept only saved uncertain job IDs. A repair command schedules guarded note-type replacement. These tests use Qt doubles; actual window visibility, rendering, browser opening, and interaction during review still require real Qt verification.

## Supported card contract

The only supported generated format is a plain-text Basic front/back card. Each item produces exactly one reviewable card through a dedicated two-field, one-template note type. The low-level adapter requires an existing normal destination deck and a provisioned note-type ID; the authorized commit operation handles creation when required.

- `front`: nonblank string, up to 500 Unicode code points after NFC normalization and trimming.
- `back`: nonblank string, up to 2,000 Unicode code points after normalization and trimming.
- No additional card fields, template definitions, IDs, reverse flags, or scheduling data are accepted from the model.
- Cloze syntax, sound/video tokens, LaTeX command markers, invalid Unicode, and control characters are rejected for this initial format.
- HTML-looking text is accepted as literal text and escaped before Anki renders it. It does not become active markup.
- The JavaScript validator detects canonical/case-insensitive duplicates; the authoritative Python boundary additionally uses full Unicode case folding (for example, `Straße` and `STRASSE`).

The length limits and excluded formats are WayOom product rules. Anki itself supports additional formats and does not impose these particular length limits. Schema validity does not establish factual correctness or educational quality.

## Setup

Use standard CPython 3.12 and Node.js 22 or later (CI uses Node 24). Native Anki wheels require a compatible Python/platform; MSYS Python is not a substitute for Windows CPython when running the real Anki suite.

```powershell
# From the project root, with a suitable Python on PATH:
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
npm ci --ignore-scripts
npm run build
python scripts/build_credentials.py
```

On macOS/Linux, use `.venv/bin/python` instead of `.venv\Scripts\python.exe`.

## Run

```powershell
npm test
.\.venv\Scripts\python.exe -m unittest discover -s tests/python -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/integration -v
```

Alternatively, activate the virtual environment and use `npm run test:python` and `npm run test:anki`. The integration suite also needs Node and the installed Zod dependency for the cross-language round trip. Missing dependencies fail the suite rather than silently skip integration coverage.

The default tests require no API keys, provider account, open Anki window, or real user profile, and make no network calls. The SDK spike uses fake credentials in Python and an in-process fake provider with socket creation explicitly blocked. It needs Python on PATH or `WAYOOM_TEST_PYTHON` set; it prefers the local `.venv` interpreter when available. Integration collections live under `.test-tmp/anki-test-*` and are closed and removed after each test. An interrupted process can leave a temporary directory there; never point these tests at a personal collection.

`npm test` also builds the self-contained browser bundle with esbuild. The generated
`web/dist/panel.js` is ignored by Git and served through Anki's add-on web exports;
run `npm run build` before loading a source checkout in Anki. End-user packaging
will include this generated asset. CI explicitly builds after `npm ci --ignore-scripts`.
The build test loads the bundle in a Node VM with browser globals and a DOM double,
without Node module globals, initializes all five adapters with a required custom
fetch, and exercises panel updates and bridge button commands. This proves bundle
loading under a double. A separate SDK spike exercises the bundled SDK with browser
globals, without Node globals, through the real JSON-line bridge and Python worker.
The in-process provider responses are fixtures, not live model behavior.

The exact AI SDK and provider adapter versions in `package.json` are runtime
dependencies for the planned generation engine. esbuild is a development dependency
only. Structured generation will use `generateText` with `Output.object`, following
the [current SDK documentation](https://ai-sdk.dev/docs/ai-sdk-core/generating-structured-data).
The SDK boundary requires a supplied fetch and uses only a nonsecret placeholder;
native connection tests now call through the bridge. Generation orchestration is pending.

## Implementation entry points

- `web/validation.mjs`: Zod request schema and request-specific result validation. This is a **local** validator; provider adapters will need a JSON-Schema-compatible wire schema without local-only transformations.
- `wayoom/validation.py`: independent validation at the Python boundary.
- `wayoom/cards.py`: discover normal destination decks, provision/verify the owned note type, resolve naming collisions, insert with `col.add_notes()`, and reconcile job tags. Successful commits and recovery retain the stable note-type ID in profile settings.
- `wayoom/jobs.py`: mint unique job IDs, snapshot provider/model references, enforce one active job per profile, authorize attempts and writes, and abort canceled/expired jobs. The budget is three attempts for each of `ceil(cardCount / 25)` fixed internal batches, plus three attempts for AI naming. The proxy consumes an attempt before transport; connection-test permits are single-use. SDK connection tests explicitly use `maxRetries: 0`.
- `web/bridge.mjs`, `web/failures.mjs`, `web/connection-test.mjs`: correlated RPC/custom fetch, shared SDK/proxy failure classification, and the structured connection-test call.
- `wayoom/bridge.py`, `wayoom/proxy.py`, `wayoom/provider_routes.json`: profile-bound RPC authorization, native-issued test permits, worker-thread HTTPS, generic credential injection/redaction, and route data.
- `wayoom/storage.py`: atomically replace allowlisted profile settings and job records under `user_files`, using hashed profile directory names. Never pass actual credentials into the model/credential-reference fields. Topics and card bodies are omitted from pending-job records.
- `wayoom/runtime.py`: open/close profile sessions, block generation until recovery completes, schedule authorized writes, and open Anki's browser using the saved job tag.
- `wayoom/operations.py`: queue authorized insertion and recovery through `CollectionOp`, bind them to the captured collection, and snapshot input data. Insertion requires a coordinator, profile, and store; it has no unauthenticated legacy entry point.
- `tests/fixtures/validation.json`: shared acceptance/rejection cases for both validators.

Both validators repair AI-generated deck titles such as `Biology::Cells` to `Biology — Cells` before final name validation. Shared fixtures check the repaired output, repeated separators, single-colon preservation, and control-character rejection. User-supplied hierarchy separators remain invalid. The JavaScript naming-result schema also accepts a standalone `{deckName}` response. The Python coordinator requires a validated naming result before authorizing card calls and binds that title to insertion. The dedicated provider call itself is not implemented yet.

Call collection-writing functions only through Anki's serialized operation mechanism. The integration tests call them directly because each test exclusively owns its temporary collection.

`commit_generation()` validates the full result, checks the active job, persists intent, then creates any required deck/type and adds tagged notes within a custom merged Undo action. Tests verify that one Undo removes the deck, newly created note type, and complete batch, and that Redo restores them. Existing artifacts remain intact. Readback failures roll back the operation. Some backend failures clear Anki's undo history; that path compensates by removing only this job's tagged notes and newly created empty artifacts. Do not assume a failed backend write preserves the previous undo history, even though pre-existing cards are preserved.

Only pending and uncertain jobs are reconciled by searching `wayoom::job::<requestId>`.
A complete tagged batch becomes committed. Committed records are final: moving,
deleting, or undoing their cards does not trigger startup recovery or authorize
reinsertion. A replay returns the historical saved outcome without writing.
Partial unresolved batches remain uncertain and block generation, while recovery
continues for other records. The panel offers View tagged cards and Acknowledge;
acknowledgment persists a terminal record and deletes no collection data. It does
not automatically retry the job. Startup and each new job prune terminal history
to the latest 100 records, preserving every pending or uncertain record. An active
coordinator still rejects old IDs whose historical records have been pruned.

Recovery also cleans recorded abandoned empty artifacts and refuses deletion when
the new deck has acquired user cards or subdecks, its name has changed, or the new
note type has been customized. A persistence failure after collection commit
leaves the pending record available for reconciliation. Tests simulate interruption
before insertion and after insertion but before the completion record is saved;
they do not simulate process termination inside an individual backend call or OS write.

Definitive write failures and synchronous payload rejection mark the active job
Failed and identify a reason and retry/repair action, without requiring a profile
reopen. Pending or uncertain commits still block new jobs. Errors after a durable
completion report the saved success. Validation can return to provider calls within
the original budgets; insertion remains guarded against canceled or stale jobs.
An unchanged failed job retains its AI-generated title for a new ID, with no naming
call. Cancel, profile close, changed inputs, or changed model/credential references
discard that title. Generation and retained-card retry orchestration remain future work.

Duplicate checks include filtered cards whose home deck is the target and earlier
owned note types after repair. Repair creates a new owned type and updates the saved
reference without changing old notes, templates, or CSS. Nonsecret `ownedNotetypeIds`
retains earlier type IDs so removing a note's tags cannot hide it from this check.
Backend tests cover preservation and duplicate rejection; scheduling and the repair
button are tested with Qt/DOM doubles.

## Credentials, setup, and removal

`wayoom/credentials.py` lazily loads specific OS keyring backends. All runtime
dependencies are pinned and vendored as wheels with SHA-256 hashes; the offline
credential build verifies and extracts them. CI runs this build. See
[vendor/README.md](vendor/README.md) for dependency purposes and supported Linux
ABIs. Windows and macOS use native ctypes backends; Linux uses Secret Service.
No configurable plaintext backend is selected. Actual OS-store behavior on each
platform is still a human verification gate.

First-run setup and Settings are native modal Qt dialogs, with masked blank key
inputs, status labels, provider selection, model overrides, remove controls,
billing/privacy/storage disclosures, and provider key-management links. Saving
a key marks it untested. Connection-test buttons require an available saved key
and a saved model ID. Model defaults are assigned in milestone 4; for now a model
must be supplied before testing. The Qt action issues a single-use permit and asks
the webview SDK to make the call. Unsaved key/model changes must be saved first.
Saving changes across several providers is sequential; after a partial failure,
the dialog clears key inputs, refreshes saved status, and reports that some changes
may already have been saved. Cancel discards unsaved input. Only one settings
dialog can be open; profile close rejects it and clears inputs.

Secrets are kept in the OS store or explicitly in session memory, never settings
or webview state. Persisted references are immutable per replacement; old references
never fall back to replacement keys. A nonsecret cleanup journal is written before
OS writes so interrupted saves and failed replacement deletions remain removable.
Session-only keys are discarded when the profile closes, even though the now
unavailable nonsecret reference remains in settings. Setup-required status is
refreshed at profile open, settings changes, and generation start; it does not poll
or prompt the OS store every 500 ms. An unavailable active provider does not
silently switch to another configured provider.

The generation bridge must use `ApplicationRuntime.start_configured_job()` to
snapshot the selected provider/reference from Python. Settings mutations are
blocked while a job is running, preserving its credential choice. Existing core
tests still call the lower-level `start_job()` with explicit nonsecret references.
The generation-start form and its bridge action are not built yet. The existing
commit bridge action accepts only a current job ID and result, and obtains the
request snapshot from Python. It never accepts a JavaScript request for insertion.

Credential tests use a fake vault and disposable files. They cover save, replace,
remove, profile/installation isolation, unavailable stores, explicit session opt-in,
session cleanup, active-provider selection, interrupted saves, deletion failures,
and removal across profiles. They check that secrets do not appear in settings,
cleanup journals, summaries, formatted exceptions, logs, job snapshots, or panel
state. Native dialog tests use Qt doubles for masked fields, cancel/save/removal,
status, and disclosures. An isolated-interpreter import check verifies vendored
backend dependencies without reading or writing any real OS credential store.

Before uninstalling WayOom, open **Settings → Remove all keys — every profile**.
Wait for confirmed success. If removal cannot be confirmed, retry while protected
storage is accessible, or revoke the keys through the provider links in Settings.
Uninstalling the add-on alone may leave keys in the OS credential store.

## Provider bridge and offline SDK proof

RPC uses the `wayoom:v1:rpc:` prefix with `{v,id,op,payload}` envelopes. Responses
are correlated by ID; repeat IDs in the bounded replay window do not send another
call. Only the current WayOom webview can dispatch commands. Supported operations
are ready, fetch, abort, test-result, naming-complete, validating, duplicates, and
commit. A connection-test permit can only be minted by the native Qt action.
Read-only duplicate checks use QueryOp and inspect the complete target deck;
the returned 200-front prompt hint does not limit conflict detection. Real Anki
tests cover full case folding, filtered home decks, old fronts outside the hint,
authorized commits, payload rejection, cancellation, and replay after Undo.

The proxy derives the provider and credential reference from the Python job or
test snapshot. The SDK owns model selection and all provider wire formats, per
the revised plan; Python has no provider-specific request/response logic. The
allowlist is data, not a custom-base-URL setting. Production connections use
stdlib HTTPSConnection with default certificate verification and no redirect or
environment-proxy handling. Only POST requests to allowed paths are accepted,
with no query strings, fragments, userinfo, or custom ports. Headers must contain
the SDK's placeholder; transport-control headers are refused. Generic placeholder
replacement happens on the worker immediately before sending.

Bodies are capped at 1 MiB in each direction, with separate bounded header and
RPC-envelope sizes. JSON sanitization removes credentials from strings and keys,
including Unicode-escaped echoes, without interpreting provider fields. Only a
fixed JSON content-type header is returned. Compressed responses are refused.
Redirects and raw transport exceptions become fixed categories. SDK warnings are
disabled, and UI errors use fixed category messages rather than SDK exception text.

There is at most one pending fetch per owner and four total; the proxy uses four
workers and holds no collection reference. Each fetch reserves its original
batch's attempt before transport. Socket timeouts and bridge polling bound a call
to 60 seconds (or the remaining job time); native tests expire after 30 seconds.
The job still has its original ten-minute deadline. Abort shuts down the matching
socket and rejects JS immediately; canceled/expired owners reject other pending
RPC promises. Auto-reconnect is disabled after connection. Responses arriving
after cancellation are discarded. GUI delivery uses Anki's main-thread dispatcher.

The spike uses the **built browser bundle** and all five real adapters:

| Provider | Observed URL/path | Placeholder auth header |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1/responses` | `authorization: Bearer …` |
| Anthropic | `https://api.anthropic.com/v1/messages` | `x-api-key` |
| Google | `https://generativelanguage.googleapis.com/v1beta/models/<model>:generateContent` | `x-goog-api-key` |
| xAI | `https://api.x.ai/v1/responses` | `authorization: Bearer …` |
| Mistral | `https://api.mistral.ai/v1/chat/completions` | `authorization: Bearer …` |

For each adapter, scripted structured output, a native-issued connection test,
and in-flight SDK cancellation each assert exactly one HTTP attempt. Additional
SDK connection tests distinguish success, authentication, model, rate-limit,
and network outcomes; network failure preserves prior verification. The shared
classifier also has unit coverage for quota, refusal, timeout, malformed output,
and proxy refusals. Python fault tests cover unauthorized requests, exhausted
budgets, replay, redirects, size limits, compression, raw exception suppression,
plain/escaped key echoes, worker dispatch, and cancellation during blocked I/O.

For an additional opt-in **loopback HTTP** proof, run:

```powershell
npm run test:proxy-loopback
```

This reruns the same SDK scenario against a disposable server on `127.0.0.1`, with
fake Python-only credentials. Only the test connection factory maps approved
HTTPS routes to local HTTP; production has no insecure transport switch. It sends
19 fake HTTP requests, with one extra simulated network failure before sending.
It does not test TLS, real provider behavior, Qt, or a real credential store.

## Coverage limits and remaining work

These tests do not certify the full software requirements specification. The following are not implemented or covered end to end yet:

- Provider API calls, SDK model compatibility, real credential stores and native onboarding behavior, and live transport retry/cancellation behavior.
- The dedicated AI naming call and generation orchestration (local title repair, naming-before-cards authorization, and new-deck insertion are tested).
- Real provider compatibility, real Qt rendering, and user-driven review sessions. The commit bridge is authorized and tested; a working generation form is still pending.
- Live TLS/authentication/model behavior and cancellation at real providers. Browser compatibility has been checked in a browser-only VM, with DOM/Qt doubles. The optional loopback test exercises local HTTP only. OS credential operations and setup/settings dialog logic still require real-platform verification.
- User-edited CSS, arbitrary note types, other card formats, or AI answer correctness.

Use `ApplicationRuntime.start_configured_job()` and `add_cards()` when connecting the generation UI. The low-level `insert_basic_cards()` function is used directly only by tests that exclusively own a disposable collection. The panel currently displays job state and offers native Settings, recovery, and repair controls; it does not yet offer a working generation form. Retry guidance is displayed from the runtime's action state; the generation Retry button belongs to that future form.

The pinned integration target is Anki **26.9.3**. This does not establish a minimum supported Anki version. The GitHub Actions workflow runs these suites on Windows, Linux, and macOS when the project is placed in a GitHub repository; adding that workflow is not evidence those remote jobs have already passed.

## References

- [Anki collection API](https://github.com/ankitects/anki/blob/main/pylib/anki/collection.py)
- [Anki collection operations](https://github.com/ankitects/anki/blob/main/qt/aqt/operations/__init__.py)
- [Zod schemas](https://zod.dev/api)
