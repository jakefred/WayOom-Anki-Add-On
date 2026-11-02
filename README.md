# WayOom

WayOom generates plain-text Basic flashcards with a provider key you supply.
This is a **development build** with a generation form and native settings.
Live providers, full Anki UI behavior, and actual OS credential stores have not
been verified. A limited offscreen Qt smoke passed; it does not certify those
release gates. Do not treat this package as a production release.

Requires Anki **26.9.3 or later** on desktop. That minimum is the tested backend
target, not a claim that every later Anki version or platform has been verified.
No Node.js, npm, terminal service, or dependency installation is needed by users.

On first opening, choose **Set up now** or **Skip for now** below the WayOom title.
In Settings, paste your API key. Recognizable key formats select the company;
otherwise choose it yourself. Select **Verify key & load models**, choose a text
model from the dropdown, then **Test selected model**. Testing uses a small amount
of billable API tokens. **Done** and generation require a successful model test.
Enter moves focus instead of closing setup. You can reopen Settings at any time.
Connecting saves the key; closing discards only unsaved input. A replacement key
or changed model must be verified again. Model token limits are shown when the
provider supplies them; no prices or limits are guessed.
After connecting, a persistent notice identifies the saved company's key and its
storage location. **Use a different key** opens the replacement field; **Keep saved
key** discards that unsaved replacement. Model-test progress and results appear in
a large banner below the WayOom title.

Open WayOom from Anki's Tools menu. Choose
an existing deck or Create new deck, enter a topic and 1–100 cards, select a
difficulty, then Generate. Leave a new deck's name blank for AI naming.
Cancel is available before addition begins; use Anki Undo after completion.
Cancel shows immediate feedback, discards retained drafts, and ignores late
generation results. It remains responsive while a failed request is being reported.
After a generation failure, Retry with unchanged inputs reuses validated drafts.
Changing any input discards those drafts. View cards opens the completed batch.

For a disposable test installation, use Anki's **Tools → Add-ons → Install from
file**, select `wayoom-0.1.7.ankiaddon`, and restart Anki. The package identity is
`wayoom`; use the same identity for upgrades. A source folder installed under a
different name is a separate installation and will not share settings or keys.

Settings and recovery records are profile-scoped under the add-on's `user_files`.
Anki preserves this directory during upgrades. Do not delete it when upgrading:
it includes nonsecret credential references needed to find and remove saved keys.
Session-only keys must be entered again after closing the profile.

Provider usage may incur charges. Your topic, generated content, and fronts of
WayOom cards in the selected deck may be processed by the selected provider.
Use a dedicated key and set a low spending limit where the provider offers one.
Windows/macOS use the OS credential store; Linux uses Secret Service. Bundled
Linux native dependencies target x86-64, glibc 2.28+, CPython 3.12–3.14. When
protected storage is unavailable, explicitly choose session-only storage.

Before uninstalling, open **WayOom Settings → Remove all keys — every profile**.
Wait for successful removal before deleting the add-on. Uninstalling alone may
leave keys in the OS credential store. If removal fails or the add-on was already
deleted, revoke the dedicated key on the provider's key-management page:
[OpenAI](https://platform.openai.com/api-keys),
[Anthropic](https://platform.claude.com/settings/keys),
[Google](https://aistudio.google.com/apikey),
[xAI](https://console.x.ai/), or
[Mistral](https://console.mistral.ai/api-keys/).
Removing a local key does not revoke it at the provider.

Maintainers: see TESTING.md in the source repository for builds, coverage, and
owner verification commands, and MANUAL_VERIFICATION.md for the remaining gates.
Python dependency licenses are retained under
`vendor/runtime/*/*.dist-info`; bundled JavaScript licenses are included in
`THIRD_PARTY_NOTICES.txt`.
