"""Locate original read-only test inputs; no bank content is distributed."""

import json
import os
from pathlib import Path
import tempfile

_roaming = os.environ.get("APPDATA")
_default_bank = (Path(_roaming) if _roaming else Path.home() / "AppData" / "Roaming") / "ETS"
BANK = Path(os.environ.get("ETS_TEST_BANK", _default_bank))


def read_record(relative):
    path = BANK / relative
    return path, json.loads(path.read_text(encoding="utf-8-sig"))


def temporary_bank():
    # An explicit workspace parent also works in restricted Windows sandboxes.
    return tempfile.TemporaryDirectory(prefix="ets-test-", dir=Path(__file__).resolve().parent)
