# Testing WayOom

## What these tests establish

The project has local validation, profile-bound job authorization, nonsecret persistence, recoverable Anki insertion, native settings, an SDK-to-Python provider bridge, a generation engine, and its generation form. The sidebar opens a runtime that reconciles saved jobs before permitting generation. Form events are tested through disposable Anki with fake provider replies. A limited Windows offscreen Qt/Chromium smoke also passed; full Anki UI, actual OS stores and live providers remain pending.

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
run `npm run build` before loading a source checkout in Anki. The `.ankiaddon`
includes this generated asset. CI explicitly builds after `npm ci --ignore-scripts`.
The build test loads the bundle in a Node VM with browser globals and a DOM double,
without Node module globals, initializes all five adapters with a required custom
fetch, and exercises panel updates and bridge button commands. This proves bundle
loading under a double. A separate SDK spike exercises the bundled SDK with browser
globals, without Node globals, through the real JSON-line bridge and Python worker.
The in-process provider responses are fixtures, not live model behavior.

The exact AI SDK and provider adapter versions in `package.json` are runtime
dependencies for the generation engine. esbuild is a development dependency
only. Structured generation uses `generateText` with `Output.object`, following
the [current SDK documentation](https://ai-sdk.dev/docs/ai-sdk-core/generating-structured-data).
The SDK boundary requires a supplied fetch and uses only a nonsecret placeholder;
native connection tests and the generation engine call through the bridge.

## Implementation entry points

- `web/validation.mjs`: Zod request schema and request-specific result validation. This is a **local** validator; providers use a separate simple wire schema without local-only transformations.
- `web/form.mjs`, `web/panel.mjs`, `panel.html`: persistent form controls, inline validation, deck refresh, generation/retained Retry, progress, cancellation, native settings, and recovery/repair actions.
- `wayoom/validation.py`: independent validation at the Python boundary.
- `wayoom/cards.py`: discover normal destination decks, provision/verify the owned note type, resolve naming collisions, insert with `col.add_notes()`, and reconcile job tags. Successful commits and recovery retain the stable note-type ID in profile settings.
- `wayoom/jobs.py`: mint unique job IDs, snapshot provider/model references, enforce one active job per profile, authorize attempts and writes, and abort canceled/expired jobs. The budget is three attempts for each of `ceil(cardCount / 25)` fixed internal batches, plus three attempts for AI naming. The proxy consumes an attempt before transport; connection-test permits are single-use. SDK connection tests explicitly use `maxRetries: 0`.
- `web/bridge.mjs`, `web/failures.mjs`, `web/connection-test.mjs`: correlated RPC/custom fetch, shared SDK/proxy failure classification, and the structured connection-test call.
- `web/generation.mjs`, `web/generation-provider.mjs`: sequential generation, in-memory retry cache, local acceptance/repair, cancellation/deadline, and the separately testable prompt/structured-call boundary.
- `wayoom/default_models.json`, `wayoom/models.py`: the single defaults table, documentation sources, and effective-model resolution. Every default is explicitly live-untested; an override is optional.
- `wayoom/bridge.py`, `wayoom/proxy.py`, `wayoom/provider_routes.json`: profile-bound RPC authorization, native-issued test permits, worker-thread HTTPS, generic credential injection/redaction, and route data.
- `wayoom/storage.py`: atomically replace allowlisted profile settings and job records under `user_files`, using hashed profile directory names. Never pass actual credentials into the model/credential-reference fields. Topics and card bodies are omitted from pending-job records.
- `wayoom/runtime.py`: open/close profile sessions, block generation until recovery completes, schedule authorized writes, and open Anki's browser using the saved job tag.
- `wayoom/operations.py`: queue authorized insertion and recovery through `CollectionOp`, bind them to the captured collection, and snapshot input data. Insertion requires a coordinator, profile, and store; it has no unauthenticated legacy entry point.
- `tests/fixtures/validation.json`: shared acceptance/rejection cases for both validators.

Both validators repair AI-generated deck titles such as `Biology::Cells` to `Biology — Cells` before final name validation. Shared fixtures check the repaired output, repeated separators, single-colon preservation, and control-character rejection. User-supplied hierarchy separators remain invalid. The JavaScript naming-result schema accepts a standalone `{deckName}` response. The engine makes a dedicated naming call when needed; the Python coordinator requires and retains its validated result before authorizing card calls and binds that title to insertion.

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
discard that title. The generation engine now orchestrates retained-card retries
under a Python-issued `retryOf` proof and profile-session identity.

Duplicate checks include filtered cards whose home deck is the target and earlier
owned note types after repair. Repair creates a new owned type and updates the saved
reference without changing old notes, templates, or CSS. Nonsecret `ownedNotetypeIds`
retains earlier type IDs so removing a note's tags cannot hide it from this check.
The provider prompt hint includes only fronts carrying a WayOom job tag. Manual
notes in an owned type are still checked locally for duplicates, and an unrelated
note with only a `wayoom` tag is neither checked nor sent as a prompt hint.
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
and an effective model ID from an override or the defaults table. The Qt action issues a single-use permit and asks
the webview SDK to make the call. Unsaved key/model changes must be saved first.
Verification is bound to `verifiedModelId`, so changing a default does not label
an untested model as verified. Native model inputs remain blank for the default
and show its ID in the placeholder, labeled live-untested.
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
The form calls the engine, whose start bridge action calls
this configured entry point and receives the Python snapshot. The existing
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
are ready, destinations, start, cancel-start, progress, fail, discard-retained, job-status,
fetch, abort, test-result, naming-complete, validating, duplicates, and commit.
A connection-test permit can only be minted by the native Qt action.
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

## Generation engine and retained retries

The browser bundle exposes `window.wayoomGeneration`, an instance of
`GenerationEngine`. The form calls `run(request)`, `cancel()`, and
`discard()` on input changes. The engine handles Python owner-ended and
profile-closed events. It validates the request locally before starting, and
Python independently validates the request, selects the active provider/effective
model/credential reference, and mints its job ID. A start token makes replay
idempotent and allows cancellation before the start response arrives; a canceled
token cannot later create an orphaned job.

Naming runs first, only for a blank new-deck name, with at most three attempts.
Cards use sequential original batches of at most 25 and at most three attempts
each. SDK automatic retries are disabled. The provider wire schemas contain only
simple object/array/string shapes. Counts, lengths, Unicode, syntax, strict fields,
and final assembly are enforced locally with the existing Zod validators, then
independently at the Python commit boundary. No validator acceptance rules were
changed in milestone 4.

Each attempt includes the normalized topic, requested missing count, difficulty
learning task, topic-language rule, all accepted fronts, and up to 200 target-deck
fronts. Python's complete-deck/full-fold duplicate check remains authoritative.
Existing normal-deck availability is checked during prefetch, before card calls.
Valid siblings from short/partly invalid responses are retained; the repair asks
only for missing cards. Bounded JSON in an SDK NoObjectGeneratedError may be parsed
to salvage valid siblings, each still subject to strict local validation. Malformed
JSON is never evaluated as code. Excess responses are rejected in full and repaired,
never truncated. Conflicts discovered in an earlier batch consume only that
original batch's remaining attempts, without resetting budgets.

Network, provider, malformed-output, per-call timeout, and size failures may consume
the remaining bounded attempts. Authentication, missing credentials, model, quota,
rate-limit, refusal, redirect, and authorization failures stop immediately. Failures
report a fixed category, stage, retained count, and exhaustion flag without returning
SDK exception text or output bodies. Python receives only allowlisted categories
and bounded stage/progress values. Drafts remain in webview memory only; titles
remain Python-owned. The ten-minute deadline covers generation and validation;
once commit is dispatched, insertion finishes through the authorized atomic path.
Late provider results are ignored even when a test caller ignores AbortSignal.

Unchanged definitive-failure retries get a new ID and a Python `retryOf` proof only
when session, normalized request, provider, model, and credential reference match.
The engine rechecks retained fronts against the full deck and fills only the missing
cards in their original batches. Cancel, profile close, changed inputs (via discard),
or changed settings invalidate reuse. An uncertain commit clears the retry cache
and requires reconciliation. A lost commit response can recover the durable saved
success through job-status without reinserting.

The model IDs and source links live only in
[default_models.json](wayoom/default_models.json). They were selected from current
primary provider docs and remain **untested live**. The engine uses no silent
provider/model fallback. The real model IDs are exercised only against fake replies;
that proves adapter/bridge construction, not provider availability, answer quality,
or live compatibility.

`tests/js/generation.test.mjs` asserts exact calls for 1/100 cards, dedicated and
retained naming, supplied names, malformed/empty/short/excess/oversized output,
invalid siblings, Unicode duplicates, complete-deck conflicts beyond the hint,
batch-spanning avoidance, exhausted budgets, partial retry, input/profile changes,
late start/response cancellation, deadline, terminal errors, and uncertain/durable
commit recovery. Its fake duplicate authority is a test double; separate real Anki
tests establish Python's full Unicode behavior.

`tests/js/generation-integration.test.mjs` uses the built browser-only bundle,
all five real SDK adapters, the actual Python bridge/coordinator/proxy, and a
disposable real Anki backend. GUI scheduling and provider replies are doubled;
socket creation is blocked. Each provider inserts exactly 100 cards with one
naming call and four card calls. Additional scenarios insert one card into a
supplied-name deck, salvage an invalid sibling, and prove that a failure after
25 drafts creates no deck/cards before retry generates only 75 missing cards.
The scenario inserts 603 cards in total, then closes/removes its disposable
collection. JSON-line test pipes explicitly use UTF-8 so names round-trip correctly
on Windows. No real keys, real credential stores, real Qt windows, or real providers
are used.

## Generation form and UI coverage

`window.wayoomPanel` is the production `GenerationPanel` controller. The form has
labeled destination/name/count/topic/difficulty controls, field-specific errors
using `aria-describedby` and `aria-invalid`, and an announced state/stage label.
Generate validates with the shared request schema and synchronously disables
submission before awaiting the engine. Python's independent checks still authorize
every call and write. A whitespace-only name requests AI naming; supplied names
are normalized by the same rules as before. No acceptance rules changed.

The read-only `destinations` RPC uses a profile/collection-guarded QueryOp and
returns normal-deck IDs plus full hierarchical names. Decks load after recovery,
refresh on panel reopening, have a manual Refresh action, and refresh after
success. Stale/out-of-order responses are ignored. A removed selection stays
explicitly unavailable; it never falls back to another deck. Failed refreshes
offer a retry without repeating on each state push. An identical deck list leaves
its option nodes intact. Names, outcomes, status and recovery labels use textContent.

The 500 ms state push does not set input values or rebuild the form. It preserves
raw typing, validation errors, selection ranges and focused nodes. While generating,
fields are disabled to preserve the request snapshot. Stage/draft counts are shown
without invented percentages; Cancel is disabled immediately before commit and
the UI explains finishing/Undo. View cards uses the native saved-job-tag action.
Definitive failures retain the raw form, display safe errors and draft counts, and
offer Retry. Backend destination/commit diagnostics and repair/recovery actions
take priority when needed. Uncertain commits stay blocked across input edits until
profile reconciliation. Repair progress takes priority over its previous error.

Input edits discard drafts even if the user subsequently restores the old value.
Python supplies an opaque configuration token based only on the active provider,
effective model, immutable key reference and availability. Changes invalidate
drafts without putting a key/reference in form state. Connection-test status or
an unrelated provider's settings do not discard unchanged drafts. Profile closure
invalidates pending UI work; a changed profile epoch resets the form. Hiding the
panel preserves its form and running engine.

`tests/js/form.test.mjs` uses an explicit DOM double for values, events, focus and
node identity. It covers invalid/boundary counts, topic/name errors, difficulty,
deck refresh failure/removal/races, synchronous double-submit prevention, insertion
Cancel lock, safe messages, actions, draft invalidation, profile changes and
uncertain commits. `bundle.test.mjs` still checks real bundled startup and commands.
Those default tests do not establish browser layout, actual tab order or
screen-reader output. The optional Qt smoke below additionally checks a narrow
set of real Chromium focus/rendering behaviors. CSS supplies light/dark colors
and visible focus; full Anki/webview/accessibility checks remain manual gates.

`tests/js/form-integration.test.mjs` dispatches form submit/Retry/Cancel events into
the actual production bundle and its engine, real OpenAI SDK adapter, actual Python
bridge and a disposable real Anki collection. It inserts ten into an existing deck
after a synchronous double submit, creates a one-card supplied-name deck, and proves
a failed 30-card AI-named job adds nothing before Retry reuses 25 drafts and makes
only one five-card call. It inserts 41 cards total, then cleans up. Cancel also
blocks a delayed provider reply. DOM/Qt scheduling, provider replies and native
Browser dispatch are doubled; socket creation is blocked. Real backend rendering,
rollback, Undo and Browser search construction are covered by their separate suites.

## Packaging and upgrade verification

Milestone 6 packages the current implementation, now including milestone 5's form.
This remains a development artifact. Packaging does not close the live-provider,
real Qt, actual credential-store,
educational-quality, dependency-review, or cross-platform CI release gates.

From a checkout with Node 24 and CPython 3.12, install exactly the locked JS
dependencies and run:

```powershell
npm ci --ignore-scripts
.\.venv\Scripts\python.exe scripts/package_addon.py
```

With the intended Python active, `npm run package` is equivalent. The command
always rebuilds `web/dist/panel.js`; it does not download anything. It verifies
all pinned wheel SHA-256 hashes and packages their runtime contents directly,
without trusting an existing `vendor/runtime` directory. End users need none of
the build tools. The output is `dist/wayoom-0.1.0.ankiaddon` and a `.sha256` sidecar;
both are ignored by Git. `--output PATH` selects another artifact destination.

The root manifest uses stable package identity `wayoom`, human version `0.1.0`,
and `min_point_version: 260903`, meaning Anki **26.9.3**. See Anki's
[manifest parser](https://github.com/ankitects/anki/blob/main/qt/aqt/addons.py) and
[version encoding](https://github.com/ankitects/anki/blob/main/pylib/anki/utils.py).
This minimum deliberately matches the tested backend, without asserting that
real Qt or all future versions work. Installing a source checkout under a
different package directory creates a separate installation; it is not upgraded
or migrated automatically.

The archive has no enclosing directory and includes only explicitly listed
production Python modules/JSON, root entry point, manifest, panel HTML, compiled
JS, the user README, dependency notices, and wheel-derived Python runtime files.
It excludes `user_files`, tests, source JS, wheels, build scripts, Node modules,
virtual environments, caches, and source maps. Dependency distribution metadata
and licenses remain included. The SDK's provider-utils tarball omits its license
file; its Apache-2.0 attribution comes from the same Vercel project's `ai/LICENSE`,
with full Apache text included in the generated notices. See vendor/README.md.

Entries are sorted, use fixed 1980 timestamps and 0644 permissions, and use ZIP
STORE to avoid compression-library variation. Project text is normalized to LF;
wheel contents are preserved byte for byte. Repeated local builds are tested for
identical archive bytes, including a dirty disposable checkout with CRLF source
and sentinel files. A corrupt wheel fails without replacing a prior artifact.
The lockfiles and verified wheels are build inputs; build from a clean checkout
with `npm ci`, rather than reusing modified dependencies. Cross-platform identical
hashes have not yet been observed. CI now builds and uploads one archive/checksum
per operating system after its tests pass; remote results remain pending.

`tests/python/test_packaging.py` audits the actual archive, imports shipped
credential dependencies in an isolated interpreter without site-packages, and
simulates Anki's replace-everything-except-`user_files` upgrade policy in a temporary
installation. Two profiles retain byte-identical settings, visibility, owned type
IDs, pending-job records, cleanup journals, model choices, and credential references.
A fresh interpreter imports the installed modules and confirms that a fake external
OS vault still resolves those references, so setup is not required again. Obsolete
code is removed. This proves persistence under the modeled policy, **not** a real
AddonManager/Qt install or actual OS-store access. Session-only keys are intentionally
not persistent (covered separately by credential tests). Use a disposable real Anki
base folder for the outstanding manual install/upgrade check.

The packaged [README](README.md) includes installation, upgrade identity, privacy,
platform limits, and uninstall guidance. Before uninstalling, use **Settings →
Remove all keys — every profile** and wait for successful deletion. Uninstalling
alone can leave OS-store keys. If removal fails or the add-on has already been
deleted, revoke the dedicated keys through the provider's key-management pages.

## Acceptance-criteria verification map

“Automated” below names the tested boundary, not certification of the complete
criterion. “Manual pending” requires real Anki/Qt or OS stores. “Live pending”
requires real providers or human quality review. Form behavior is covered with
DOM doubles and production event wiring; a backend test alone does not prove
real Qt interaction.
All automated provider calls use fake replies and fake keys.

| Criterion | Current evidence | Remaining verification |
| --- | --- | --- |
| AC-01 First setup | Automated: `test_sidebar.py`, `test_settings_dialog.py` first-open/setup logic with Qt doubles | Manual pending: native popup in real Anki |
| AC-02 Cancel setup | Automated: dialog cancel and no-save logic, sidebar lifecycle | Manual pending: actual dialog and continued Anki use |
| AC-03 Reopen settings | Automated: `test_credentials.py` fake-vault reopen, sidebar setup suppression | Manual pending: real OS vault and profile reopen |
| AC-04 Five providers | Automated: five real adapters → bridge → disposable Anki with fake replies; production form reaches the same engine using OpenAI | Live pending: all five services; manual pending: real form |
| AC-05 Existing-deck count | Automated: production form inserts exactly 10 into a real existing deck; backend tests preserve existing cards/scheduling | Manual pending: real Qt form and review |
| AC-06 Explicit deck name | Automated: form supplied-name creation, backend collision suffix and preservation tests | Manual pending: selection and displayed name in Qt |
| AC-07 AI deck name | Automated: form AI naming/retained retry and SDK-to-Anki fake pipeline | Live pending: topic relevance; manual pending: real form |
| AC-08 Counts 1 and 100 | Automated: form boundary validation, engine exact-call tests and real-backend insertion counts | Manual pending: real boundary entry |
| AC-09 Invalid inputs | Automated: form inline errors/no engine calls, shared validators and engine zero-provider-call assertions | Manual pending: real webview input/accessibility |
| AC-10 Difficulty | Automated: form all three choices and engine difficulty/language prompt assertions | Live pending: NFR-08 sample and ≥80% human rating |
| AC-11 Invalid output repair | Automated: engine malformed/empty/duplicate/short/excess output budgets; fake pipeline zero writes on failure | Live pending: model behavior |
| AC-12 Generation failures | Automated: form preserves fields/actions, shared classification, engine timeout/errors, atomic backend failure checks | Manual pending: real UI errors; live pending: services |
| AC-13 Duplicate submission | Automated: synchronous production form double-submit inserts once; bridge/durable replay does not reinsert | Manual pending: real double-click timing |
| AC-14 Cancel/profile switch | Automated: engine late responses, coordinator/bridge ownership, real-backend canceled write guards | Manual pending: UI cancel/switch; live pending: provider cancellation |
| AC-15 Destination/type changes | Automated: real-backend deleted/filtered destination/type repair; form preserves missing selection, diagnostic and repair actions | Manual pending: user-driven repair in real Qt |
| AC-16 Write failure | Automated: real-backend rollback/compensation preserves pre-existing data | Manual pending: actual error presentation |
| AC-17 One Undo | Automated: real backend Undo/Redo for cards, deck, type and prior scheduling | Manual pending: Anki Undo menu interaction |
| AC-18 Inert content | Automated: real Anki rendering escape tests, bundle DOM text insertion, restricted bridge sender | Manual pending: real webview/card interaction |
| AC-19 Secret exclusion | Automated: fake-vault persistence/log/exception/state checks, proxy scrubbing and packaged-data exclusion | Manual pending: actual stores/platform diagnostics |
| AC-20 Interrupted response | Automated: durable status recovery, restart/tag reconciliation, unresolved evidence blocks writes | Manual pending: actual Anki restart/inspection |
| AC-21 Connection tests | Automated: all five SDK adapters, fake connection outcomes, verification preserved on network failure | Live pending: real outcomes; manual pending: native button |
| AC-22 Repeated topic duplicates | Automated: avoid-list accumulation; full Python casefold/deck checks including filtered cards and >200 hints | Live pending: actual repeated-topic output |
| AC-23 View cards | Automated: runtime saved-tag search and bundle button dispatch | Manual pending: real Browser shows precisely the job's cards |
| AC-24 Retained retry | Automated: engine 25/75 scenario plus actual form Retry retains 25 and calls only for missing five; form edits invalidate cache | Manual pending: real Retry/input interaction |
| AC-25 Panel lifecycle | Automated: `test_sidebar.py` first opening, visibility persistence, hide without cancellation | Manual pending: real Qt docking and menu interaction |
| AC-26 Upgrade | Automated: `test_packaging.py` disposable replacement simulation preserves two profiles and fake OS-key references; installed-module reopen needs no setup | Manual pending: real Anki installation/upgrade and OS store |
| AC-27 Allowlist | Automated: `test_proxy_bridge.py` invalid host/path/redirect refusal before credential delivery | Live pending: real provider routing compatibility |
| AC-28 Call authorization | Automated: coordinator/bridge budgets, stale-owner refusal, original-batch retry limits | Manual pending: real lifecycle timing |
| AC-29 Secret-safe errors | Automated: fake echoed secrets scrubbed from bodies/errors, credential traceback/log checks | Live pending: real error formats; manual pending: platform diagnostics |
| AC-30 Remove all keys | Automated: fake-vault removal across profiles, interrupted-save cleanup, deletion-failure evidence, native-dialog doubles | Manual pending: actual stores on all three OSes |

## Owner verification tools (milestone 7)

These tools prepare the human gates; they do not close them. No live provider
or real OS credential store was exercised during implementation. Do not add live
checks or the optional Qt environment to the default suites.

### Opt-in live provider probe

Build the browser bundle first with `npm run build`, then run from the project
root. Both the Node driver and Python peer require explicit `--live`. Without it,
they fail before reading a key or starting provider traffic. The environment names
are `WAYOOM_OPENAI_API_KEY`, `WAYOOM_ANTHROPIC_API_KEY`, `WAYOOM_GOOGLE_API_KEY`,
`WAYOOM_XAI_API_KEY`, and `WAYOOM_MISTRAL_API_KEY`.

For example, in PowerShell, enter a dedicated key through a masked prompt rather
than pasting a key into a command or file:

```powershell
$env:WAYOOM_OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'Dedicated OpenAI key' -AsSecureString)).Password
try {
  node scripts/verify_provider.mjs --live --provider openai
} finally {
  Remove-Item Env:WAYOOM_OPENAI_API_KEY -ErrorAction SilentlyContinue
}
```

Repeat separately with each provider/environment name. `--model ID` tests an
explicit override; otherwise the existing live-untested defaults table is used.
The tool does not update defaults or mark them verified automatically. It prefers
the project `.venv` Python; `WAYOOM_TEST_PYTHON` can select a compatible interpreter.
See `node scripts/verify_provider.mjs --help` for options.

The driver loads the actual built browser bundle in a VM with no Node globals or
environment credentials. Its SDK uses the normal BridgeClient → Python Bridge →
ProviderProxy HTTPS path. Python alone reads the selected environment key, removes
its environment entry, and uses the existing session-only credential service.
Other WayOom provider-key environment variables are removed from the child
environment. No real OS store is accessed. Collection and nonsecret state live
in a disposable `.test-tmp/anki-test-live-*` folder, cleaned up on exit; no personal
profile is opened. Qt collection-operation scheduling is doubled, while card
insertion uses the real Anki backend.

The probe runs a native-permit SDK connection test, dedicated AI naming plus
three-card structured generation/insertion, then a separate one-card cancellation
probe. A generic HTTPS observer emits only a sent-event after the real request is
written, before reading its response. The driver cancels on that event and checks
the canceled job and unchanged collection count. This establishes local abort/no
insertion; it cannot guarantee upstream cancellation or stop provider billing.
Streaming is not used. A passing probe does not prove real webview behavior, actual
credential stores, every failure category, or educational quality.

**These are billable calls.** A successful ordinary probe normally uses four
calls; existing bounded repairs/transient failures can use up to ten total attempts.
Adding `--quality-output .test-tmp/openai-sample.json` collects nine more five-card
jobs: three public representative topics at each difficulty (45 review cards).
This normally makes 13 calls total, bounded by 37 attempts including the probe.
The file is written exclusively after all nine cases succeed; an existing output
is never overwritten. Failed checks return nonzero. Standard output contains
only model/provider/version and safe status metadata, never prompts, cards, auth
headers or raw exceptions. Requested sample files intentionally contain topics
and generated cards. Keys are never included. An interrupted process may leave
nonsecret temporary files; it never saves the key there.

`verification.test.mjs` exercises the same driver with two socket-free fixtures:
the existing bridge peer and the actual owner peer with only its HTTPS connection
replaced. The latter reads a fake environment key into session storage, checks its
injection, then tests naming, insertion, cancellation and all sample cases. Each
fixture inserts 48 cards (three probe cards plus 45 review cards), then cleans up.
The test explicitly proves that absent opt-in cannot activate a real connection.
No environment setting can enable live traffic in these default tests.

### Human quality review

After explicitly collecting a sample with the live tool, prepare an unrated file:

```powershell
.\.venv\Scripts\python.exe scripts/quality_review.py prepare .test-tmp/openai-sample.json --output .test-tmp/openai-review.json
# A human edits reviewer, ratings, and notes in openai-review.json.
.\.venv\Scripts\python.exe scripts/quality_review.py report .test-tmp/openai-review.json --output .test-tmp/openai-quality-report.json
```

For each card, set strict booleans for `relevance`, `correctness`, `clarity`,
`distinctConcept` (no redundant learning objective in the sample), and
`difficultyMatch`. `true` means the card meets that dimension; `false` means it
does not; `null` remains unreviewed. Check facts against reliable references and
put concerns/references in notes. Compare cards for duplicate concepts, beyond
the deterministic front-text check. Enter a nonblank reviewer identity. The tool
does not invent ratings or use an AI grader.

Judge difficulty against the product definitions: Beginner emphasizes essential
facts and definitions; Intermediate emphasizes relationships and application;
Advanced emphasizes nuanced distinctions and multi-step reasoning with concise
answers. Difficult wording alone is not evidence of an advanced learning task.

The owner-tool sampling policy requires three topics at every difficulty, with
at least five cards per topic, **separately for each provider/model**. This is a
documented conservative choice for NFR-08's otherwise unspecified sample size.
The report requires at least 80% difficulty matches at each level; this is stricter
than aggregating the AC-10 sample into one percentage. It counts concerns in all
five dimensions. Missing ratings/reviewer/coverage or a below-threshold level
returns exit 1; malformed input or an existing output returns exit 2. Outputs are
never overwritten. Input is bounded and validated against the existing card contract.
`releaseApproved` is always false: even a passing difficulty sample leaves factual
concerns and the other release gates for the owner. No real cards have been rated yet.

### Optional offscreen Qt smoke

This is a separate **Windows CPython 3.12 development target**, not an add-on
runtime dependency or default-suite requirement. Its 38 resolved aqt/Qt dependencies
are pinned in `requirements-qt-smoke-windows.txt`; they are aqt 26.9.3's dependency
closure and do not change the project's normal environment. Use a fresh target:

```powershell
.\.venv\Scripts\python.exe -m pip install --target .test-tmp/qt-env -r requirements-qt-smoke-windows.txt
.\.venv\Scripts\python.exe scripts/qt_smoke.py --qt-path .test-tmp/qt-env --output-dir .test-tmp/qt-smoke-evidence
```

The script loads real native settings controls, checks password masking and key
clearing on Cancel using a fake key, then loads the real compiled form in a real
QWebEngineView. It uses an off-the-record browser profile and disposable base,
blocks external browser requests, and does not load the Anki main window or any
personal profile. It validates invalid-count errors, preservation of typed topic
and focus across 20 state pushes, inert text, and saves settled light/dark PNGs.
The light screenshot explicitly forces light colors because the host prefers dark.
It does not disable Chromium's sandbox/web security. `--disable-gpu` is used for
headless rendering. The smoke stays optional; absence/failure is explicit, never
a skipped default-suite success.

Observed on Windows with aqt/Anki 26.9.3 and Qt/PyQt 6.12.0: the smoke passed and
both screenshots were visually inspected. Chromium emitted GPU-context warnings
but completed the checks. Native DLL access required elevated tool execution in
this workspace sandbox. This narrow proof does not close manual docking, actual
AnkiWebView, keyboard traversal/screen-reader, OS-store, Browser/Undo, full upgrade,
or cross-platform gates. For those, use [MANUAL_VERIFICATION.md](MANUAL_VERIFICATION.md).

## Coverage limits and remaining work

These tests do not certify the full software requirements specification. The following are not implemented or covered end to end yet:

- Provider API calls, SDK model compatibility, real credential stores and native onboarding behavior, and live transport retry/cancellation behavior.
- Real-model naming, generation, repair behavior, relevance, and difficulty quality.
- Real provider compatibility, real Qt rendering, accessibility, and user-driven review sessions. The form is wired and tested with DOM doubles and real backend operations; it is not a real Qt UI test.
- Live TLS/authentication/model behavior and cancellation at real providers. Browser compatibility has been checked in a browser-only VM, with DOM/Qt doubles. The optional loopback test exercises local HTTP only. OS credential operations and setup/settings dialog logic still require real-platform verification.
- User-edited CSS, arbitrary note types, other card formats, or AI answer correctness.

The generation UI uses `ApplicationRuntime.start_configured_job()` and `add_cards()` through the guarded bridge. The low-level `insert_basic_cards()` function is used directly only by tests that exclusively own a disposable collection. The panel offers the form, Generate/Retry/Cancel, View cards, native Settings, recovery, and repair controls. All planned implementation milestones are complete; the owner verification tools and checklist are ready, while the named human gates remain open.

The pinned integration target and packaged minimum are Anki **26.9.3**. Real Qt compatibility remains unverified. The GitHub Actions workflow runs these suites and packages artifacts on Windows, Linux, and macOS when the project is placed in a GitHub repository; adding that workflow is not evidence those remote jobs have already passed.

## References

- [Anki collection API](https://github.com/ankitects/anki/blob/main/pylib/anki/collection.py)
- [Anki collection operations](https://github.com/ankitects/anki/blob/main/qt/aqt/operations/__init__.py)
- [Zod schemas](https://zod.dev/api)
