![WayOom icon](assets/WayOom Korean Brush Calligraphy App Icon.png)
# WayOom

Turn a topic or document into Anki flashcards using your own AI provider.

WayOom adds a sidebar to Anki for creating plain-text question-and-answer cards.
Choose a deck, describe what you want to learn, and generate cards without copying
answers between apps. You can also attach a document and focus on selected pages.

## Features

- Generate 1–100 Basic cards into an existing or new deck, with an optional AI-chosen count.
- Use OpenAI, Anthropic, Google Gemini, xAI, or Mistral with your own API key.
- Attach a PDF, Word document, PowerPoint, or text file.
- Select PDF pages or PowerPoint slides, with local English OCR for scanned PDFs.
- Leave difficulty automatic, or choose Beginner, Intermediate, or Advanced.
- Hide the sidebar from Anki's toolbar and continue reviewing.
- View generated cards in Anki's Browser and undo additions with Anki Undo.

**Preview release:** AI output can contain mistakes. Review generated cards before
studying them. Live-provider compatibility and full Windows, macOS, and Linux
workflows have not yet been verified across all supported configurations.

## Install

Requires **Anki desktop 26.9.3 or later** and an API key from a supported provider.
API usage is billed by the provider; a chat subscription may not include API credits.
No separate OCR installation, Node.js, or terminal service is required.

1. Download the supplied `wayoom-0.1.7.ankiaddon` file.
2. In Anki, choose **Tools → Add-ons → Install from file** and select it.
3. Restart Anki, open WayOom, and choose **Set up now**.
4. Paste your API key and choose the provider if it is not detected.
5. Select **Verify key & load models**, choose a model, then **Test selected model**.
6. Select **Done** after the test succeeds. Testing makes a small, billable request.

The minimum version is the tested Anki backend target; it does not guarantee
compatibility with every later Anki release. WayOom is a desktop add-on, not an
AnkiMobile or AnkiDroid extension.

## Create cards

1. Choose a deck, or select **Create new deck**. Leave its name blank for AI naming.
2. Leave **Set card count** on to enter a number, or turn it off to let the AI choose.
3. Under **Instructions**, describe what you want to learn.
4. Optionally enable **Set difficulty** and choose a level.
5. Select **Generate**, then **View cards** to review the results.

You can cancel before card insertion begins. After insertion, use **Anki Undo**
to undo the addition. If generation fails, **Retry** with unchanged inputs can reuse
validated drafts. Editing an input discards those drafts.

Both **Set card count** and **Set difficulty** reveal their fields when on and let
WayOom decide when off. Turning **Set card count** off collapses the number field;
turning it back on restores your last number. When off, the AI chooses 1–100 cards
based on your instructions and selected document before generating them. This adds
a small, billable planning request. An unchanged
retry keeps the chosen count; changing inputs starts a new decision.

Click the cream WayOom button immediately after **Sync** in Anki's toolbar to hide
or restore the sidebar. **Tools → WayOom** does the same. Visibility is remembered
per profile; hiding preserves your inputs and any running generation.

## Use a document

Attach one **PDF, DOCX, PPTX, TXT, Markdown, CSV, or TSV** file.
For a PDF or PowerPoint, enter a range such as `1-3, 7`, then select
**Read selected content**. A blank range includes all pages or slides, within the
limits below. Numbers follow file order, starting at 1, rather than printed labels.

Check the text preview. Add optional **Instructions** to focus on particular ideas,
or leave them blank to cover the document's main points. PDF pages without
selectable text use local English OCR automatically. **Force English OCR** also
recognizes pages with an existing text layer. Check scans carefully for errors.

| Limit | Maximum |
| --- | --- |
| File size | 30 MB |
| PDF pages or PowerPoint slides in the file | 500 |
| Selected pages or slides per generation | 50 |
| Extracted text | 60,000 characters |

Word and text files use the whole document. Export Word to PDF to select pages;
convert older `.doc` or `.ppt` files first. Word/PowerPoint extraction reads main
text and tables, excluding embedded images, speaker notes, and Word headers or
footnotes. Export image-only slides to PDF for OCR. Handwriting, equations, and
complex layouts may not be recognized reliably.

Larger excerpts use more input tokens and may exceed your selected model's context
limit. Choose fewer pages if necessary. Expand **File formats and limits** in the
generator for a reminder.

## Privacy and API keys

File reading and OCR run on your computer. When you select **Generate**, WayOom
sends your instructions and selected document text, filename, and page/slide
selection to your chosen AI provider. Generated content and fronts of WayOom cards
in the selected deck may also be sent to help avoid duplicates. The original
document and scanned page images are not uploaded by the document reader.

Use a dedicated API key with a low spending limit. Keys use Windows Credential
Manager, macOS Keychain, or Linux Secret Service, with an explicit session-only
option when protected storage is unavailable. Bundled Linux protected storage
targets x86-64, glibc 2.28+, and CPython 3.12–3.14; other Linux configurations can
use session-only keys. Session-only keys must be entered again after closing the
profile. Do not include API keys or private document text in bug reports.

Settings and recovery records are stored per profile in the add-on's `user_files`
directory. Original documents and extracted text are not saved to those records.
Removing an attachment clears it from the form; a generation request may remain
in memory until replaced or the profile closes. Hiding the sidebar keeps it open
in memory.

## Updates and removal

Keep the same installation identity when upgrading, and preserve `user_files` so
settings and saved-key references remain available. Source-folder installations
under a different name are separate installations.

Before uninstalling, open **Settings → Remove all keys — every profile** and wait
for confirmation. Removing keys locally does not revoke them with the provider.
If WayOom is already removed or key removal fails, revoke the dedicated key on
your provider's website:
[OpenAI](https://platform.openai.com/api-keys),
[Anthropic](https://platform.claude.com/settings/keys),
[Google](https://aistudio.google.com/apikey),
[xAI](https://console.x.ai/), or
[Mistral](https://console.mistral.ai/api-keys/).

## Help

If a request fails, check your key, API credits, selected model, and connection in
**Settings**. If document reading fails, try fewer pages, a clearer scan, or a new
PDF export. WayOom currently generates plain-text Basic cards, not cloze cards or
image-based cards.

[Report a problem](https://github.com/jakefred/WayOom-Anki-Add-On/issues) with your
Anki version, operating system, provider/model, and steps to reproduce it. Remove
private information from screenshots and error details.

## License and source

Copyright (C) 2026 WayOom contributors. WayOom's original code is licensed under
the **GNU AGPL version 3 or later**. You may use, modify, and redistribute it under
those terms. It comes **without warranty**. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

Editable source and build instructions are provided with each release as
`wayoom-VERSION-source.zip`; see [SOURCE_CODE.txt](SOURCE_CODE.txt). The source
repository is [WayOom-Anki-Add-On](https://github.com/jakefred/WayOom-Anki-Add-On).

Bundled dependency notices are included in the installed add-on as
`THIRD_PARTY_NOTICES.txt` and Python distribution license files under `vendor/runtime`.
