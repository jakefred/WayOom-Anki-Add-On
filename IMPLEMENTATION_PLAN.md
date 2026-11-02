# WayOom implementation plan

**For:** Codex, working through this with little supervision.
**Goal:** take WayOom from a tested core to a feature-complete release candidate against `SOFTWARE_REQUIREMENTS_SPECIFICATION.md`. The project owner will review and refine everything before production, so aim for complete, honest, and easy to review over polished.

The spec is the source of truth for behavior; look up requirements by ID. This plan gives the order, the decisions already made, and the known problems. It leaves implementation design to you. You will not get answers mid-run: when something isn't covered here, choose the option that is safest for user data and secrets, record it in the Handoff section, and keep going.

## Where things stand

Verified 2026-10-30 on Node 24 and Python 3.12 with `anki` 26.9.3: 63 JS, 39 Python unit, and 47 integration tests pass.

- **Built and tested:** validation in both languages, job authorization and call budgets, nonsecret storage, insertion with Undo and recovery, and the sidebar lifecycle.
- **Not built:** the AI SDK and everything that uses it, the provider proxy, credentials, setup and settings, the generation form, packaging.
- **Built but unwired:** `list_destinations`, `consume_call`, `naming_complete`, `register_abort`, and `ConnectionTestPermit` have no production callers, and `start_job` and `add_cards` are reached only from tests. Trace callers before trusting a module.
- `aqt` is not installed in `.venv`, so no test has touched real Qt or a real webview.

## Decisions already made

1. **API keys never enter the webview.** Setup and settings are a native Qt dialog. This reads SEC-02 and AC-19 strictly, and it conflicts with section 6, which lists the setup dialog under the JavaScript UI. Update that table row. The deck and generation form stays in the webview.
2. **Connection tests run through the AI SDK.** The Qt button asks the webview to make one SDK call over the same bridge as generation, with Python issuing one `ConnectionTestPermit` per press. Python never builds a provider request.
3. **JavaScript classifies failures in one place,** from the SDK's normalized errors plus the proxy's own refusal, timeout, and network results. Generation and connection tests share that classification, so SET-06 and GEN-10 cannot drift apart.
4. **Ownership split.** Python owns job IDs, the request snapshot, budgets, the retained deck name, and all checks against the collection. JavaScript owns prompts, batching, the repair loop, and retained cards in memory.
5. **Commit takes a job ID and result only.** The bridge commit path must use Python's snapshot of the request, never a request sent by JavaScript. Rewrite the TESTING.md statement that no bridge command reaches insertion, so it describes this authorized path.
6. **Python is the authority on duplicates during generation.** Add a read-only bridge check that takes candidate fronts and reports conflicts against the batch and the full target deck. The 200-front avoid list is a prompt hint and cannot be the check, because larger decks would pass it and then fail at write time. This also covers the known JS and Python difference in case folding, which otherwise costs the user a finished job at the last step.
7. **No streaming, sequential batches.** The avoid list has to accumulate across batches (GEN-03), and non-streaming keeps the bridge and abort handling simple.
8. **The proxy is provider-agnostic.** The AI SDK does all provider-specific work: request format, auth header placement, response parsing, structured output. Python has no per-provider logic or branching. It replaces the SDK's placeholder key with the real key wherever the placeholder appears in the headers, and refuses the request if it finds none. The only per-provider data is the host and path allowlist. It also follows no redirects, caps body sizes, and scrubs the key from anything returned, including provider error bodies, which can echo it.
9. **`keyring`, vendored,** with its per-platform dependencies, imported lazily so a failure degrades to session-only storage (SEC-01).
10. **Remove all keys covers every profile** under `user_files`. AC-30 says no key remains, and it is the pre-uninstall step (SEC-09).
11. **No UI framework.** Plain ES modules bundled by esbuild, targeting the Chromium in Anki's Qt. Built output is generated, never hand-edited.
12. **Use the structured-output API the current AI SDK documents.** Check its docs rather than relying on memory. The provider wire schema is separate from the local Zod schema and stays simple enough for every provider; counts and limits are enforced locally.
13. **ARCH-03 is split, and this departs from the spec's order.** You likely have no provider keys, so prove what you can offline, then continue. If the offline work shows the bridge fetch cannot serve a provider, follow the spec's fallback and revise ARCH-02 and ARCH-07. The live proof remains a human gate before release.

