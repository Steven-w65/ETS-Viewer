# ETS-Viewer design

The user's specification authorizes a complete local Python 3.11+ / PySide6
application. Only folder selection, question search/list, and answer viewing
belong in the product. The application files live directly at the repository root.

## Observed bank

Read-only inspection of `C:\Users\steve\AppData\Roaming\ETS` found 617 files,
including 36 `content.json` question groups and matching `content2.json` and
`info.json` copies. There are no SQLite or XML question stores in this bank.

- `collector.choose`: `info.stid` identifies the group; `xtlist[].xt_xh`
  identifies each question. `xt_nr` is the prompt, `xt_value` the transcript,
  `xxlist[].xx_mc/xx_nr` the options, `answer` the explicit correct answer,
  and `xt_analy` the optional explanation. `st_nr` is shared context.
- `collector.dialogue`: `question[].xh/ask` identifies/prompts each part;
  `std[].value` and `ref[].value` are stored reference responses. The `ai`
  field duplicates text for the original speech evaluator and is not used
  as a fallback. `keywords` are scoring hints, not answers.
- `collector.picture`: `topic`, `value`, and local `image` form the prompt;
  `std[].value` holds reference answers; `analyze` and `keypoint` are original
  supplementary text.
- `collector.word` and `collector.read`: display `value`, original
  translations/explanations, and explicitly mark absent answers.
- Multiple-choice `info.json` also stores an answer array keyed by
  `(stid, xth)`. It can supplement absent answers and provide a fallback
  question copy. Other metadata wrappers do not contain complete questions.

Totals: 36 groups, 96 answerable units, 60 explicit choice answers, 1,189
stored reference responses, and nine units without an answer. Seven root
`[choice]` text caches have no question IDs and must not be assigned by guess.
Bundled dictionaries, template instructions, and JavaScript demo data are not
question-bank records.

During development, another source directory (`2674`) appeared with seven
groups using these same observed schemas. Final inspection has 679 files,
43 groups, 108 units, 60 choice answers, 1,299 references and 12 absent
answers. All original 617 files retained their SHA-256 hashes. These totals
describe the inspected input and are never hardcoded into product behavior.
For passage questions, `xt_value` holds the numbered question text when
`xt_nr` is only a marker; preserve and preview that actual text.

## Implementation

Use immutable records in `core/parser.py`, a read-only recursive scanner in
`core/scanner.py`, and one Qt window in `ui/main_window.py`. Keep rendering
separate in `ui/renderer.py` so source markup is sanitized without executing
scripts or loading network resources. Load local prompt images only within
the selected folder. Group sub-questions under their real `stid`.

Scan and parse on a QThread with interruption checks. Search precomputed
Unicode text with a tree model/proxy and a short debounce. The left panel
groups questions under their actual assignment/exam source directories.
Groups start collapsed; searching expands matching groups and retains the
assignment/exam parent of each matching question. Use real folder IDs when
the cached data has no descriptive container titles. Keep the toolbar,
split assignment/question tree and detail browser, and loaded-question status visible. A
load-report dialog explains unreadable/unsupported files; no settings,
history, accounts, generated answers, media player, or network calls.

Prefer `content.json` and fall back to `content2.json` or the known choice
metadata wrapper if damaged/missing. Deduplicate only within each source
directory; retain different local bank copies with their source paths.

## Verification

Use unittest with actual bank files (never invented question fixtures), plus
temporary copies for missing answers, malformed files, and fallback checks.
Verify exact answer-ID joins, all reference responses, Unicode search,
sanitized rendering, responsive background loading, folder replacement,
and safe closure during a scan. Compare source hashes before/after testing.
Run the real GUI and visually inspect a choice and a picture prompt.
