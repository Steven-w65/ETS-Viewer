"""Application entry point. Python 3.11+; no server or network services."""

import argparse
from pathlib import Path
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow, default_bank_folder


def main() -> int:
    parser = argparse.ArgumentParser(description="View questions and stored answers in a local ETS bank.")
    parser.add_argument("folder", nargs="?", type=Path,
                        help="Question-bank folder to open (default: the current user's roaming ETS folder)")
    arguments = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("ETS-Viewer")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()
    folder = arguments.folder if arguments.folder is not None else default_bank_folder()
    if arguments.folder is not None or folder.is_dir():
        QTimer.singleShot(0, lambda: window.load_folder(folder))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
