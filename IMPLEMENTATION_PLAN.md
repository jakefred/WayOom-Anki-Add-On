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
2. **Connection tests run from Python** through the same transport as generation (allowlist, credential injection, redaction), one call per button press, using `ConnectionTestPermit`.
3. **Python classifies failures in one place.** Generation and connection tests share that classification, so SET-06 and GEN-10 cannot drift apart.
4. **Ownership split.** Python owns job IDs, the request snapshot, budgets, the retained deck name, and all checks against the collection. JavaScript owns prompts, batching, the repair loop, and retained cards in memory.
5. **Commit takes a job ID and result only.** The bridge commit path must use Python's snapshot of the request, never a request sent by JavaScript. Rewrite the TESTING.md statement that no bridge command reaches insertion, so it describes this authorized path.
6. **Python is the authority on duplicates during generation.** Add a read-only bridge check that takes candidate fronts and reports conflicts against the batch and the full target deck. The 200-front avoid list is a prompt hint and cannot be the check, because larger decks would pass it and then fail at write time. This also covers the known JS and Python difference in case folding, which otherwise costs the user a finished job at the last step.
7. **No streaming, sequential batches.** The avoid list has to accumulate across batches (GEN-03), and non-streaming keeps the bridge and abort handling simple.
8. **Proxy checks against the job, not the caller.** Beyond the allowlist, the proxy refuses a request whose model differs from the job snapshot, follows no redirects, caps body sizes, and scrubs the key from anything returned, including provider error bodies, which can echo it.
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
- **Spec and doc changes:**
  - TESTING.md now documents the build step, generated asset, SDK boundary, and exact scope of the browser-bundle test. No spec edits yet.
- **Existing tests changed, and why:**
  - Extended the sidebar load test to assert the exported bundle URL and absence of inline scripts after moving existing JavaScript into the bundled entry point. No existing expectations were weakened.
- **Test results actually observed:**
  - Baseline commit `186b426`: 63 JS, 39 Python unit, 47 real-backend integration tests passed.
  - Milestone 0: clean `npm ci --ignore-scripts` passed; bundle build passed; 64 JS, 39 Python unit, 47 real-backend integration tests passed. npm reported no vulnerabilities. All Anki collections were disposable.
  - Windows sandbox ACLs prevented esbuild reading ancestor directories; build/install verification succeeded with elevated tool execution. Git commits also required elevated execution because `.git` is read-only in the sandbox. Used command-scoped `safe.directory`, without changing global Git configuration.
- **Unverified or incomplete:**
  - Milestone 0 complete under test doubles. Milestones 1–7 remain, including all listed core defects. Real Qt/webview, provider requests, credential stores, cross-platform CI, and every human production gate remain open.
