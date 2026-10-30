# WayOom: agent entry point

An Anki desktop add-on for AI-generated flashcards. Python integrates with Anki; JavaScript provides the UI and Zod validation. Read source to establish current implementation status; requirements describe intended behavior.

## Read only what the task needs

- [SOFTWARE_REQUIREMENTS_SPECIFICATION.md](SOFTWARE_REQUIREMENTS_SPECIFICATION.md): product scope, requirements, contracts, acceptance criteria. Local and gitignored; it may be absent in a fresh checkout. Do not recreate or commit it automatically.
- [TESTING.md](TESTING.md): environment setup, test commands, coverage boundaries, and remaining integration work. Consult this before running tests or claiming end-to-end support.
- Search relevant sections rather than loading both documents in full. Keep requirements and testing instructions in those files, not here.

## Source map

| Task | Start here |
| --- | --- |
| Add-on startup, sidebar lifecycle | `__init__.py` |
| Embedded UI | `panel.html` |
| JavaScript request/output validation | `web/validation.mjs` |
| Python boundary validation | `wayoom/validation.py` |
| Note-type provisioning, actual card insertion | `wayoom/cards.py` |
| Background writes, collection/profile guard | `wayoom/operations.py` |
| Job authorization, limits, cancellation | `wayoom/jobs.py` |
| Profile settings and pending-job records | `wayoom/storage.py` |
| Application lifecycle and recovery | `wayoom/runtime.py` |
| Shared validation cases | `tests/fixtures/validation.json` |
| Schema tests | `tests/js/`, `tests/python/test_validation.py` |
| UI lifecycle/scheduling test doubles | `tests/python/test_sidebar.py`, `tests/python/test_operations.py` |
| Real Anki backend tests | `tests/integration/` |
| Dependencies, scripts, CI | `package.json`, `package-lock.json`, `requirements-test.txt`, `.github/workflows/tests.yml` |

The root `__init__.py` is Anki's entry point; `wayoom/__init__.py` is the importable domain package. Trace callers before assuming a tested module is wired into the UI. A browser webview does not supply a Node.js runtime.

## Working alongside Claude Code

- Maintain Codex-specific guidance here. Leave `CLAUDE.md` and other Claude-owned instruction files to Claude Code unless the user explicitly requests edits.
- Source files are shared. Inspect current contents and available diffs before editing; re-read immediately before applying a patch. Preserve unrelated and concurrent changes.
- Keep edits scoped to the requested task. If concurrent work conflicts with yours, surface the conflict rather than replacing it wholesale.
- Update the appropriate existing document when behavior changes. Avoid duplicate specifications, test instructions, progress logs, or test-count snapshots here.

Exclude dependency/cache directories when searching. Use disposable collections for integration tests; never target a personal Anki profile. Report which tests actually ran and which integration boundaries remain unverified.
