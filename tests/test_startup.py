"""Startup path checks using real Qt windows and empty local directories."""

import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import main as entry
from tests.bank import temporary_bank
from ui.main_window import MainWindow


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def run_startup(self, *arguments):
        windows = []

        def create_window():
            window = MainWindow()
            windows.append(window)
            return window

        poll = QTimer()
        poll.setInterval(10)
        poll.timeout.connect(lambda: self.app.quit() if windows and windows[0]._result is not None else None)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(self.app.quit)
        poll.start()
        deadline.start(1000)
        try:
            # Reuse the real application between tests; the window, event loop,
            # startup timer, and background folder scanner all run normally.
            with patch.object(entry, "QApplication", return_value=self.app), \
                    patch.object(entry, "MainWindow", side_effect=create_window), \
                    patch.object(entry.sys, "argv", ["ETS-Viewer", *map(str, arguments)]):
                self.assertEqual(entry.main(), 0)
            self.assertEqual(len(windows), 1)
            self.assertIsNone(windows[0]._worker)
            return windows[0]._result
        finally:
            poll.stop()
            deadline.stop()
            for window in windows:
                window.close()
            self.app.processEvents()

    def test_startup_automatically_loads_current_users_roaming_ets_folder(self):
        with temporary_bank() as directory:
            folder = Path(directory) / "ETS"
            folder.mkdir()
            with patch.dict(os.environ, {"APPDATA": directory}):
                result = self.run_startup()
            self.assertIsNotNone(result)
            self.assertEqual(result.root, folder.resolve())

    def test_explicit_command_line_folder_takes_priority(self):
        with temporary_bank() as directory:
            roaming = Path(directory) / "ETS"
            roaming.mkdir()
            selected = Path(directory) / "selected"
            selected.mkdir()
            with patch.dict(os.environ, {"APPDATA": directory}):
                result = self.run_startup(selected)
            self.assertIsNotNone(result)
            self.assertEqual(result.root, selected.resolve())

    def test_missing_default_folder_leaves_manual_selection_available(self):
        with temporary_bank() as directory:
            with patch.dict(os.environ, {"APPDATA": directory}):
                self.assertIsNone(self.run_startup())
            self.assertFalse((Path(directory) / "ETS").exists())

    def test_folder_picker_starts_in_current_users_roaming_ets_folder(self):
        window = MainWindow()
        self.addCleanup(window.close)
        with temporary_bank() as directory:
            with patch.dict(os.environ, {"APPDATA": directory}), \
                    patch("ui.main_window.QFileDialog.getExistingDirectory", return_value="") as dialog:
                window._choose_folder()
            self.assertEqual(Path(dialog.call_args.args[2]), Path(directory) / "ETS")

    def test_folder_picker_falls_back_to_current_profile_when_appdata_is_missing_or_empty(self):
        window = MainWindow()
        self.addCleanup(window.close)
        with temporary_bank() as directory:
            for value in (None, ""):
                with self.subTest(appdata=value), patch.dict(os.environ), \
                        patch("ui.main_window.Path.home", return_value=Path(directory)), \
                        patch("ui.main_window.QFileDialog.getExistingDirectory", return_value="") as dialog:
                    if value is None:
                        os.environ.pop("APPDATA", None)
                    else:
                        os.environ["APPDATA"] = value
                    window._choose_folder()
                    self.assertEqual(Path(dialog.call_args.args[2]), Path(directory) / "AppData" / "Roaming" / "ETS")
