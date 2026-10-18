"""Public UI behavior with a small, fully synthetic, offline question bank."""

import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QSettings, QTimer, Qt, QUrl
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from core.scanner import ScanIssue, ScanResult
from tests.bank import temporary_bank
from ui.main_window import MainWindow


def wait_for(predicate, timeout=5000):
    if predicate():
        return
    loop = QEventLoop()
    timer = QTimer(loop)
    timer.setInterval(10)
    timer.timeout.connect(lambda: loop.quit() if predicate() else None)
    deadline = QTimer(loop)
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    timer.start()
    deadline.start(timeout)
    loop.exec()
    if not predicate():
        raise AssertionError("Qt operation did not complete")


def write_bank(root):
    records = {
        ("试卷 Alpha", "a1"): {"structure_type": "collector.dialogue", "info": {
            "stid": "a1", "value": "Shared forest 中文 passage", "question": [
                {"xh": "1", "ask": "Describe the forest", "std": [
                    {"value": "First reference about trees"},
                    {"value": "Hidden sapphire reference 中文"},
                    {"value": "Another original reference"}]},
                {"xh": "2", "ask": "Tell a story", "std": [{"value": "Original second answer"}]}]}},
        ("试卷 Alpha", "a2"): {"structure_type": "collector.choose", "info": {
            "stid": "a2", "xtlist": [{"xt_xh": "1", "xt_nr": "Choose the river",
                "answer": "B", "xxlist": [{"xx_mc": "A", "xx_nr": "A hill"},
                                             {"xx_mc": "B", "xx_nr": "A river"}]}]}},
        ("Paper Beta", "b1"): {"structure_type": "collector.word", "info": {
            "stid": "b1", "value": "Remote mountain", "translate": "远山", "analyze": "Original explanation"}},
    }
    for (paper, group), record in records.items():
        folder = root / paper / f"content_{group}"
        folder.mkdir(parents=True)
        (folder / "content.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    for paper, year in (("试卷 Alpha", 2025), ("Paper Beta", 2024)):
        stamp = datetime(year, 1, 2).timestamp()
        os.utime(root / paper, (stamp, stamp))


class NavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temporary = temporary_bank()
        self.root = Path(self.temporary.name)
        write_bank(self.root)
        self.settings = QSettings(str(self.root / "appearance.ini"), QSettings.Format.IniFormat)
        self.window = MainWindow(settings=self.settings)
        self.window.show()
        self.window.load_folder(self.root)
        wait_for(lambda: self.window._worker is None)

    def tearDown(self):
        self.window.close()
        wait_for(lambda: self.window._worker is None)
        self.app.processEvents()
        self.temporary.cleanup()

    def query(self, value):
        self.window.search.setText(value)
        wait_for(lambda: not self.window._search_timer.isActive())

    def enter(self, row=0):
        tree = self.window.question_tree
        index = self.window.proxy.index(row, 0)
        tree.setCurrentIndex(index)
        self.app.processEvents()

    def test_loading_keeps_complete_overview_and_papers_sorted_by_date(self):
        window = self.window
        self.assertEqual(window.proxy.rowCount(), 2)
        self.assertIn("Alpha", window.proxy.index(0, 0).data())
        self.assertEqual(getattr(window, "navigation_level", None), "papers")
        self.assertEqual(window.overview_totals, (2, 3, 4))
        self.assertIn("Ready", window.status_label.text())

    def test_enter_paper_opens_first_question_and_only_its_questions(self):
        self.enter()
        window = self.window
        self.assertIn("Describe the forest", window.browser.toPlainText())
        self.assertEqual(window.navigation_level, "questions")
        self.assertEqual(window.question_tree.rootIndex(), window.proxy.index(0, 0))
        self.assertEqual(window.proxy.rowCount(window.question_tree.rootIndex()), 2)
        self.assertTrue(window.back_button.isVisible())
        self.assertTrue(window.export_button.isVisible())

    def test_global_search_leaves_overview_unchanged_and_back_restores_query(self):
        self.query("sapphire 中文")
        self.assertEqual(self.window.proxy.rowCount(), 1)
        self.assertEqual(getattr(self.window, "navigation_level", None), "papers")
        self.assertEqual(self.window.overview_totals, (2, 3, 4))
        self.enter()
        self.assertEqual(self.window.search.text(), "sapphire 中文")
        self.assertIn("Hidden sapphire reference 中文", self.window.browser.toPlainText())
        self.query("river")
        self.assertEqual(self.window.browser._question.id, "a2")
        QTest.mouseClick(self.window.back_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.search.text(), "sapphire 中文")
        self.assertEqual(self.window.navigation_level, "papers")
        self.assertEqual(self.window.proxy.rowCount(), 1)
        self.assertEqual(self.window.overview_totals, (2, 3, 4))

    def test_paper_metadata_and_multiple_terms_agree_at_both_levels(self):
        self.query("试卷 Alpha RIVER")
        self.assertEqual(self.window.proxy.rowCount(), 1)
        root = self.window.proxy.index(0, 0)
        self.assertEqual(self.window.proxy.rowCount(root), 1)
        self.enter()
        self.assertIn("Choose the river", self.window.browser.toPlainText())
        self.assertGreater(self.window.browser.match_count, 0)
        self.assertEqual(self.window.search.text(), "试卷 Alpha RIVER")

    def test_global_and_paper_no_results_are_inline_with_clear_action(self):
        self.query("absent-never-here")
        self.assertEqual(self.window.proxy.rowCount(), 0)
        self.assertFalse(self.window.findChildren(QMessageBox))
        self.assertEqual(getattr(self.window, "overview_totals", None), (2, 3, 4))
        QTest.mouseClick(self.window.clear_search_button, Qt.MouseButton.LeftButton)
        self.enter()
        self.query("absent-never-here")
        self.assertTrue(self.window.empty_page.isVisible())
        self.assertIn("No matching", self.window.empty_title.text())
        self.assertTrue(self.window.export_button.isVisible(), "Entire paper remains exportable")
        self.assertFalse(self.window.findChildren(QMessageBox))
        self.window.search.clear()
        wait_for(lambda: not self.window._search_timer.isActive())
        self.assertEqual(self.window.browser._question.id, "a1")

    def test_filter_preserves_valid_selection_and_replaces_invalid_selection(self):
        self.enter()
        self.query("river")
        question = self.window.browser._question
        self.query("RIVER A2")
        self.assertIs(self.window.browser._question, question)
        self.query("forest")
        self.assertEqual(self.window.browser._question.id, "a1")
        self.query("")
        self.assertEqual(self.window.browser._question.id, "a1")

    def test_collapse_and_responsive_restore_keep_question_and_expansions(self):
        self.enter()
        window = self.window
        window.browser._toggle_answers(QUrl("ets-answer:0"))
        question = window.browser._question
        window.splitter.setSizes([360, 800])
        self.app.processEvents()
        width = window.splitter.sizes()[0]
        changes = QSignalSpy(window.browser.document().contentsChanged)
        QTest.mouseClick(window.sidebar_toggle, Qt.MouseButton.LeftButton)
        self.assertTrue(window.sidebar_collapsed)
        self.assertTrue(window.rail_expand.isVisible())
        QTest.mouseClick(window.rail_expand, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertFalse(window.sidebar_collapsed)
        self.assertAlmostEqual(window.splitter.sizes()[0], width, delta=4)
        self.assertEqual(changes.count(), 0)
        window.resize(620, 600)
        self.app.processEvents()
        self.assertTrue(window.sidebar_collapsed)
        self.assertFalse(window._manual_sidebar_collapsed)
        window.resize(1250, 800)
        self.app.processEvents()
        self.assertFalse(window.sidebar_collapsed)
        self.assertIs(window.browser._question, question)
        self.assertIn(0, window.browser._expanded_answers)
        self.assertEqual(changes.count(), 0)
        QTest.mouseClick(window.sidebar_toggle, Qt.MouseButton.LeftButton)
        window.resize(620, 600)
        window.resize(1250, 800)
        self.app.processEvents()
        self.assertTrue(window.sidebar_collapsed, "Automatic state must not overwrite manual choice")

    def test_qn_navigation_and_matches_stay_in_current_group(self):
        self.enter()
        self.assertTrue(getattr(self.window, "part_nav", None) is not None)
        self.assertTrue(self.window.part_nav.isVisible())
        self.assertEqual(len(self.window.part_buttons), 2)
        self.query("reference")
        count = self.window.browser.match_count
        self.assertEqual(count, 3)
        for _ in range(count + 1):
            QTest.mouseClick(self.window.next_match_button, Qt.MouseButton.LeftButton)
            self.assertEqual(self.window.browser._question.id, "a1")
        QTest.mouseClick(self.window.part_buttons[1], Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.part_buttons[1].isChecked())

    def test_metadata_only_matches_have_explanation_without_fabricated_count(self):
        self.query("content_a1")
        self.enter()
        self.assertEqual(getattr(self.window.browser, "match_count", None), 0)
        self.assertIn("metadata", self.window.match_label.text().lower())
        self.assertFalse(self.window.next_match_button.isEnabled())
        self.assertFalse(self.window.browser.extraSelections())

    def test_export_success_is_toast_entire_paper_and_error_has_destination(self):
        self.enter()
        self.query("sapphire")
        browser = self.window.browser
        before = browser.toPlainText()
        output = self.root / "output.txt"

        def save(dialog):
            dialog.selectFile(str(output))
            return QFileDialog.DialogCode.Accepted

        with patch.object(QFileDialog, "exec", save):
            self.window._export_answers()
        self.assertTrue(output.is_file())
        text = output.read_text(encoding="utf-8-sig")
        self.assertIn("Question group: a1", text)
        self.assertIn("Question group: a2", text)
        self.assertNotIn("sapphire", text)
        self.assertIn("First reference about trees", text)
        self.assertIn("Original second answer", text)
        self.assertEqual(browser.toPlainText(), before)
        self.assertFalse(self.window.findChildren(QMessageBox))
        self.assertTrue(self.window.toast.isVisible())
        self.assertIn("Paper exported successfully", self.window.toast.text())
        output = self.root / "missing" / "fail.txt"
        with patch.object(QFileDialog, "exec", save):
            self.window._export_answers()
        box = next(box for box in self.window.findChildren(QMessageBox) if box.isVisible())
        self.assertIn(str(output), box.informativeText())
        self.assertEqual(box.icon(), QMessageBox.Icon.Critical)
        box.accept()

    def test_empty_scan_and_failed_scan_have_inline_actions_and_real_details(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.window.load_folder(empty)
        wait_for(lambda: self.window._worker is None)
        self.assertFalse(self.window.findChildren(QMessageBox))
        self.assertIn("No Exams Found", getattr(self.window, "empty_title", self.window.status_label).text())
        missing = empty / "not-present"
        self.window.load_folder(missing)
        wait_for(lambda: self.window._worker is None)
        self.assertIn("failed", self.window.status_label.text().lower())
        self.assertTrue(self.window.error_details_button.isVisible())
        QTest.mouseClick(self.window.error_details_button, Qt.MouseButton.LeftButton)
        dialog = next(d for d in self.window.findChildren(QDialog) if d.isVisible())
        self.assertIn(str(missing), dialog.findChild(type(self.window.report_text)).toPlainText())
        dialog.accept()

    def test_progress_unknown_total_and_actual_percentage(self):
        self.window._update_progress(0, 0)
        self.assertEqual(self.window.progress.maximum(), 0)
        self.window._update_progress(7, 10)
        self.assertEqual(self.window.progress.value(), 7)
        self.assertIn("70%", self.window.status_label.text())

    def test_responsive_collapse_respects_the_user_sidebar_width(self):
        self.enter()
        # Keep the preferred navigation width while reserving a comfortable
        # reading area. A window can exceed both minimum widths and still be
        # too narrow for the actual sidebar width selected by the user.
        self.window._sidebar_width = 390
        self.window._sync_sidebar()
        self.window.resize(390 + self.window._reading_min - 12, 620)
        self.app.processEvents()
        self.assertTrue(self.window.sidebar_collapsed)
        self.window.resize(1250, 820)
        self.app.processEvents()
        self.assertFalse(self.window.sidebar_collapsed)
        self.assertAlmostEqual(self.window.splitter.sizes()[0], 390, delta=3)

    def test_automatically_collapsed_navigation_can_be_explicitly_revealed(self):
        self.enter()
        window = self.window
        window.resize(620, 600)
        self.app.processEvents()
        self.assertTrue(window._auto_sidebar_collapsed)
        question = window.browser._question
        changes = QSignalSpy(window.browser.document().contentsChanged)
        QTest.mouseClick(window.rail_expand, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertFalse(window.sidebar_collapsed)
        self.assertTrue(window.search.isVisible())
        self.assertIs(window.browser._question, question)
        self.assertEqual(changes.count(), 0)
        self.query("river")
        self.assertEqual(window.browser._question.id, "a2")
        self.assertEqual(window.browser.horizontalScrollBar().maximum(), 0)
        window.resize(610, 600)
        self.app.processEvents()
        self.assertTrue(window.sidebar_collapsed, "A new window width reevaluates responsive layout")
        window.resize(1250, 800)
        self.app.processEvents()
        self.assertFalse(window.sidebar_collapsed)
        self.assertFalse(window._manual_sidebar_collapsed)

    def test_large_images_grow_after_opening_in_a_narrow_window(self):
        folder = self.root / "Paper Images" / "content_image"
        folder.mkdir(parents=True)
        picture = QImage(1200, 600, QImage.Format.Format_RGB32)
        picture.fill(Qt.GlobalColor.lightGray)
        picture.save(str(folder / "local.png"))
        (folder / "content.json").write_text(json.dumps({"structure_type": "collector.picture", "info": {
            "stid": "image", "value": "Describe this local image", "image": "local.png",
            "std": [{"value": "Original stored fixture answer"}]}}), encoding="utf-8")
        self.window.resize(620, 620)
        self.window.load_folder(self.root / "Paper Images")
        wait_for(lambda: self.window._worker is None)
        self.enter()
        self.window.browser.anchorClicked.emit(QUrl("ets-shared:0"))
        self.app.processEvents()
        before = next(fmt.toImageFormat().width() for fmt in self.window.browser.document().allFormats() if fmt.isImageFormat())
        self.window.resize(1500, 820)
        self.app.processEvents()
        visible = []
        block = self.window.browser.document().begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fmt = it.fragment().charFormat()
                if fmt.isImageFormat():
                    visible.append(fmt.toImageFormat())
                it += 1
            block = block.next()
        self.assertEqual(len(visible), 1)
        self.assertGreater(visible[0].width(), before)
        self.assertGreaterEqual(visible[0].width(), 650)
        self.assertAlmostEqual(visible[0].width() / visible[0].height(), 2, delta=.02)
        self.assertEqual(self.window.browser.horizontalScrollBar().maximum(), 0)

    def test_theme_menu_preserves_the_reading_session_and_updates_custom_colors(self):
        self.enter()
        self.query("reference")
        window = self.window
        browser = window.browser
        window.theme_manager.set_mode("Light")
        browser.navigate_match(1)
        cursor = browser.document().find("Hidden sapphire")
        browser.setTextCursor(cursor)
        selected = cursor.selectedText()
        index = window.question_tree.currentIndex()
        expanded = set(browser._expanded_answers)
        position, text, match = browser.verticalScrollBar().value(), browser.toPlainText(), browser.current_match
        window.appearance_actions["Dark"].trigger()
        self.app.processEvents()
        self.assertEqual(window.theme_manager.mode, "Dark")
        self.assertEqual(window.question_tree.colors["background"], "#0B0D10")
        self.assertEqual(browser._colors["answer_background"], "#19241E")
        self.assertEqual(window.question_tree.currentIndex(), index)
        self.assertEqual(browser.toPlainText(), text)
        self.assertEqual(browser.textCursor().selectedText(), selected)
        self.assertEqual(browser.current_match, match)
        self.assertEqual(browser._expanded_answers, expanded)
        self.assertEqual(window.search.text(), "reference")
        self.assertAlmostEqual(browser.verticalScrollBar().value(), position, delta=3)
        self.assertEqual(self.settings.value("appearance/mode"), "Dark")

    def test_paper_back_restores_list_scroll_without_rescanning(self):
        result = self.window._result
        base = next(question for question in result.questions if question.id == "a1")
        questions = tuple(replace(base, id=f"group{index}", source=self.root / f"paper{index:02}" / "content_test" / "content.json")
                          for index in range(40))
        self.window._accept_result(replace(result, questions=questions, folder_modified={}))
        tree = self.window.question_tree
        self.app.processEvents()
        tree.verticalScrollBar().setValue(250)
        previous = tree.verticalScrollBar().value()
        self.assertGreater(previous, 0)
        self.enter(6)
        QTest.mouseClick(self.window.back_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertEqual(tree.verticalScrollBar().value(), previous)
        self.assertIsNone(self.window._worker)
        self.assertEqual(self.window.overview_totals, (40, 40, 80))

    def test_missing_modification_dates_sort_last_without_filesystem_reads(self):
        result = self.window._result
        dates = dict(result.folder_modified)
        dates[self.root / "试卷 Alpha"] = None
        with patch("ui.navigation.Path.stat", side_effect=AssertionError("UI must use scan metadata")):
            self.window._accept_result(replace(result, folder_modified=dates))
            self.assertIn("Paper Beta", self.window.proxy.index(0, 0).data())
            self.assertIn("Date unavailable", self.window.proxy.index(1, 0).data())
            self.enter(1)
            self.assertEqual(self.window.paper_date_label.text(), "Date unavailable")

    def test_scan_notes_open_scrollable_details_and_preserve_reading(self):
        result = self.window._result
        issue = ScanIssue(self.root / "broken.json", "Cannot load this copy: invalid JSON")
        self.window._accept_result(replace(result, issues=(issue,)))
        self.enter()
        before = self.window.browser.toPlainText()
        self.assertIn("1 scan notes", self.window.status_label.text())
        QTest.mouseClick(self.window.report_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(self.window.report_text.isVisible())
        self.assertTrue(self.window.report_text.isReadOnly())
        self.assertIn(str(issue.path), self.window.report_text.toPlainText())
        self.assertIn(issue.message, self.window.report_text.toPlainText())
        self.window.report_text.window().close()
        self.assertEqual(self.window.browser.toPlainText(), before)

    def test_replacement_and_shutdown_do_not_accept_stale_scans(self):
        self.window.load_folder(self.root)
        self.window.load_folder(self.root / "Paper Beta")
        wait_for(lambda: self.window._worker is None)
        self.assertEqual(self.window._result.root, (self.root / "Paper Beta").resolve())
        self.assertEqual(len(self.window._result.questions), 1)
        self.window.load_folder(self.root)
        self.window.close()
        wait_for(lambda: self.window._worker is None)
        wait_for(lambda: not self.window.isVisible())


if __name__ == "__main__":
    unittest.main()
