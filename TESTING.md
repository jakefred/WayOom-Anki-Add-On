# Testing WayOom

## What these tests establish

The project has local validation, profile-bound job authorization, nonsecret persistence, recoverable Anki insertion, and sidebar lifecycle modules. The sidebar opens a runtime that reconciles saved jobs before permitting generation. A generation form and AI provider transport are not yet connected.

| Suite | What runs | What it checks |
| --- | --- | --- |
| JavaScript | Node's test runner and real Zod | Strict request/result types, required and unexpected fields, count limits, exact batch size, field lengths, Unicode, naming rules, duplicate fronts, supported provider/difficulty values, unsupported card syntax |
| Python unit | Standard-library unittest | Independent validation, job snapshots and call budgets, concurrent attempt reservations, timeout/cancellation, atomic settings writes, profile isolation, queued writes, runtime recovery gates, sidebar toggling and saved visibility |
| Anki integration | Real `anki` Python/Rust backend in fresh temporary collections | Actual card counts and rendering, existing/subdeck placement, collision-safe new decks, batch/deck/type Undo and Redo, interrupted commits, job-tag reconciliation, rollback and artifact compensation, duplicate checks, unsafe-text escaping, Zod-to-Anki round trip |

The GUI scheduling and sidebar unit tests use explicit Qt/`aqt` test doubles. They do **not** claim to exercise a real Qt window. The backend integration suite does **not** mock card creation, rendering, persistence, or Undo. Fault-injection tests deliberately corrupt one backend request or alter a returned count to test failure handling.

The sidebar tests verify automatic initial opening, Tools-menu toggling, per-profile visibility persistence, preservation of running jobs while hidden, profile-close invalidation, and restriction of panel commands to the current webview. These tests use Qt doubles; actual window visibility, rendering, browser opening, and interaction during review still require real Qt verification.

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
```

On macOS/Linux, use `.venv/bin/python` instead of `.venv\Scripts\python.exe`.

## Run

```powershell
npm test
.\.venv\Scripts\python.exe -m unittest discover -s tests/python -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/integration -v
```

Alternatively, activate the virtual environment and use `npm run test:python` and `npm run test:anki`. The integration suite also needs Node and the installed Zod dependency for the cross-language round trip. Missing dependencies fail the suite rather than silently skip integration coverage.

The tests require no API keys, provider account, open Anki window, or real user profile. They make no AI requests. Integration collections live under `.test-tmp/anki-test-*` and are closed and removed after each test. An interrupted process can leave a temporary directory there; never point these tests at a personal collection.

## Implementation entry points

- `web/validation.mjs`: Zod request schema and request-specific result validation. This is a **local** validator; provider adapters will need a JSON-Schema-compatible wire schema without local-only transformations.
- `wayoom/validation.py`: independent validation at the Python boundary.
- `wayoom/cards.py`: discover normal destination decks, provision/verify the owned note type, resolve naming collisions, insert with `col.add_notes()`, and reconcile job tags. Successful commits and recovery retain the stable note-type ID in profile settings.
- `wayoom/jobs.py`: mint unique job IDs, snapshot provider/model references, enforce one active job per profile, authorize attempts and writes, and abort canceled/expired jobs. The budget is three attempts for each of `ceil(cardCount / 25)` fixed internal batches, plus three attempts for AI naming. Connection-test permits are single-use. The future provider proxy must consume an attempt before every transport call; these tests do not establish a working proxy or SDK retry configuration.
- `wayoom/storage.py`: atomically replace allowlisted profile settings and job records under `user_files`, using hashed profile directory names. Never pass actual credentials into the model/credential-reference fields. Topics and card bodies are omitted from pending-job records.
- `wayoom/runtime.py`: open/close profile sessions, block generation until recovery completes, schedule authorized writes, and open Anki's browser using the saved job tag.
- `wayoom/operations.py`: queue authorized insertion and recovery through `CollectionOp`, bind them to the captured collection, and snapshot input data. Insertion requires a coordinator, profile, and store; it has no unauthenticated legacy entry point.
- `tests/fixtures/validation.json`: shared acceptance/rejection cases for both validators.

Both validators repair AI-generated deck titles such as `Biology::Cells` to `Biology — Cells` before final name validation. Shared fixtures check the repaired output, repeated separators, single-colon preservation, and control-character rejection. User-supplied hierarchy separators remain invalid. The JavaScript naming-result schema also accepts a standalone `{deckName}` response. The Python coordinator requires a validated naming result before authorizing card calls and binds that title to insertion. The dedicated provider call itself is not implemented yet.

Call collection-writing functions only through Anki's serialized operation mechanism. The integration tests call them directly because each test exclusively owns its temporary collection.

`commit_generation()` validates the full result, checks the active job, persists intent, then creates any required deck/type and adds tagged notes within a custom merged Undo action. Tests verify that one Undo removes the deck, newly created note type, and complete batch, and that Redo restores them. Existing artifacts remain intact. Readback failures roll back the operation. Some backend failures clear Anki's undo history; that path compensates by removing only this job's tagged notes and newly created empty artifacts. Do not assume a failed backend write preserves the previous undo history, even though pre-existing cards are preserved.

Saved jobs are reconciled by searching `wayoom::job::<requestId>`. A complete tagged batch returns the existing outcome; an undone or definitively uncommitted ID cannot insert again. Partial tagged batches block retry. Recovery also cleans recorded abandoned empty artifacts and refuses deletion when the new deck has acquired user cards or subdecks, its name has changed, or the new note type has been customized. A persistence failure after collection commit leaves the pending record available for reconciliation. Tests simulate interruption before insertion and after insertion but before the completion record is saved; they do not simulate process termination inside an individual backend call or OS write.

## Coverage limits and remaining work

These tests do not certify the full software requirements specification. The following are not implemented or covered end to end yet:

- Provider API calls, SDK model compatibility, credentials/onboarding, and live transport retry/cancellation behavior.
- The dedicated AI naming call and generation orchestration (local title repair, naming-before-cards authorization, and new-deck insertion are tested).
- A live provider bridge, real Qt rendering, or user-driven review sessions. The panel exposes only cancel and view-cards commands; no model output or raw bridge command may call insertion directly.
- OS credential-store integration, setup/settings dialogs, provider allowlists and redirect handling, SDK browser compatibility, and real provider cancellation. Abort callbacks are tested with doubles, not live HTTP requests.
- User-edited CSS, arbitrary note types, other card formats, or AI answer correctness.

Use `ApplicationRuntime.start_job()` and `add_cards()` when connecting the generation UI. The low-level `insert_basic_cards()` function is used directly only by tests that exclusively own a disposable collection. The panel currently displays job state and exposes Cancel and View cards; it does not yet offer provider setup or a working generation form.

The pinned integration target is Anki **26.9.3**. This does not establish a minimum supported Anki version. The GitHub Actions workflow runs these suites on Windows, Linux, and macOS when the project is placed in a GitHub repository; adding that workflow is not evidence those remote jobs have already passed.

## References

- [Anki collection API](https://github.com/ankitects/anki/blob/main/pylib/anki/collection.py)
- [Anki collection operations](https://github.com/ankitects/anki/blob/main/qt/aqt/operations/__init__.py)
- [Zod schemas](https://zod.dev/api)
