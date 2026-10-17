"""Recursively identify and read ETS files. Never open source files for writing."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path

from core.parser import Question, metadata_answer_map, parse_document


MAX_DATA_BYTES = 32 * 1024 * 1024
_COPIES = ("content.json", "content2.json", "info.json")
_ASSET_EXTENSIONS = {".mp3", ".wav", ".mp4", ".png", ".jpg", ".jpeg", ".gif",
                     ".css", ".js", ".html", ".htm", ".svg", ".ico", ".woff", ".ttf"}


@dataclass(frozen=True)
class ScanIssue:
    path: Path
    message: str


@dataclass(frozen=True)
class ScanResult:
    root: Path
    questions: tuple[Question, ...]
    issues: tuple[ScanIssue, ...]
    files_scanned: int
    cancelled: bool = False
    folder_modified: dict[Path, datetime | None] = field(default_factory=dict)


def task_folder(source: Path) -> Path:
    """Use the same containing task for grouping and filesystem metadata."""
    folder = source.parent
    return folder.parent if folder.name.startswith("content_") else folder


def _load_json(path: Path):
    if path.stat().st_size > MAX_DATA_BYTES:
        raise ValueError("Data file exceeds the 32 MiB parsing limit")
    raw = path.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    return json.loads(raw.decode(encoding))


def _support_asset(path: Path) -> bool:
    if path.suffix.lower() in _ASSET_EXTENSIONS:
        return True
    if path.name in {"pc_xst_dict.json", "worddict_data.json"}:
        return True
    if any(p.startswith("template_") for p in path.parts) and path.name in {
        "ctrl.json", "ctrl_check.json", "res.json", "info.json"
    }:
        return True
    return path.name == "config" and path.parent.name == "common"


def scan_bank(
    folder: Path,
    progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ScanResult:
    """Load supported question groups and report data that cannot be mapped.

    Traversal does not follow symlinks/junctions. All opens are read-only.
    Cancellation is cooperative, including during recursive enumeration.
    """
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Question bank folder does not exist: {root}")
    issues, questions, files = [], [], []
    folder_modified = {}
    stopped = cancelled or (lambda: False)

    def result(interrupted=False):
        return ScanResult(root, tuple(questions), tuple(issues), len(files),
                          cancelled=interrupted, folder_modified=dict(folder_modified))

    def walk_error(error):
        issues.append(ScanIssue(Path(error.filename or root), f"Cannot read folder: {error.strerror}"))

    for directory, subdirs, names in os.walk(root, followlinks=False, onerror=walk_error):
        if stopped():
            return result(True)
        base = Path(directory)
        safe_dirs = []
        for name in sorted(subdirs):
            child = base / name
            if child.is_symlink() or getattr(child, "is_junction", lambda: False)():
                issues.append(ScanIssue(child, "Linked folder skipped; select its real location explicitly"))
            else:
                safe_dirs.append(name)
        subdirs[:] = safe_dirs
        for name in sorted(names):
            path = base / name
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                issues.append(ScanIssue(path, "Linked file outside the bank skipped"))
                continue
            files.append(path)
    if progress:
        progress(0, len(files))

    groups = {}
    others = []
    for path in files:
        if path.name in _COPIES and not _support_asset(path):
            groups.setdefault(path.parent, {})[path.name] = path
        else:
            others.append(path)

    processed = 0

    def advance(count=1):
        nonlocal processed
        processed += count
        if progress and (processed % 25 == 0 or processed >= len(files)):
            progress(processed, len(files))

    def add(question):
        # Build the search index on the worker, not on the GUI thread.
        _ = question.search_text
        task = task_folder(question.source)
        if task not in folder_modified:
            try:
                folder_modified[task] = datetime.fromtimestamp(task.stat().st_mtime)
            except (OSError, ValueError, OverflowError) as error:
                folder_modified[task] = None
                issues.append(ScanIssue(task, f"Cannot read task folder modification date: {error}"))
        questions.append(question)

    for copies in groups.values():
        if stopped():
            return result(True)
        metadata = None
        metadata_path = copies.get("info.json")
        if metadata_path:
            try:
                metadata = _load_json(metadata_path)
                metadata_answer_map(metadata)
            except (OSError, ValueError, RecursionError, TypeError) as error:
                issues.append(ScanIssue(metadata_path, f"Cannot read metadata: {error}"))
                metadata = None
        for name in _COPIES:
            path = copies.get(name)
            if path is None or (name == "info.json" and metadata is None):
                continue
            try:
                data = metadata if name == "info.json" else _load_json(path)
                add(parse_document(data, path, metadata=metadata))
                break
            except (OSError, ValueError, RecursionError, TypeError) as error:
                issues.append(ScanIssue(path, f"Cannot load this copy: {error}"))
        advance(len(copies))

    for path in others:
        if stopped():
            return result(True)
        try:
            if not _support_asset(path):
                with path.open("rb") as stream:
                    header = stream.read(512)
                stripped = header.lstrip(b"\xef\xbb\xbf \r\n\t")
                if stripped.startswith(b"SQLite format 3\x00"):
                    issues.append(ScanIssue(path, "SQLite detected; this bank's observed JSON adapter cannot map an unknown database schema"))
                elif stripped.startswith(b"<"):
                    issues.append(ScanIssue(path, "XML/text markup detected; no supported question schema was found"))
                elif stripped.startswith(b"[choice]"):
                    issues.append(ScanIssue(path, "Standalone answer cache has no question ID mapping; no answers were assigned"))
                elif (stripped.startswith((b"{", b"[", b"\xff\xfe", b"\xfe\xff"))
                      or path.suffix.lower() == ".json"):
                    add(parse_document(_load_json(path), path))
                else:
                    issues.append(ScanIssue(path, "Unsupported file format; unknown/encrypted data is not decoded or bypassed"))
        except (OSError, ValueError, RecursionError, TypeError) as error:
            issues.append(ScanIssue(path, f"Cannot load file: {error}"))
        advance()
    return result()