## Milestones

Do these in order. Each ends with all suites passing and a commit.

### 0. Baseline and toolchain

The folder is not a git repository. Run `git init`, commit the current state untouched as the baseline, then commit per milestone so the owner can review each one as a diff. The spec is gitignored and CODEX.md says not to commit it, so leave that alone and list your spec edits in Handoff.

Pin the AI SDK, the five provider adapters, and the bundler in the lockfile. Add a build script and web exports so the panel loads the bundle, and add the build to CI. Confirm the existing `npm ci --ignore-scripts` flow still works.

**Done when:** a built bundle loads under test and the original three suites are unchanged and green.

### 1. Fix the existing core

These were reproduced against real Anki except where marked. Write a failing test first for each. Some existing tests encode the current behavior; change those only where this list requires it, and say which in Handoff.

| Problem | Required result |
| --- | --- |
| Deleting or moving one generated card breaks WayOom for that profile. Startup recovery re-checks finished jobs too, the check raises, and `ready` stays false on every later load. | Committed jobs are final. Startup checks only unresolved records. Saved job records don't grow forever. Pending jobs with partial tags stay blocked. |
| An uncertain job blocks generation permanently, with no way out. The spec is silent; NFR-05 requires recovery. | The user can inspect the job's tagged cards and acknowledge it. Acknowledging deletes nothing. |
| Any failed write, including a plain VAL-05 conflict, disables generation until the profile is reopened. | Definitive failures become Failed with a reason and an action (UX-06). Only an uncertain commit blocks. A payload rejected before scheduling must not leave the job stuck in `validating` (found by reading, not reproduced). |
| The duplicate check misses a card that is temporarily in a filtered deck, because it compares the current deck rather than the home deck. A real duplicate was inserted. | Checks include cards whose home deck is the target. Note-type repair must not shrink the set of checked cards. |
| `validating` is one-way, so provider calls are refused afterwards and repair is impossible. | UX-01 can show Validating between batches and repair can continue, while writes stay gated. |
| Retrying an AI-named job can't reuse the retained name. Card calls are refused until naming is done, and naming can't complete without a new call. | A retry with unchanged inputs carries the name forward without a provider call. Canceling or changing an input discards it (UX-06). |
| ANKI-02 has a stop but no repair action (found by reading, not reproduced). | The stop offers a repair action that never overwrites user customizations. |

### 2. Credentials and settings

Build credential storage (OS store, session-only fallback, profile-scoped references) and the Qt setup and settings dialog. Cover first-run behavior, add, replace, test, and remove, active provider, Remove all keys, and the required disclosures and links. The connection test itself lands in milestone 3.

Scope: SET-01 to SET-09, SEC-01 to SEC-04, SEC-07 to SEC-09, SEC-11.

**Done when:** AC-01, 02, 03, 19, and 30 are covered, and a test proves no key reaches settings, logs, or webview state.

### 3. Provider proxy and bridge

Build the versioned bridge protocol, the Python proxy on a worker thread, abort, and the custom `fetch` for the SDK. Every transport call must consume an attempt first. Jobs canceled or timed out from the Python side must reject whatever JavaScript has pending. Test against a local fake provider, never a real one.

Then do the offline half of ARCH-03: run each of the five real SDK adapters through the bridge `fetch` and confirm the URLs, paths, and auth placement, which you also need to fill in the allowlist.

Scope: ARCH-01, 02, 05, 06, 07, 08; SEC-05, 06; SET-06; GEN-10.

**Done when:** AC-21, 27, 28, and 29 pass against the fake, and a redirect, an oversized body, and an echoed key are all covered.

### 4. Generation engine

Build naming, card batches, the repair and replacement loop within each batch's budget, avoid-list assembly, difficulty prompts, cancellation, and the overall timeout. Keep prompts and provider differences behind a small, separately testable boundary (NFR-06). Choose default model IDs from current provider docs, keep them in one table, and mark them untested until a live run.

Scope: GEN-01 to GEN-10, VAL-01 to VAL-08, DECK-05 to DECK-08, FORM-05, ANKI-07.

**Done when:** scripted fake-provider runs cover 1 and 100 cards, malformed, empty, duplicate, short, and oversized output, exhausted budgets, and a late response after cancel, with exact call counts asserted. AC-07, 08, 10 (prompt half), 11, 12, 14, and 22 are covered.

