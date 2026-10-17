# ETS-Viewer

A small, entirely local desktop application for browsing ETS questions
and their stored answers. It uses Python 3.11+ and PySide6; there are no other
third-party dependencies, accounts, servers, or network requests.

## Run

From the repository root, with Python 3.11 or newer:

```powershell
python -m pip install -r requirements.txt
python main.py
```

For normal use, run this command from your own Windows terminal. A viewer
started inside a development sandbox inherits that sandbox's write
restrictions and may be unable to save to normal folders. Close that copy
and launch it normally; administrator rights are not required.

On startup, the application automatically loads the current Windows user's
`%APPDATA%\ETS` folder when it exists, normally
`C:\Users\<user>\AppData\Roaming\ETS`. The username is never hard-coded.
If `APPDATA` is unavailable, it resolves `AppData\Roaming\ETS` under the
current user's home directory. If the folder is absent, use **Select Folder**
to choose a bank manually. Passing a folder on the command line overrides
the default.

Click **Select Folder** to change banks, then expand an assignment
or exam in the left panel using its arrow or by double-clicking the row.
Select a question underneath it to view all its sub-questions and stored
answers in the right panel. Assignments/exams start collapsed, so questions
are not presented as one flat list.

The groups follow the actual containing bank folders. In the supplied cache,
these are `2674`, `392791`, `581015`, and `586944`. The cache does not store
descriptive assignment/exam titles, so the original folder IDs are shown
without inventing names. Selecting an individual bank or `content_<stid>`
folder still retains the question's assignment/exam grouping.

Each bank row shows its task folder's modification date as `YYYY-MM-DD`.
Selecting a bank or one of its questions also shows **Folder modified** in the
details header. Hover over the bank row or date badge for the complete folder
path and timestamp in local time. Dates are read during the background scan;
an unreadable timestamp displays **Date unavailable** and adds a scan note
while its readable questions remain available.

Type an assignment ID, question ID, or keywords to search. Searching is
insensitive to case, supports Unicode, and matches all entered words across
the original question, transcript, options, answers, and source path.
Matching questions stay under their assignment/exam, and matching groups
expand automatically. Clear the search to return to the collapsed groups.
The bottom bar shows assignment/exam, question-group, and question counts.

You can also open a folder directly:

```powershell
python main.py "D:\path\to\ETS"
```

The window has a dark theme, resizable panes, keyboard tree navigation, and
scrollable question details. An invisible 8 px resize area separates the
assignment panel from the question details. The resize cursor and live
dragging remain available, with no visible bar on hover or while dragging.
Question blocks use slate backgrounds,
passages and translations use blue, answers use green, and original
explanations or missing-answer notices use amber.

Controls use 10 pt text, question/answer content uses 10.5 pt, question titles
use 13 pt, and supporting text uses 9–9.5 pt. Repeated captions, navigation
hints, and answer-count labels are omitted. Expanded references use compact
numbering; multiple-choice answers show the stored answer without repeating
the option text. Source paths remain in the details header.

Bank groups have distinct headings and compact counts; question previews,
source paths, and status text use progressively quieter styling. Tree rows
have one continuous rounded selection across their indentation and content,
with a visible focus outline for keyboard navigation. The selection remains
visible when focus moves to search or answers, and text selection uses the
same colors throughout the interface.

For questions with multiple stored reference answers, the first answer is
always visible. Click **Show all … answers** to expand the remaining original
responses, or **Show fewer answers** to collapse them. Each sub-question has
its own compact green button, with a directional chevron and distinct hover,
pressed, and keyboard-focus states. List selections use a stronger blue fill;
hovered rows use a quieter tint and outline. Resizing either pane preserves
the expansion and reading position; selecting a different question starts
with the first-answer preview.

Select an assignment/exam or one of its questions to show **Export answers**
in the details header. Click it and choose a `.txt` destination to export the
entire selected paper, including questions hidden by the current search.
The text includes question IDs, content, options, and dividing lines between
questions and answers. Only the first stored answer for each sub-question is
exported, even when more references are expanded on screen. Missing answers
remain **Answer not found**; no answers are generated.

The save dialog starts in the current user's `%APPDATA%\ETS` directory and
suggests the task folder's name with Windows-invalid filename characters
replaced. Files use UTF-8 with a BOM for Chinese text support in Windows
editors. A message confirms a successful export or explains an I/O error.
Saving is atomic, and original files inside the loaded bank cannot be
overwritten; previously generated exports can be replaced.

Folder scanning and parsing run on a background
thread; the GUI remains usable during loading. Selecting another folder
cancels the previous scan. Closing during a scan requests interruption and
waits for the worker to finish without blocking the GUI.

## What was found in the supplied bank

Read-only inspection of `C:\Users\steve\AppData\Roaming\ETS` found 679 files
(about 124 MB) across four local bank directories. The question stores are
UTF-8 JSON in `<bank>/content_<stid>/`, with local assets under each
question's `material/` directory. No SQLite or XML question store was found.

The supplied bank contains **43 question groups / 108 questions and
sub-questions**: 60 multiple-choice questions, 32 dialogue parts, four picture
responses, eight word/sentence items, and four reading items. It contains 60
explicit multiple-choice answers and 1,299 stored dialogue/picture reference
responses. Twelve word/reading items have no stored answer and display exactly
**Answer not found**. The application never treats reading text as an answer.

