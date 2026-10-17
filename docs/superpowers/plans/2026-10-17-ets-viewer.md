# ETS-Viewer Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Select the local ETS folder, browse/search actual questions, and view their stored answers.

**Architecture:** A strict parser recognizes only the five observed collector schemas. A recursive read-only scanner returns immutable records and scan notes; a background Qt loader feeds a searchable list and a scrollable answer browser.

**Tech Stack:** Python 3.11+, standard library, PySide6.

**Spec:** `docs/superpowers/specs/2026-10-17-ets-viewer-design.md`

## Global Constraints

- No generated/guessed answers or writes to source question-bank files.
- No product features beyond folder selection, search/list, answer viewing, and load error reporting.
- No server/network dependency; Chinese and Unicode must display correctly.
- Unknown/encrypted formats are reported without bypassing access controls.

## Review Focus

- A missing answer must never become the prompt, transcript, scoring keywords, or speech-evaluator text.
- Separate questions with the same sub-question number must not share answers.
- Corruption in one file must preserve other readable questions and allow another copy as fallback.
- Source HTML and images must not load network or out-of-folder content.
- Closing/reloading while a worker runs must not destroy a running QThread or show stale results.

### Task 1: Actual-format extraction and scanning

**Files:** `core/parser.py`, `core/scanner.py`, `tests/test_parser.py`, `tests/test_scanner.py`.

**Interfaces:** `parse_document(data, source, metadata=None) -> Question`; `scan_bank(folder, progress=None, cancelled=None) -> ScanResult`. Records expose original source paths, group IDs, parts, options, answer provenance, and original explanations.

- [x] Write and run failing tests against real choice/dialogue/picture/reading records and temporary damaged copies.
- [x] Implement the five known schemas, explicit `(stid, xth)` metadata joins, alternate-copy fallback, format notes, and interruption.
- [x] Run the core suite and verify the original baseline: 36 groups, 96 units, nine missing answers.

### Task 2: Minimal desktop UI

**Files:** `main.py`, `ui/main_window.py`, `ui/renderer.py`, `ui/style.qss`, `tests/test_ui.py`, `requirements.txt`.

**Interfaces:** `MainWindow.load_folder(Path)` starts a worker; immutable `ScanResult` populates the list; `render_question(Question, root) -> str` renders complete local content.

- [x] Write and run failing integration tests for answer display, search, source resource isolation, and asynchronous loading/closing.
- [x] Implement the toolbar, list/proxy, scrollable details, original images, and status/report.
- [x] Run tests and the application on the supplied folder; inspect real choice and picture views.

### Task 3: Delivery and verification

**Files:** `README.md`, `.gitignore`.

- [x] Document installation, launch commands, observed field mappings, deduplication, and unsupported portions.
- [x] Run the complete unittest suite, compile checks, and a separate code review.
- [x] Verify all original source file hashes are unchanged and leave the real application available to the user.

## Completion evidence

- `python -m unittest discover -v`: 25 tests passed; `compileall` passed.
- Actual runtime: Python 3.14.7, PySide6 6.11.2, Windows with native Chinese font fallback.
- Separate final review identified a decoded UNC-file-URL edge case and
  acceptance of structurally empty primary copies. Both were reproduced by
  failing regression tests and fixed; the full suite passed afterward.
- Native Qt renders of real choice, dialogue, picture, and missing-answer
  records were inspected. The offscreen platform had no system font database;
  the native Windows platform had 340 font families and displayed the text.
- An additional source directory appeared during development: final current
  totals are 43 groups / 108 units / 1,299 references / 12 missing answers.
  Tests retain an explicit original-bank baseline and separately check the
  additional bank, so added directories do not cause false failures.
- Final SHA-256 comparison: all original 617 files unchanged, none missing;
  62 additional source files appeared independently. No bank files are
  included in the repository or written by application/test code.