### 5. Panel UI

Build the generation form and wire everything together: deck list with refresh, inline errors, states and stage text, provider shown, cancel, View cards, retry with retained cards, and failure actions. The 500 ms state push must never overwrite what the user is typing (FORM-03). Render all model text as inert text.

Scope: FORM-01 to FORM-04, DECK-01 to DECK-04, DECK-09, UX-01 to UX-06, PANEL-01 to PANEL-03, NFR-01, NFR-04.

**Done when:** AC-05, 06, 09, 13, 15, 16, 17, 18, 20, 23, 24, and 25 are covered at the deepest level available without real Qt, and any gap is named.

### 6. Packaging and documentation

Build a reproducible `.ankiaddon` with manifest, built assets, and vendored dependencies. Set the minimum Anki version to the tested 26.9.3 rather than guessing lower. Confirm settings survive an upgrade (AC-26) and that the package contains no test files, dev tooling, or `user_files`.

Update TESTING.md to match reality and add a table of AC-01 to AC-30 showing how each is verified: automated, manual pending, or live pending. Add the uninstall guidance SEC-09 requires.

### 7. Verification tooling for the owner

Build what the human gates below need, without running it against anything real: an opt-in live provider check reading keys from environment variables, a small NFR-08 quality-review script, and a short manual checklist for real Anki. If you can install `aqt` in a separate dev environment, try an offscreen smoke test with a disposable base folder. Don't add it to the default suites if it is flaky.

## Ground rules

- Never touch a real Anki profile. Use disposable collections and base folders.
- Default test suites make no network calls and need no keys. Keys come only from environment variables in opt-in scripts, and are never written anywhere.
- Don't weaken, skip, or delete a test to get green.
- Validation changes go in both languages and in `tests/fixtures/validation.json`.
- If the spec and code disagree, don't quietly pick one. Follow the decisions above, otherwise pick the safer reading and record it. Spec drift already known and still to resolve in the spec: the 100-character deck-name limit and the rejection of Anki reserved syntax exist in code but not in VAL-02, VAL-06, or DECK-06.
- Source files are shared with other agents. Re-read before editing, keep changes scoped, and don't edit `CLAUDE.md`.
- Add dependencies only when a milestone needs them; pin and justify each one.
- Be accurate about status. "Tested with a double" and "verified end to end" are different claims.

## Human gates before production

You cannot close these. Leave them listed as open, with tooling ready.

- Live ARCH-03 proof and AC-04 for all five providers, including choosing tested default models.
- Real Qt and webview behavior, including visibility, dialogs, dark theme, keyboard use, and Undo through the UI.
- Credential storage on real Windows, macOS, and Linux.
- NFR-08 quality review and the 80% rating in AC-10.
- CI passing on all three operating systems, and the dependency review in SEC-10.

## Handoff

Fill this in as you go, in this section.

