import importlib
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QEventLoop, QObject, QSettings, QTimer, Qt, qInstallMessageHandler
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import (
    QApplication, QDialog, QLineEdit, QMenu, QProgressBar, QPushButton,
    QScrollBar, QTextBrowser, QToolButton, QTreeView, QVBoxLayout,
)

from tests.bank import temporary_bank


REQUIRED_TOKENS = {
    "background", "sidebar", "surface", "surface_hover", "surface_selected",
    "text_primary", "text_secondary", "text_muted", "border", "border_hover",
    "border_focus", "answer_background", "answer_border", "answer_accent",
    "warning", "error", "search_highlight", "search_highlight_active",
}


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        try:
            self.theme = importlib.import_module("ui.theme")
        except ModuleNotFoundError as error:
            if error.name != "ui.theme":
                raise
            self.fail("The centralized theme subsystem has not been implemented")
        self.temporary = temporary_bank()
        self.addCleanup(self.temporary.cleanup)
        self.settings_path = Path(self.temporary.name) / "appearance.ini"
        self.settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        self.owner = QObject()
        self.addCleanup(self._delete_owner)

    def _delete_owner(self):
        self.owner.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def manager(self):
        return self.theme.ThemeManager(parent=self.owner, settings=self.settings)

    def wait_for(self, predicate, timeout=2500):
        if predicate():
            return
        loop = QEventLoop()
        poll = QTimer(loop)
        poll.setInterval(10)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        deadline = QTimer(loop)
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        poll.start()
        deadline.start(timeout)
        loop.exec()
        self.assertTrue(predicate(), "System appearance update was not observed")

    def test_first_launch_follows_windows_app_appearance(self):
        # Windows' application setting must win over an offscreen/Fusion style hint.
        with patch("ui.theme._windows_color_scheme", return_value=Qt.ColorScheme.Dark):
            manager = self.manager()
        self.assertEqual(manager.mode, "System")
        self.assertLess(QColor(manager.colors["background"]).lightnessF(), 0.25)
        self.assertGreater(QColor(manager.colors["text_primary"]).lightnessF(), 0.75)

    def test_selected_modes_are_restored_from_local_settings(self):
        with patch("ui.theme._windows_color_scheme", return_value=Qt.ColorScheme.Light):
            for mode, dark in (("Dark", True), ("Light", False), ("System", False)):
                with self.subTest(mode=mode):
                    manager = self.manager()
                    manager.set_mode(mode)
                    self.settings.sync()
                    restored_settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
                    restored = self.theme.ThemeManager(self.owner, restored_settings)
                    self.assertEqual(restored.mode, mode)
                    self.assertEqual(QColor(restored.colors["background"]).lightnessF() < 0.5, dark)

    def test_corrupt_preference_defaults_to_system(self):
        self.settings.setValue("appearance/mode", "Sepia")
        with patch("ui.theme._windows_color_scheme", return_value=Qt.ColorScheme.Light):
            manager = self.manager()
        self.assertEqual(manager.mode, "System")
        self.assertGreater(QColor(manager.colors["background"]).lightnessF(), 0.9)

    def test_native_color_scheme_signal_updates_system_mode(self):
        with patch("ui.theme._windows_color_scheme", return_value=Qt.ColorScheme.Light):
            manager = self.manager()
        changes = QSignalSpy(manager.theme_changed)
        self.app.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)
        self.assertLess(QColor(manager.colors["background"]).lightnessF(), 0.25)
        self.assertEqual(changes.count(), 1)
        self.assertEqual(changes.at(0)[0], manager.colors)

    def test_manual_choice_survives_native_system_changes(self):
        with patch("ui.theme._windows_color_scheme", return_value=Qt.ColorScheme.Dark):
            manager = self.manager()
            manager.set_mode("Light")
            colors = manager.colors
            changes = QSignalSpy(manager.theme_changed)
            self.app.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)
            self.assertEqual(manager.colors, colors)
            self.assertEqual(changes.count(), 0)
            manager.set_mode("System")
        self.assertEqual(manager.mode, "System")
        self.assertLess(QColor(manager.colors["background"]).lightnessF(), 0.25)
        self.assertEqual(changes.count(), 1)

    def test_windows_poll_detects_a_change_without_native_qt_signal(self):
        system_scheme = [Qt.ColorScheme.Light]
        with patch("ui.theme._windows_color_scheme", side_effect=lambda: system_scheme[0]):
            manager = self.manager()
            changes = QSignalSpy(manager.theme_changed)
            system_scheme[0] = Qt.ColorScheme.Dark
            self.wait_for(lambda: changes.count() > 0)
        self.assertLess(QColor(manager.colors["background"]).lightnessF(), 0.25)
        self.assertEqual(changes.count(), 1)

    def test_invalid_mode_does_not_change_the_current_choice(self):
        manager = self.manager()
        manager.set_mode("Light")
        with self.assertRaises(ValueError):
            manager.set_mode("Sepia")
        self.assertEqual(manager.mode, "Light")
        self.assertEqual(self.settings.value("appearance/mode"), "Light")

    def test_shared_tokens_produce_readable_palettes(self):
        manager = self.manager()
        for mode in ("Light", "Dark"):
            with self.subTest(mode=mode):
                manager.set_mode(mode)
                colors = manager.colors
                self.assertTrue(REQUIRED_TOKENS.issubset(colors))
                self.assertTrue(all(QColor(value).isValid() for value in colors.values()))
                palette = self.theme.palette_for(colors)
                for role, token in (
                    (QPalette.ColorRole.Window, "background"),
                    (QPalette.ColorRole.WindowText, "text_primary"),
                    (QPalette.ColorRole.Base, "surface"),
                    (QPalette.ColorRole.Text, "text_primary"),
                    (QPalette.ColorRole.ButtonText, "text_primary"),
                    (QPalette.ColorRole.Highlight, "surface_selected"),
                    (QPalette.ColorRole.PlaceholderText, "text_muted"),
                ):
                    self.assertEqual(palette.color(role), QColor(colors[token]))
                self.assertNotEqual(palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText),
                                    palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText))

    def test_reader_text_keeps_normal_text_contrast_in_both_modes(self):
        def luminance(color):
            rgb = QColor(color)
            channels = (rgb.redF(), rgb.greenF(), rgb.blueF())
            linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                      for value in channels]
            return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

        manager = self.manager()
        for mode in ("Light", "Dark"):
            manager.set_mode(mode)
            colors = manager.colors
            for foreground, background in (
                ("text_primary", "background"),
                ("text_primary", "surface"),
                ("text_primary", "surface_selected"),
                ("text_primary", "answer_background"),
                ("text_primary", "search_highlight"),
                ("text_primary", "search_highlight_active"),
                ("text_secondary", "background"),
                ("text_muted", "sidebar"),
            ):
                with self.subTest(mode=mode, foreground=foreground, background=background):
                    values = sorted((luminance(colors[foreground]), luminance(colors[background])))
                    self.assertGreaterEqual((values[1] + 0.05) / (values[0] + 0.05), 4.5)

    def test_template_is_resolved_and_styles_native_controls_without_qss_errors(self):
        manager = self.manager()
        dialog = QDialog()
        self.addCleanup(dialog.deleteLater)
        layout = QVBoxLayout(dialog)
        for widget_type in (QPushButton, QToolButton, QLineEdit, QTreeView,
                            QTextBrowser, QScrollBar, QProgressBar):
            layout.addWidget(widget_type(dialog))
        menu = QMenu(dialog)
        menu.addAction("System")
        messages = []
        previous_handler = qInstallMessageHandler(lambda kind, context, message: messages.append(message))
        try:
            for mode in ("Light", "Dark"):
                with self.subTest(mode=mode):
                    manager.set_mode(mode)
                    sheet = self.theme.style_sheet(manager.colors)
                    self.assertFalse(re.search(r"@[a-z_]+@", sheet))
                    dialog.setPalette(self.theme.palette_for(manager.colors))
                    dialog.setStyleSheet(sheet)
                    dialog.ensurePolished()
                    for widget in dialog.findChildren(QPushButton):
                        widget.ensurePolished()
        finally:
            qInstallMessageHandler(previous_handler)
        errors = [message for message in messages if "parse" in message.lower() or "unknown property" in message.lower()]
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
