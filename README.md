<p align="center">
  <img src="docs/assets/readme-banner.svg" alt="ETS-Viewer — a local question bank viewer" width="100%">
</p>

<p align="center">
  <strong>Browse questions. Find stored answers. Stay offline.</strong><br>
  A focused desktop viewer for your existing ETS question bank.
</p>

<p align="center">
  <samp>Python 3.11+ · PySide6 · Windows · Offline</samp>
</p>

<p align="center">
  <strong>English</strong> · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#features">Features</a> ·
  <a href="#everyday-use">Usage</a> ·
  <a href="#supported-data">Data formats</a> ·
  <a href="#development">Development</a>
</p>

---

ETS-Viewer turns a local ETS bank into a searchable collection of papers, questions, and reference answers. Open a folder, select a question, and read its original content alongside the answers already stored in the bank.

> **Bring your own bank.** This repository includes the viewer only. Question-bank files and answer datasets are not bundled.

## Quick start

You need **Python 3.11 or newer** and a local bank in a [supported format](#supported-data). The commands below use Windows PowerShell.

**1. Open a terminal in the project folder.** Download or clone the repository first.

**2. Install the dependency and launch.**

```powershell
python -m pip install -r requirements.txt
python main.py
```

**3. Choose your bank.** The app opens `%APPDATA%\ETS` automatically when that folder exists. Otherwise, click **Select Question Bank**.

To open a different folder directly:

```powershell
python main.py "path\to\ETS"
```

Replace `path\to\ETS` with your bank's location. If `APPDATA` is unavailable, the default is `AppData\Roaming\ETS` inside the current user's home directory.

## Features

| | What you get |
| --- | --- |
| **Organized browsing** | A two-level sidebar lists papers newest first, then the selected paper's question groups with two-line previews. |
| **Flexible search** | Find papers or questions by ID, keywords, original text, transcripts, options, answers, or source path. Supports Chinese and other Unicode text. |
| **Comfortable reading** | A centered reading column, sticky Qn navigation, secure inline images, and separate question and reference cards. |
| **Adaptive appearance** | System, Light and Dark modes; a resizable sidebar with an icon rail on narrow windows. |
| **Reference answers** | See the first stored response immediately; expand additional references for each sub-question independently. |
| **Paper exports** | Save a complete paper's questions and first stored answers as a Windows-friendly UTF-8 text file. |
| **Local operation** | Browse without accounts, servers, or network requests. Scanning and viewing leave the original bank files unchanged. |

## Everyday use

1. **Open a paper.** The **All Papers** page shows your complete bank overview. Click a paper to list only its questions and open the first visible group. **All Papers** returns to the overview without rescanning and restores your global search.
2. **Find a question.** Search IDs, text, answers, translations, or source paths. Search ignores case and requires all entered words. Global search filters the paper list while overview totals stay unchanged. Inside a paper, search is restricted to its groups. Matching shared material and additional references open automatically. Use the reading toolbar's arrows to visit highlighted matches within the current group; navigation reopens a matching section if you collapsed it. A metadata-only match is identified without a made-up content count.
3. **Read the references.** Each question has a separate primary reference card. All choices use the same neutral style. **Show N more answers** reveals the remaining stored responses together, in source order. Each part expands independently; top and bottom controls collapse the card. Shared passages and group images start collapsed on each visit. Qn buttons scroll within the same document. Resizing, collapsing the sidebar, and changing themes retain the reading session.
4. **Adjust the workspace.** Drag the sidebar divider or use its collapse button. A narrow window uses an icon rail automatically; use Expand when you need the list or search, and widening the window restores your manual preference. Choose **Settings → Appearance → System / Light / Dark**. The preference is saved locally; System follows Windows app appearance changes.
5. **Export a paper.** Click **Export Paper** at the top right of a paper and choose a `.txt` destination. Success shows a brief toast; failures include the destination and actual error.

**Export behavior:** the file includes the **entire selected paper**, including questions hidden by search, and **only the first stored answer per sub-question**. Questions without an answer retain `Answer not found`. Exports use UTF-8 with a BOM, save atomically, and protect original bank files from being overwritten.

The reading header shows the paper, group, type and short date. Hover over the source metadata or date for the full local path and precise modification timestamp. Folder IDs serve as paper names when descriptive titles are absent. **Change Bank** remains at the bottom of the sidebar (a folder icon in the rail).

## Supported data

The viewer recognizes these ETS collector schemas:

| Schema | Displayed content | Answer source |
| --- | --- | --- |
| `collector.choose` | Shared passages, prompts, transcripts, and labeled options | Explicit choice answers, supplemented by matching metadata when available |
| `collector.dialogue` | Dialogue passages and individual prompts | Explicit answers and linked stored reference responses |
| `collector.picture` | Topic, prompt, original local image, and available explanations | Stored reference responses |
| `collector.word` | Word or sentence content, translations, and explanations | No answer field in the inspected records |
| `collector.read` | Reading content, translations, and explanations | No answer field in the inspected records |

**Answers come from the bank.** Missing answers display `Answer not found`. Prompts, reading passages, speech-evaluator fields, and scoring keywords are never used to invent answers.

<details>
<summary><strong>Folder layout and record mapping</strong></summary>

A typical bank contains paper folders with question records and local assets:

```text
ETS/
└── <paper-id>/
    └── content_<question-id>/
        ├── content.json
        ├── content2.json
        ├── info.json
        └── material/
```

- `content.json` is preferred. If it is missing, damaged, or structurally incomplete, the scanner tries `content2.json` and then supported multiple-choice metadata in `info.json`.
- Alternate copies in the same question folder are not counted as additional questions. Separate source folders stay distinct, even when their IDs match.
- Choice metadata joins `stid` and `xth` to `info.stid` and `xt_xh`. An explicit answer in the primary record takes precedence.
- Dialogue references come from the same part's `answer`, `std[].value`, and `ref[].value`; picture references come from `std[].value` and `ref[].value`.
- Leading `ets_thN` markers are removed for display. Original question and reference text is otherwise retained.
- Supported JSON records can also load under other filenames or extensions. Dictionaries, templates, and media assets are excluded from the question list.

</details>

<details>
<summary><strong>Scope and file limits</strong></summary>

- Unknown collector schemas, SQLite/XML stores, archives, encrypted data, and inaccessible files are reported in **scan notes**. Other readable questions can still load.
- Standalone `[choice]` answer caches without question IDs are reported and left unassigned.
- JSON files larger than **32 MiB** are not parsed. Symlink and junction directories are not followed.
- Prompt images must stay inside the selected bank and are limited to **16 MiB** and **40 million pixels**. Source markup cannot load scripts or network resources.
- The app covers question browsing, local images, stored answers, and text export. Audio/video playback, recording, scoring, and study management are outside its scope.
- Scans run in the background. Opening another folder cancels the previous scan; closing the app requests a clean worker shutdown.

</details>

## Troubleshooting

| What you see | What to do |
| --- | --- |
| **No Exams Found** | Use the inline **Change Bank** button to choose supported question records. You can open a bank root, one paper, or an individual `content_<id>` folder. |
| **scan notes / Scan failed** | Click **View details** in the status bar for a scrollable report containing real source paths and explanations. |
| **Answer not found** | The source record has no usable stored answer. The viewer preserves that absence. |
| **Image unavailable** | Check that the original asset exists in the question folder or its `material/` directory and is within the image limits. |
| **Export failed** | Choose a writable destination. For normal use, launch the app from your own terminal; a development sandbox may restrict saving to other folders. Administrator rights are not required. |

## Development

The application uses Python's standard library and PySide6. Parsing, scanning, export, and UI rendering are separated:

```text
ETS-Viewer/
├── main.py              # Application entry point
├── requirements.txt     # PySide6 dependency
├── core/                # Parser, scanner, and answer export
├── ui/                  # Window, safe rendering, and styles
├── tests/               # Unit and integration tests
├── docs/assets/         # README artwork
├── README.md            # English
└── README.zh-CN.md       # 简体中文
```

Run the suite from the project root:

```powershell
python -m unittest discover -v
```

Data-backed tests expect the original reference bank used during development. They default to the current user's ETS folder and skip when the configured bank folder is absent. An arbitrary bank may not match the expected records. No question-bank data is distributed with the tests.

To select the reference bank explicitly:

```powershell
$env:ETS_TEST_BANK = 'path\to\ETS'
python -m unittest discover -v
```

GUI integration tests use Qt's offscreen platform and real event loops. Synthetic navigation, reading, appearance and startup tests run without a private bank. Private data tests keep their existing fixture checks and skip when that bank is absent. No additional test packages are required.