- **Decisions made beyond this plan:**
  - Milestone 0: use an IIFE browser bundle targeting Chrome 100, served with Anki's `setWebExports` and `stdHtml(js=...)`. Require an explicit custom fetch when creating an SDK model. Real Qt compatibility remains unverified.
  - Pinned `ai` 7.0.158, OpenAI 4.0.106, Anthropic 4.0.93, Google 4.0.109, xAI 5.0.34, Mistral 4.0.76 for the planned SDK engine; esbuild 0.28.2 is build-only. Versions were resolved from npm. The documented structured API is `generateText` with `Output.object`; no generation is wired yet.
  - Milestone 1: retain the most recent 100 terminal job records at startup/new-job boundaries; never prune unresolved evidence. Committed outcomes are historical and final, including after Undo. Acknowledgment is a terminal `acknowledged` record, changes no cards, and never retries automatically.
  - Repair creates a replacement owned type rather than modifying the old one. Keep nonsecret `ownedNotetypeIds` so earlier types remain checked even when tags are removed. Filtered cards are checked by home deck. Unchanged retries also require identical model/credential references to reuse a title; this is stricter than UX-06's input list.
  - Milestone 2: select keyring's protected Windows/macOS/Secret Service backends explicitly, bypassing configurable third-party backends. A protected-storage failure requires explicit session-only opt-in. Session keys are forgotten on profile close. Settings changes are blocked during active jobs; replacements get immutable references and never substitute a new key for an old reference.
  - Journal nonsecret OS credential references before writing secrets so interrupted saves and failed replacement cleanup remain discoverable by Remove all keys. Deletion failures retain cleanup evidence and never report success. Multi-provider saves are sequential and the dialog reports possible partial success, clears secrets, and reloads saved status on failure.
  - Pinned/vendored keyring 25.7.0 plus jaraco.classes 3.4.0, jaraco.context 6.1.2, jaraco.functools 4.6.0, more-itertools 11.2.1, pywin32-ctypes 0.2.3, SecretStorage 3.5.0, jeepney 0.9.0, cryptography 50.0.2, cffi 2.1.1, and pycparser 3.12. Their backend roles and Linux x86-64/glibc 2.28+/CPython 3.12–3.14 limits are documented in vendor/README.md. Runtime extraction is offline and verifies wheel hashes; packaging must include the extracted output.
  - Milestone 3 follows Claude's revised decisions 2, 3, and 8: SDK connection tests, shared JavaScript classification, and a provider-agnostic Python proxy. Provider/model choices for SDK calls come from Python snapshots; provider-specific wire formats/auth placement stay in the SDK. Python route data is limited to hosts and paths.
  - Use stdlib HTTPSConnection on four dedicated workers, one fetch per owner, 1 MiB request/response caps, 16 KiB headers, no redirects/query strings/custom ports/compression, a 60-second call deadline, and 30-second connection-test permits. Generic JSON redaction also sanitizes escaped secrets inside nested structured-output strings; it does not interpret provider fields. SDK warnings and raw exception reporting are suppressed.
  - Default suites remain socket-free. The bundled SDK spike uses a Python peer and in-process fake provider with socket creation blocked. `npm run test:proxy-loopback` is the explicit opt-in loopback HTTP proof with fake keys; production has no insecure-transport switch. A saved model ID is required for connection tests until milestone 4 adds defaults.
  - Milestone 4: generation uses original batches of at most 25, each with three attempts shared by transport errors, repairs, and replacements; returning to an earlier conflicted batch does not reset its budget. Retain valid siblings, reject excess responses as a whole, and parse only bounded JSON from SDK structured-output failures before applying strict existing Zod schemas. Authentication/model/quota/rate-limit/refusal/authorization/redirect failures stop immediately; transient failures use remaining bounded attempts.
  - Retained cards stay in webview memory; Python supplies `retryOf` and session identity only for unchanged definitive-failure jobs with identical model/credential references. Input edits must call the engine's discard method (form wiring is milestone 5). Cancel/close/uncertain commits clear drafts. Start tokens make delayed-start cancellation safe. Commit responses can be recovered from durable job status, without repeating insertion. Generation's monotonic ten-minute deadline stops before dispatching atomic insertion.
  - Defaults are kept only in wayoom/default_models.json, with current primary documentation links and liveStatus=untested. Blank model overrides use defaults; connection verification is bound to the effective model ID. No new dependencies. Connection-test output allowance is now 1,024 tokens to accommodate the selected models; actual live suitability remains a gate.
  - Milestone 6 (requested explicitly before milestone 5): package the current incomplete implementation; do not imply that the generation form or release gates are complete. Stable package identity is `wayoom`, version 0.1.0, minimum Anki 26.9.3 (`min_point_version` 260903). Packaging always rebuilds the browser bundle and reads checksum-verified wheel contents directly, not the possibly stale extracted runtime. Explicit production file allowlists exclude local state and development files. Fixed ZIP timestamps/permissions, sorted entries, stored content, and LF-normalized project text make repeated local archives byte-identical. No new dependencies or pins.
  - Milestone 5: the persistent plain-DOM form owns inputs; periodic Python state pushes change status/eligibility, never rehydrate inputs from a job. Fields lock during generation, and submission locks synchronously. A read-only, profile-guarded destinations QueryOp loads normal IDs/full names at opening, reopening, manual refresh and success; unavailable selections remain explicit instead of falling back. Input edits discard drafts; an opaque token invalidates them on active provider/model/key-reference/availability changes without reacting to unrelated settings or connection-test status. An uncertain commit stays blocked across edits. Adding-stage progress disables Cancel before commit dispatch; repair progress and backend destination/commit diagnostics take precedence over stale failure UI. No dependencies or validator acceptance rules changed.
  - Milestone 7: live verification is an owner-only, double-opt-in Node/Python harness using the actual browser bundle, SDK, bridge/proxy and a disposable real Anki collection, with synchronous Qt-operation scheduling doubles. Python alone reads an environment key into session storage; no real OS store is accessed. It tests a native-permit connection, dedicated naming, three-card insertion and cancellation after HTTPS send with unchanged collection count. Raw errors are suppressed; requested sample exports contain cards, not secrets. The quality sampling policy is three topics × three levels × five cards per provider/model, with human boolean ratings and at least 80% difficulty matches separately per level. This conservative policy interprets NFR-08's unspecified sample size; other quality concerns and release approval stay human-owned.
  - The optional offscreen development environment is isolated under .test-tmp/qt-env. Its 38 Windows CPython 3.12 dependencies are pinned in requirements-qt-smoke-windows.txt as aqt 26.9.3's resolved dependency closure (Qt/PyQt 6.12.0); no normal/runtime dependency pins changed and none of this environment enters the addon. Real Qt native controls and Chromium were exercised with external requests blocked, fake credentials, no Anki main window, and a disposable base. Browser sandbox/web security stayed enabled. It remains an optional smoke, outside default suites.