| Observed format | Question content | Stored answers and mapping |
| --- | --- | --- |
| `collector.choose` | `info.st_nr` shared passage, `xtlist[].xt_nr` prompt, `xt_value` transcript or question text, `xxlist[].xx_mc/xx_nr` option label/text | `xtlist[].answer` belongs to the same `info.stid` and `xt_xh`; `xt_analy` is shown only when populated |
| `collector.dialogue` | `info.value` passage and `question[].ask` prompt, identified by `xh` | The same part's explicit `answer`, `std[].value`, and `ref[].value`; every stored reference is retained |
| `collector.picture` | `topic`, `value`, and the original local `image` | `std[].value` / `ref[].value`; original `keypoint` and `analyze` are shown separately |
| `collector.word` | `value`, optional `translate` and `analyze` | No answer field exists in the inspected records |
| `collector.read` | `value`, optional `translate` and `analyze` | No answer field exists in the inspected records |

`content.json` is preferred because it retains original explanation fields.
`content2.json` is a display copy with substituted question numbers, and
`info.json` is template metadata. These copies are not counted as additional
questions. If the primary copy is missing or damaged, the scanner tries the
display copy, then the observed multiple-choice metadata wrapper if available.

Multiple-choice metadata includes an explicit answer table. Its `stid` and
`xth` are joined to `info.stid` and `xt_xh` to supplement missing answers;
neither array order nor sub-question number alone establishes a relationship.
The primary record's explicit answer takes precedence. Different source
directories remain distinct even if they reuse the same IDs.

The leading `ets_thN` template markers are removed for display. Original
question and reference text is otherwise retained, including any pre-existing
replacement characters or errors in the local bank. The `ai` evaluator field
and scoring `keywords` are never used to construct answers. Source paths are
shown with every selected question.

For passage questions, `xt_nr` may contain only a template marker while
`xt_value` contains the complete numbered question. That text is displayed
as the question and used for the list preview. A genuinely absent answer is
handled separately from a damaged or structurally incomplete record: damaged
primary records produce a scan note and allow a complete alternate copy to
load.

## Limits and scan notes

- Seven root `.txt` files contain standalone `[choice]` response caches without
  question IDs. They are reported in **scan notes** and are not assigned to a
  question by guess. The structured dialogue records already contain their
  own explicitly linked reference responses.
- Dictionaries, media, UI templates, and JavaScript example data in `common/`
  are supporting application assets, not question records. They do not appear
  in the question list.
- The scanner checks data contents, so a supported collector JSON document
  can also load under a different data filename/extension. It does not infer
  arbitrary JSON schemas or execute JavaScript.
- Unknown collector types, SQLite/XML schemas, archives, encrypted/proprietary
  files, malformed data, and inaccessible files are reported. There is no
  decryption or access-control bypass. Readable questions continue to load
  when other files fail.
- Source JSON files larger than 32 MiB are reported rather than parsed.
  Symlink/junction directories are not followed. Local prompt images are
  limited to the selected bank, 16 MiB per file and 40 million pixels.
  Missing images show their original filename as unavailable; the viewer
  never substitutes an unrelated image from `common/`.
- This application displays question text, local prompt images, and stored
  answers. Audio/video playback, recording, scoring, and study-management
  functions are outside its scope.

Click **scan notes** in the status bar, then expand the dialog's details to
see affected paths and reasons. When a completed scan finds no supported
exams, scanning stops and a **No exams found** warning dialog shows the
selected folder. Dismiss it with **OK**, then use **Select Folder** to choose
another bank. Search results with no matches do not trigger this warning.
Scanning and viewing never modify the original bank files or create cache
or settings files there. Answer exports are written only to the destination
chosen in the save dialog.

## Project files

```text
ETS-Viewer/
├── main.py
├── requirements.txt
├── core/
│   ├── scanner.py
│   ├── parser.py
│   └── exporter.py
├── ui/
│   ├── main_window.py
│   ├── renderer.py
│   └── style.qss
├── tests/
└── README.md
```

The renderer is separate to keep source markup sanitization and local image
loading out of the window's event-handling code. The GUI uses Qt's
[QThread](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThread.html) and
[QTextBrowser](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QTextBrowser.html)
without an embedded web engine.

## Tests

```powershell
python -m unittest discover -v
```

Tests use real files in the inspected bank, with temporary copies inside the
test directory for corruption and missing-data cases. No question-bank data
or invented question/answer fixtures are distributed with the project. If
your copy of this bank is elsewhere, set its path first:

```powershell
$env:ETS_TEST_BANK = 'D:\path\to\ETS'
python -m unittest discover -v
```

The tests use the original three inspected bank directories for their stable
36-group / 96-question baseline, and separately check the additional `2674`
bank when available. Adding bank folders does not invalidate that baseline.
They skip when the required bank is unavailable. GUI integration tests use Qt's offscreen
platform and actual event loops to check background loading, folder
replacement, closing during a scan, real answer rendering, Unicode search,
local-resource isolation, collapsed assignment/exam grouping, nested question
selection, searching within groups, panel spacing, live splitter dragging
without rebuilding text, reference-answer controls, wheel scrolling through
expanded answers without jumps, image rescaling,
reading-position preservation on resize, continuous tree selection across
focus changes, task-folder modification dates, current-user startup paths,
command-line folder overrides, complete-paper TXT export under a search
filter, first-answer-only output, Windows-safe filenames, Unicode files,
save cancellation, I/O error messages, and protection of original bank files.
They require no additional test packages.
