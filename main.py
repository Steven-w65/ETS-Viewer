"""Application entry point. Python 3.11+; no server or network services."""

import argparse
from pathlib import Path
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QFont, QPalette
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
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#10151e"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#dce5f4"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#171e2a"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#dce5f4"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#263149"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#dce5f4"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#334f7c"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#f3f6fc"))
    app.setPalette(palette)
    window = MainWindow()
    window.show()
    folder = arguments.folder if arguments.folder is not None else default_bank_folder()
    if arguments.folder is not None or folder.is_dir():
        QTimer.singleShot(0, lambda: window.load_folder(folder))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