- **Spec and doc changes:**
  - TESTING.md now documents the build step, generated asset, SDK boundary, and exact scope of the browser-bundle test. No spec edits yet.
  - TESTING.md now describes final records, bounded history, acknowledgment, definitive failure actions, retained titles, and note-type repair. No spec edits in milestone 1; the native-settings table row and deck-name drift remain for later milestones.
  - Milestone 2: updated the local, gitignored spec's section 6 table to assign credential setup/settings to native Python/Qt and generation inputs to JavaScript. TESTING.md documents credential coverage, limitations, the configured-job entry point, and pre-uninstall removal guidance. Deck-name spec drift remains unresolved.
  - Milestone 3: TESTING.md now documents RPC, the authorized insertion path, complete-deck duplicate checks, proxy limits, measured SDK routes/auth headers, connection-test categories, and both fake-provider test modes. Adopted the approved SDK-first edits already in the plan. No additional spec edits in this milestone; Claude's updated ARCH-03/ARCH-07 text is preserved. Deck-name drift remains open.
  - Milestone 4: updated TESTING.md for generation, retry proof, start cancellation, defaults, and the real-backend pipeline test. Reconciled the known drift in the local/gitignored spec: DECK-06 now states the existing 100-code-point NFC deck-name limit; VAL-02 clarifies existing NFC/code-point/Unicode/control rules; VAL-06 lists the existing unsupported Anki card tokens. No acceptance rules changed, so both validators and shared fixtures remain unchanged. The spec is not committed.
  - Milestone 6: added packaged README with installation, stable upgrade identity, platform limits, privacy, and pre-uninstall key-removal/revocation guidance. TESTING.md now describes packaging/reproducibility, upgrade simulation boundaries, the packaged minimum, and an AC-01 through AC-30 evidence/pending-verification table. Vendor docs record retained Python licenses and the full Apache text used with JS notices; provider-utils omits LICENSE in its pinned tarball, so notices retain the same Vercel project's attribution from ai/LICENSE. No spec edits in this milestone.
  - Milestone 5: updated README with the now-wired form workflow and TESTING.md with persistent input/state behavior, the destinations RPC, form and production-wiring coverage, and updated acceptance evidence. Real Qt, screen readers, tab order, light/dark rendering and live provider checks remain explicitly pending. No spec edits.
  - Milestone 7: added MANUAL_VERIFICATION.md with uncompleted real-Anki/OS/provider/quality/CI checks. TESTING.md documents safe environment-key entry, explicit live invocation/cost bounds, quality export/rating/report commands, pinned optional Qt setup, observed offscreen scope and remaining gates. README now distinguishes the limited successful Qt smoke from full UI certification. No spec edits, live calls, real keys or real OS-store operations.
