# WayOom

Anki desktop add-on that generates flashcards with the user's AI provider key and inserts them into a deck. Python handles Anki integration; JavaScript in Anki's webview handles the UI and Zod validation.

## Where things live

Read these on demand. Don't load them in full; search for the section or requirement ID you need.

- `SOFTWARE_REQUIREMENTS_SPECIFICATION.md`: all product behavior, by ID (`GEN-08`, `ARCH-07`, `AC-12`). Source of truth for intended behavior.
- `TESTING.md`: setup, test commands, coverage limits, and what isn't built yet. Read it before running tests or claiming something works end to end.

| Area | Start here |
| --- | --- |
| Anki entry point, sidebar lifecycle | `__init__.py` |
| Embedded UI | `panel.html` |
| Domain package | `wayoom/` |
| JavaScript validation | `web/validation.mjs` |
| Python boundary validation | `wayoom/validation.py` |
| Note type, card insertion | `wayoom/cards.py` |
| Background writes, profile guard | `wayoom/operations.py` |
| Shared JS/Python validation cases | `tests/fixtures/validation.json` |
| Tests | `tests/js/`, `tests/python/`, `tests/integration/` |
| Dependencies, CI | `package.json`, `requirements-test.txt`, `.github/workflows/tests.yml` |

When searching, exclude `.venv/`, `node_modules/`, `.test-tmp/`, and `__pycache__/`.

## Before you change code

- Source shows what exists; the spec shows what's intended. A tested module may not be wired into the UI, so trace its callers before assuming it is.
- Validation rules live in both JavaScript and Python. Change both, and update the shared fixtures.
- For anything touching API keys, provider calls, or the bridge, read `ARCH-02`, `ARCH-07`, `ARCH-08`, and section 7 of the spec first.
- If a requirement changes, update the spec. Put test instructions in `TESTING.md`. Don't copy either into this file.
- If the spec and code disagree, flag it rather than quietly picking one.

## Working alongside Codex

Codex works in this project at the same time and owns `CODEX.md`. Don't edit that file unless the user asks.

Source files are shared, so files can change between your read and your edit. Re-read just before editing, keep changes scoped, and raise conflicts instead of overwriting concurrent work.