- **Existing tests changed, and why:**
  - Extended the sidebar load test to assert the exported bundle URL and absence of inline scripts after moving existing JavaScript into the bundled entry point. No existing expectations were weakened.
  - Changed the Undo replay test to expect the historical committed outcome and no reinsertion, rather than rewriting a committed record to undone. Changed the partial-result test to mark the record unresolved before checking recovery; a final committed record must no longer be rechecked. Extended the bundle test for inspection, acknowledgment, repair, inert labels, and focus preservation on repeated state pushes.
  - Milestone 2: sidebar lifecycle tests mock native dialog creation because they already use Qt doubles; separate dialog tests now cover masked inputs and Save/Cancel/removal. Extended the bundle test for the Settings bridge action. No assertions were weakened or tests skipped.
  - Milestone 3: extended bundle startup tests to acknowledge the new ready handshake, sidebar tests for RPC sender/lifecycle guards, native dialog tests for connection-test callbacks, and the job test for replay authorization after the original deadline. New bridge, transport, SDK spike, and real-collection bridge tests were added. No tests skipped, deleted, or weakened.
  - Milestone 4: extended the SDK peer fixture with scripted generation and disposable real Anki operations. Its default socket guard now subclasses socket.socket so Anki's requests/PySocks imports can inspect the class while socket creation remains blocked; pipe encoding is explicitly UTF-8. Added an existing bridge-collection test for unavailable destinations. New engine and generation-control suites cover the new behavior; no old expectations were weakened.
  - Milestone 6: no existing test expectations changed. Added five packaging tests for actual archive contents/licenses, byte-identical rebuilds, dirty/CRLF source exclusion and checksum-failure preservation, shipped dependency imports without site-packages, and two-profile upgrade persistence using installed modules plus a fake OS vault. Credential extraction now exposes the same verified contents to the packager. CI builds and uploads per-OS artifacts after successful suites; those remote jobs have not been observed.
  - Milestone 5: expanded the bundle DOM double to include real form IDs, events and disabled controls. Its cancellation check now clicks during generation, since a genuinely disabled insertion-stage button must send nothing; replay/recovery/repair/inert-label assertions remain. Added form-controller tests and a production form-event → real engine/SDK/Python bridge → disposable Anki test. Extended the test-only peer with runtime state inspection; it remains socket-free. Added Python checks for scheduling/profile guards, eligibility/configuration identity and the real destination list. No tests skipped or deleted.
  - Milestone 7: added four JS owner-tool tests (including two socket-free real-SDK/Anki scenarios) and six Python quality-review tests. The existing peer gained test-only verification controls; a separate fixture injects fake HTTPS into the actual owner Python peer and asserts fake environment-key injection plus removal from its environment. Existing assertions were not weakened, skipped or deleted. The Qt smoke is intentionally absent from default suites.
- **Test results actually observed:**
  - Baseline commit `186b426`: 63 JS, 39 Python unit, 47 real-backend integration tests passed.
  - Milestone 0: clean `npm ci --ignore-scripts` passed; bundle build passed; 64 JS, 39 Python unit, 47 real-backend integration tests passed. npm reported no vulnerabilities. All Anki collections were disposable.
  - Windows sandbox ACLs prevented esbuild reading ancestor directories; build/install verification succeeded with elevated tool execution. Git commits also required elevated execution because `.git` is read-only in the sandbox. Used command-scoped `safe.directory`, without changing global Git configuration.
  - Milestone 1 regression tests were observed failing before fixes for startup finality, partial recovery, filtered-deck duplicates, old-type duplicates after repair, failure retry, retained titles, cancel discarding titles, history pruning, repair/recovery bridge actions, and errors after durable completion.
  - Milestone 1 final verification: browser bundle build and all 64 JS, 49 Python unit, and 53 real-backend integration tests passed. Collection tests used disposable Anki collections; GUI/DOM controls and scheduling used doubles. No provider calls or real profiles were used.
  - Milestone 2 final verification: offline extraction verified all 13 wheel hashes; all 64 JS, 67 Python unit, and 53 disposable real-backend integration tests passed. Fake-vault tests cover AC-01/02/03/19/30 logic and secret exclusion; native controls use Qt doubles. Vendored imports were tested in an isolated interpreter without accessing a real credential store. No provider calls, real keys, or real Anki profiles were used. Real Qt/OS-store verification remains open.
  - Milestone 3 final verification: browser build plus 69 JS, 85 Python unit, and 56 disposable real-Anki integration tests passed; default tests are socket-free. The additional opt-in loopback SDK scenario passed with 19 fake HTTP requests and a simulated pre-send network failure. All five bundled adapters passed structured output, native-issued connection tests, and cancellation with exact one-call assertions. AC-21/27/28/29 behaviors are covered with fakes, including rejected credentials, unavailable models, rate limits, preserved verification on network failure, unauthorized/exhausted requests, redirect/size refusal, and secret-free failures. No real provider, key, OS credential store, Anki profile, or Qt window was used.
  - Milestone 4 final verification: browser build and all 98 JS, 91 Python unit, and 57 real-backend integration tests passed. The socket-free browser-bundle generation test ran all five real SDK adapters through the real Python bridge into a disposable Anki collection, inserting 100 per provider plus one-card/repair/retry scenarios (603 cards, then cleanup). It verified one naming plus four card calls per 100-card job, unchanged collection/decks after partial failure, and an unchanged retry using only three calls for 75 missing cards. Engine tests cover malformed/empty/duplicate/short/excess/oversized output, exhausted budgets, prompt difficulty/language, cancel/timeout/late responses, input changes, and uncertain commit handling. The opt-in loopback HTTP proof also passed. AC-07/08/10 (prompt half)/11/12/14/22 have automated coverage at these stated boundaries; factual quality and real providers remain unverified.
  - Milestone 6 final verification: all 98 JS, 96 Python unit (including five packaging tests), and 57 disposable real-backend integration tests passed on Windows. Offline credential extraction passed. Two full CLI builds, each rebuilding the browser bundle, produced identical 21,119,475-byte archives with SHA-256 `e9077cd3e11c38b8285330f76547bde713ec7fbe6ca4157c787024ec9ca6e9a7`. Actual archive audits, installed Windows dependency imports without site-packages, and simulated upgrade persistence passed. No real provider, key, OS store, Qt install, or real Anki profile was used.
  - Milestone 5 final verification: all 113 JS, 98 Python unit, and 58 disposable real-backend integration tests passed on Windows. Fourteen DOM-controller tests plus the production bundle form-event test cover the UI at the stated boundaries. The latter inserted 41 cards across existing/supplied/AI-named jobs, proved synchronous double-submit prevention, retained 25 drafts and requested only five missing cards on Retry, refreshed the new deck, and canceled a held response without adding cards. Two complete rebuilt UI packages matched SHA-256 `96e1a7bb3312b500a7428a5186061408011c980c8101d9f1b47b7de5a056b465`. One initial reproducibility run correctly failed because I concurrently rebuilt its bundle; rerunning after the build finished passed, without changing that test. AC-05/06/09/13/15/16/17/18/20/23/24/25 have automated evidence in the updated acceptance map, with real Qt/accessibility/native Browser/Undo interactions explicitly pending. No real keys, providers, OS stores, Qt windows or real user profiles were used.
  - Milestone 7 final verification: all 117 JS, 104 Python unit and 58 disposable real-backend integration tests passed. The owner driver was exercised with socket-free fakes, including the actual new Python peer using a fake environment key and injected HTTPS; each probe/sample scenario inserted 48 disposable cards and canceled without an extra insertion. Human-review tests cover unrated data, strict booleans, missing reviewer/coverage, quality concerns and the exact 80% boundary. A separate aqt 26.9.3 environment was installed under .test-tmp; the normal environment still has no aqt. The optional real Windows Qt/PyQt 6.12.0 smoke passed native masking/Cancel plus Chromium validation/focus/inert text, and settled light/dark renders were visually inspected. GPU-context warnings occurred, and native DLL access required elevated tool execution; no browser security bypass was used. Two final development packages matched SHA-256 `40f31e4c37f4fe14d016d512a430317adf4250942e9eb9ab42e924b80c500fce`. No live provider call, real API key, actual OS credential store, personal profile or human quality rating was used.
- **Unverified or incomplete:**
  - Milestones 0–7 implemented; the planned implementation/tooling work is complete. This is ready for owner acceptance testing, not a production-certified release. Default model IDs remain explicitly live-untested. All live ARCH-03/AC-04 checks, full real Anki UI/webview/accessibility, actual OS credential stores, real package installation/upgrade, cross-platform CI/hash comparison, human quality review and dependency review remain open. The limited Windows offscreen Qt smoke does not close those gates. The upgrade test models preservation with a fake vault, not a Qt AddonManager run. Scripted responses cannot establish provider compatibility or the AC-10 quality threshold. The owner tools and manual checklist are ready for the remaining work.
