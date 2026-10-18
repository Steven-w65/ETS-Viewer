import json
import os
from dataclasses import replace
from datetime import datetime
from html import escape
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, QTimer, Qt, QUrl
from PySide6.QtGui import QMouseEvent, QTextBlockFormat, QTextDocument, QWheelEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QPushButton, QSplitter

from core.parser import parse_document, plain_text
from tests.bank import BANK, read_record, temporary_bank
from ui.main_window import MainWindow
from ui.renderer import LocalTextBrowser, local_image, render_question, safe_fragment


@unittest.skipUnless(BANK.is_dir(), "Set ETS_TEST_BANK to the inspected ETS bank")
class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait_for(self, predicate, timeout=10):
        if predicate():
            return
        # Use Qt's actual event loop. Repeated QTest.qWait calls retain the
        # GIL for much of the wait on this runtime and starve Python workers.
        loop = QEventLoop()
        poll = QTimer(loop)
        poll.setInterval(10)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        deadline = QTimer(loop)
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        poll.start()
        deadline.start(int(timeout * 1000))
        loop.exec()
        poll.stop()
        deadline.stop()
        self.assertTrue(predicate(), "GUI operation did not complete")

    def test_export_uses_complete_selected_paper_and_preserves_answer_expansion(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        self.assertTrue(window.export_button.isHidden())
        window.load_folder(BANK / '586944')
        self.wait_for(lambda: window._worker is None)
        self.assertTrue(window.export_button.isHidden())
        window.question_tree.setCurrentIndex(window.proxy.index(0, 0))
        self.assertTrue(window.export_button.isVisible())
        window.search.setText('632489')
        self.wait_for(lambda: not window._search_timer.isActive())
        self.assertEqual(window.proxy.rowCount(window.proxy.index(0, 0)), 1)
        self.assertTrue(window.export_button.isVisible())
        window.browser._toggle_answers(QUrl('ets-answer:0'))
        before = window.browser.toPlainText()
        selection = window.question_tree.currentIndex()
        self.assertIn('Who inspired you to choose Italy', before)
        with temporary_bank() as temporary:
            root = Path(temporary)
            roaming = root / 'Roaming'
            (roaming / 'ETS').mkdir(parents=True)
            output = root / '英语答案.txt'

            def choose_save(dialog):
                self.assertEqual(Path(dialog.directory().absolutePath()), roaming / 'ETS')
                self.assertEqual(Path(dialog.selectedFiles()[0]).name, '586944.txt')
                self.assertEqual(dialog.acceptMode(), QFileDialog.AcceptMode.AcceptSave)
                self.assertEqual(dialog.defaultSuffix(), 'txt')
                self.assertEqual(dialog.nameFilters(), ['Text files (*.txt)'])
                dialog.selectFile(str(output))
                return QFileDialog.DialogCode.Accepted

            with patch.dict(os.environ, {'APPDATA': str(roaming)}), patch.object(QFileDialog, 'exec', choose_save):
                QTest.mouseClick(window.export_button, Qt.MouseButton.LeftButton)
            self.assertTrue(window.toast.isVisible())
            self.assertIn('Paper exported successfully', window.toast.text())
            self.assertIn(str(output), window.toast.toolTip())
            self.assertFalse(window.findChildren(QMessageBox))
            exported = output.read_text(encoding='utf-8-sig')
            self.assertEqual(exported.count('Question group: '), 12)
            self.assertEqual(exported.count('\nAnswer:\n'), 32)
            self.assertNotIn('Who inspired you to choose Italy', exported)
        self.assertEqual(window.browser.toPlainText(), before)
        self.assertEqual(window.question_tree.currentIndex(), selection)
        window.search.setText('no matching test 000000000')
        self.wait_for(lambda: not window._search_timer.isActive())
        self.assertTrue(window.export_button.isVisible(), 'The complete selected paper remains exportable')

    def test_export_cancel_and_io_failure_leave_the_viewer_usable(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        window.load_folder(BANK / '586944')
        self.wait_for(lambda: window._worker is None)
        window.question_tree.setCurrentIndex(window.proxy.index(0, 0))
        with patch.object(QFileDialog, 'exec', return_value=QFileDialog.DialogCode.Rejected):
            QTest.mouseClick(window.export_button, Qt.MouseButton.LeftButton)
        self.assertFalse(window.findChildren(QMessageBox))
        with temporary_bank() as temporary:
            destination = Path(temporary) / 'missing' / 'answers.txt'

            def choose_save(dialog):
                dialog.selectFile(str(destination))
                return QFileDialog.DialogCode.Accepted

            with patch.object(QFileDialog, 'exec', choose_save):
                QTest.mouseClick(window.export_button, Qt.MouseButton.LeftButton)
            self.wait_for(lambda: any(b.isVisible() for b in window.findChildren(QMessageBox)))
            box = next(b for b in window.findChildren(QMessageBox) if b.isVisible())
            self.assertEqual(box.windowTitle(), 'Export failed')
            self.assertEqual(box.icon(), QMessageBox.Icon.Critical)
            self.assertIn(str(destination), box.informativeText())
            box.accept()
            self.assertFalse(destination.exists())
        self.assertTrue(window.export_button.isEnabled())
        window.question_tree.setCurrentIndex(window.proxy.index(0, 0, window.proxy.index(0, 0)))
        self.assertIn('Why is the woman surprised?', window.browser.toPlainText())

    def test_empty_or_unsupported_scan_shows_inline_state_after_stopping_and_allows_a_new_folder(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        with temporary_bank() as temporary:
            folder = Path(temporary)
            for unsupported in (False, True):
                with self.subTest(unsupported=unsupported):
                    if unsupported:
                        (folder / "unsupported.dat").write_bytes(b"Unsupported bank format")
                    window.load_folder(folder)
                    self.wait_for(lambda: window._worker is None)
                    self.assertEqual(window.empty_title.text(), "No Exams Found")
                    self.assertIn("No supported ETS exams", window.empty_description.text())
                    self.assertIn(str(folder), window.folder_label.toolTip())
                    self.assertIsNone(window._worker)
                    self.assertFalse(window._scanning)
                    self.assertTrue(window.progress.isHidden())
                    self.assertEqual(window.proxy.rowCount(), 0)
                    self.assertIn("No supported papers", window.status_label.text())
                    self.assertIs(window.workspace.currentWidget(), window.empty_page)
                    self.assertTrue(window.empty_action.isVisible())
                    self.assertEqual(window.empty_action.text(), "Change Bank")
                    self.assertFalse(window.findChildren(QMessageBox))
                    self.assertEqual(window.report_button.isVisible(), unsupported)
                    if unsupported:
                        self.assertTrue(any(issue.path == folder / "unsupported.dat" for issue in window._result.issues))
            window.load_folder(BANK / "586944")
            self.wait_for(lambda: window._worker is None)
            self.assertEqual(window.proxy.rowCount(window.proxy.index(0, 0)), 12)
            window.search.setText("no matching exam 000000000")
            self.wait_for(lambda: not window._search_timer.isActive())
            self.app.processEvents()
            self.assertFalse(window.findChildren(QMessageBox))
            self.assertIs(window.workspace.currentWidget(), window.overview_page)

    def test_replaced_or_closing_empty_scans_do_not_show_an_error_prompt(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        with temporary_bank() as temporary:
            window.load_folder(Path(temporary))
            window.load_folder(BANK / "586944")
            self.wait_for(lambda: window._worker is None)
            self.assertEqual(window._result.root, (BANK / "586944").resolve())
            self.assertFalse(window.findChildren(QMessageBox))
            window.load_folder(Path(temporary))
            window.close()
            self.wait_for(lambda: window._worker is None)
            self.wait_for(lambda: not window.isVisible())
            self.assertFalse(window.findChildren(QMessageBox))

    def test_background_load_search_and_real_answer_selection(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        ticks = []
        timer = QTimer(window)
        timer.setInterval(1)
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start()
        window.load_folder(BANK / "586944")
        self.assertIsNotNone(window._worker)
        self.wait_for(lambda: window._worker is None)
        self.assertGreater(len(ticks), 0)
        self.assertEqual(window.proxy.rowCount(), 1)
        self.assertEqual(window.proxy.rowCount(window.proxy.index(0, 0)), 12)
        self.assertEqual(window.overview_totals, (1, 12, 32))
        self.assertEqual([value.text() for value in window.stat_values], ["1", "12", "32"])
        window.search.setText("632481")
        self.wait_for(lambda: not window._search_timer.isActive())
        self.assertEqual(window.proxy.rowCount(), 1)
        self.assertEqual(window.proxy.rowCount(window.proxy.index(0, 0)), 1)
        self.assertIs(window.workspace.currentWidget(), window.overview_page)
        window.question_tree.setCurrentIndex(window.proxy.index(0, 0))
        self.assertIn("Why is the woman surprised?", window.browser.toPlainText())
        self.assertIn("C", window.browser.toPlainText())
        self.assertIn("The man has learned names quickly.", window.browser.toPlainText())
        window.search.setText("no such question id 000000000")
        self.wait_for(lambda: window.proxy.rowCount() == 0)
        self.assertIn("No matching questions", window.empty_title.text())
        self.assertIs(window.workspace.currentWidget(), window.empty_page)
        window.search.clear()
        self.wait_for(lambda: window.proxy.rowCount() == 1)
        self.assertEqual(window.proxy.rowCount(window.proxy.index(0, 0)), 12)
        self.assertIn("Why is the woman surprised?", window.browser.toPlainText())
        window._back_to_papers()
        window.search.clear()
        self.wait_for(lambda: not window._search_timer.isActive())
        self.assertIs(window.workspace.currentWidget(), window.overview_page)

    def test_papers_drill_down_to_their_own_question_children_with_keyboard_navigation(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        window.load_folder(BANK)
        self.wait_for(lambda: window._worker is None)
        seen_questions = []
        assignment = None
        for row in range(window.proxy.rowCount()):
            root = window.proxy.index(row, 0)
            self.assertIsNone(root.data(Qt.ItemDataRole.UserRole))
            self.assertFalse(window.question_tree.isExpanded(root))
            self.assertIn("Assignment / exam", root.data())
            for child_row in range(window.proxy.rowCount(root)):
                child = window.proxy.index(child_row, 0, root)
                question = child.data(Qt.ItemDataRole.UserRole)
                self.assertIsNotNone(question)
                self.assertIn(question.source.parent.parent.name, root.data())
                seen_questions.append(question.source)
            if "586944" in root.data():
                assignment = root
                self.assertEqual(window.proxy.rowCount(root), 12)
        self.assertEqual(set(seen_questions), {q.source for q in window._result.questions})
        self.assertEqual(len(seen_questions), len(window._result.questions))
        self.assertIsNotNone(assignment)
        self.assertIs(window.workspace.currentWidget(), window.overview_page)
        window.question_tree.setCurrentIndex(assignment)
        self.assertEqual(window.question_tree.rootIndex(), assignment)
        self.assertEqual(window.navigation_level, "questions")
        self.assertFalse(window.question_tree.itemsExpandable())
        child = window.proxy.index(0, 0, assignment)
        self.assertEqual(window.question_tree.currentIndex(), child)
        QTest.keyClick(window.question_tree, Qt.Key.Key_Down)
        self.assertEqual(window.question_tree.currentIndex(), window.proxy.index(1, 0, assignment))
        QTest.keyClick(window.question_tree, Qt.Key.Key_Up)
        self.assertEqual(window.question_tree.currentIndex(), child)
        window.question_tree.scrollTo(child)
        self.app.processEvents()
        QTest.mouseClick(window.question_tree.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.question_tree.visualRect(child).center())
        self.assertIn("Question 632481", window.browser.toPlainText())
        self.assertIn("The man has learned names quickly.", window.browser.toPlainText())

    def test_search_retains_assignment_parents_and_filters_their_children(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.load_folder(BANK)
        self.wait_for(lambda: window._worker is None)
        window.search.setText("586944")
        self.wait_for(lambda: not window._search_timer.isActive())
        self.assertEqual(window.proxy.rowCount(), 1)
        root = window.proxy.index(0, 0)
        self.assertIn("586944", root.data())
        self.assertEqual(window.proxy.rowCount(root), 12)
        self.assertFalse(window.question_tree.rootIndex().isValid())
        self.assertIs(window.workspace.currentWidget(), window.overview_page)
        window.search.setText("586944 ELEPHANTS")
        self.wait_for(lambda: not window._search_timer.isActive())
        self.assertEqual(window.proxy.rowCount(), 1)
        root = window.proxy.index(0, 0)
        self.assertEqual(window.proxy.rowCount(root), 1)
        self.assertEqual(window.proxy.index(0, 0, root).data(Qt.ItemDataRole.UserRole).id, "632484")
        self.assertIs(window.workspace.currentWidget(), window.overview_page)
        window.question_tree.setCurrentIndex(root)
        self.assertIn("Question 632484", window.browser.toPlainText())
        self.assertEqual(window.search.text(), "586944 ELEPHANTS")
        window._back_to_papers()
        window.search.clear()
        self.wait_for(lambda: not window._search_timer.isActive())
        for row in range(window.proxy.rowCount()):
            self.assertFalse(window.question_tree.isExpanded(window.proxy.index(row, 0)))
        self.assertIs(window.workspace.currentWidget(), window.overview_page)

    def test_selecting_one_question_folder_still_retains_its_assignment(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.load_folder(BANK / "586944" / "content_632481")
        self.wait_for(lambda: window._worker is None)
        self.assertEqual(window.proxy.rowCount(), 1)
        root = window.proxy.index(0, 0)
        self.assertIn("586944", root.data())
        self.assertEqual(window.proxy.rowCount(root), 1)
        self.assertEqual(window.proxy.index(0, 0, root).data(Qt.ItemDataRole.UserRole).id, "632481")

    def test_task_folder_date_is_visible_for_banks_and_their_questions_without_ui_filesystem_reads(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        window.load_folder(BANK / "586944")
        self.wait_for(lambda: window._worker is None)
        modified = datetime.fromtimestamp((BANK / "586944").stat().st_mtime)
        date = f"{modified:%Y-%m-%d}"
        root = window.proxy.index(0, 0)
        self.assertIn(f"Folder modified: {date}", root.data())
        self.assertIn(f"{modified:%Y-%m-%d %H:%M:%S}", root.data(Qt.ItemDataRole.ToolTipRole))
        with patch("ui.main_window.Path.stat", side_effect=AssertionError("Date metadata must already be cached")):
            window.question_tree.setCurrentIndex(root)
            self.assertEqual(window.paper_date_label.text(), date)
            self.assertIn(f"{modified:%Y-%m-%d %H:%M:%S}", window.paper_date_label.toolTip())
            self.assertFalse(window.paper_date_label.isHidden())
            window.question_tree.setCurrentIndex(window.proxy.index(0, 0, root))
            window.question_tree.viewport().grab()
            self.assertEqual(window.paper_date_label.text(), date)
            self.assertIn("Why is the woman surprised?", window.browser.toPlainText())
        window._back_to_papers()
        window.search.setText("no matching paper 000000000")
        self.wait_for(lambda: not window._search_timer.isActive())
        self.assertFalse(window.paper_date_label.isVisible())

    def test_picture_renders_actual_image_and_reading_marks_absent_answer(self):
        browser = LocalTextBrowser()
        browser.root = BANK
        path, data = read_record("586944/content_632490/content.json")
        question = parse_document(data, path)
        html = render_question(question, BANK, shared_expanded=True)
        browser.setHtml(html)
        self.assertIn("Shared umbrellas.", browser.toPlainText())
        self.assertIn("Answer", browser.toPlainText())
        self.assertIn("content.jpg", html)
        image = browser.loadResource(QTextDocument.ResourceType.ImageResource,
                                     QUrl.fromLocalFile(str(path.parent / "material" / "content.jpg")))
        self.assertFalse(image.isNull())
        path, data = read_record("586944/content_632488/content.json")
        browser.setHtml(render_question(parse_document(data, path), BANK, shared_expanded=True))
        self.assertIn("Earworms", browser.toPlainText())
        self.assertIn("Answer not found", browser.toPlainText())

    def test_navigation_and_question_panels_have_a_visible_resizable_gap(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        self.app.processEvents()
        self.wait_for(lambda: not window._resize_timer.isActive())
        splitter = window.findChild(QSplitter)
        left, right = splitter.widget(0), splitter.widget(1)
        gap = right.x() - left.x() - left.width()
        self.assertGreaterEqual(gap, 4)
        self.assertLessEqual(gap, 10)
        splitter.setSizes([400, 600])
        self.app.processEvents()
        self.wait_for(lambda: not window._resize_timer.isActive())
        self.assertEqual(right.x() - left.x() - left.width(), gap)

    def test_dragging_splitter_reflows_expanded_text_without_rebuilding_it(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        window.load_folder(BANK / '586944')
        self.wait_for(lambda: window._worker is None)
        window.search.setText('632489')
        self.wait_for(lambda: not window._search_timer.isActive() and not window._resize_timer.isActive())
        window.question_tree.setCurrentIndex(window.proxy.index(0, 0))
        window.browser.anchorClicked.emit(QUrl('ets-answer:0'))
        window.browser.verticalScrollBar().setValue(200)
        selection = window.question_tree.currentIndex()
        text = window.browser.toPlainText()
        changes = QSignalSpy(window.browser.document().contentsChanged)
        splitter = window.splitter
        before = splitter.sizes()
        handle = splitter.handle(1)
        origin = handle.mapToGlobal(handle.rect().center())
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
        for offset in (20, 40, 60, 80):
            target = origin + QPoint(offset, 0)
            event = QMouseEvent(QEvent.Type.MouseMove, QPointF(handle.mapFromGlobal(target)),
                                QPointF(target), Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(handle, event)
            self.app.processEvents()
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton,
                           pos=handle.mapFromGlobal(origin + QPoint(80, 0)))
        self.wait_for(lambda: not window._resize_timer.isActive())
        self.assertGreater(splitter.sizes()[0], before[0] + 40)
        self.assertEqual([splitter.widget(i).sizePolicy().horizontalStretch() for i in range(2)], [0, 1])
        self.assertEqual(changes.count(), 0, 'Resizing plain text must not rebuild the question document')
        self.assertEqual(window.browser.toPlainText(), text)
        self.assertEqual(window.question_tree.currentIndex(), selection)
        self.assertGreater(window.browser.verticalScrollBar().value(), 0)

    def test_wheel_scrolling_expanded_answers_never_jumps_to_another_answer_link(self):
        path, data = read_record('586944/content_632489/content.json')
        browser = LocalTextBrowser()
        self.addCleanup(browser.close)
        browser.resize(760, 600)
        browser.show()
        browser.show_question(parse_document(data, path), BANK)
        self.app.processEvents()
        button = browser._answer_buttons['ets-answer:0']
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(button.hasFocus())
        self.assertIn('Who inspired you to choose Italy', browser.toPlainText())
        bar = browser.verticalScrollBar()
        point = QPoint(400, 300)
        for delta in (-120, 120):
            # Taller cards and proportional text can require more than a fixed
            # number of notches; retain the per-notch stability checks to each end.
            for step in range(bar.maximum() // max(1, bar.singleStep()) + 2):
                before = bar.value()
                event = QWheelEvent(QPointF(point), QPointF(browser.viewport().mapToGlobal(point)),
                                    QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                                    Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
                QApplication.sendEvent(browser.viewport(), event)
                self.app.processEvents()
                after = bar.value()
                with self.subTest(delta=delta, step=step):
                    self.assertLessEqual(abs(after - before), bar.pageStep(),
                                         'One wheel notch must not skip several screens')
                    if delta < 0:
                        self.assertGreaterEqual(after, before)
                    else:
                        self.assertLessEqual(after, before)
                if after == (bar.maximum() if delta < 0 else 0):
                    break
            self.assertEqual(bar.value(), bar.maximum() if delta < 0 else 0)

    def test_resizing_a_question_still_scales_its_original_local_image(self):
        path, data = read_record('586944/content_632490/content.json')
        question = parse_document(data, path)
        browser = LocalTextBrowser()
        self.addCleanup(browser.close)
        browser.show_question(question, BANK, image_width=640)
        browser.anchorClicked.emit(QUrl('ets-shared:0'))
        self.assertTrue(browser._shared_expanded)
        before = [fmt.toImageFormat().width() for fmt in browser.document().allFormats() if fmt.isImageFormat()]
        text = browser.toPlainText()
        browser.show_question(question, BANK, image_width=320)
        after = [fmt.toImageFormat().width() for fmt in browser.document().allFormats() if fmt.isImageFormat()]
        self.assertEqual(len(before), 1)
        self.assertEqual(len(after), 1)
        self.assertLess(after[0], before[0])
        self.assertLessEqual(after[0], 320)
        self.assertEqual(browser.toPlainText(), text)
        self.assertTrue(browser._shared_expanded)

    def test_question_rows_have_one_continuous_highlight_across_focus_changes(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        window.load_folder(BANK / "586944")
        self.wait_for(lambda: window._worker is None)
        tree = window.question_tree
        parent = window.proxy.index(0, 0)
        tree.setCurrentIndex(parent)
        QTest.mouseMove(window.search)
        for index in (window.proxy.index(0, 0, parent), window.proxy.index(1, 0, parent)):
            tree.clearSelection()
            self.app.processEvents()
            row = tree.visualRect(index)
            normal_snapshot = tree.viewport().grab()
            scale = normal_snapshot.devicePixelRatio()
            normal_color = normal_snapshot.toImage().pixelColor(
                round((row.right() - 12) * scale), round(row.center().y() * scale)).getRgb()[:3]
            tree.setCurrentIndex(index)
            tree.selectionModel().select(index, tree.selectionModel().SelectionFlag.ClearAndSelect)
            tree.scrollTo(index)
            selected_background = None
            for focus in (tree, window.search, window.browser):
                with self.subTest(row=index.data(), focus=focus.accessibleName()):
                    focus.setFocus()
                    self.app.processEvents()
                    row = tree.visualRect(index)
                    snapshot = tree.viewport().grab()
                    image = snapshot.toImage()
                    scale = snapshot.devicePixelRatio()

                    def color(x, y):
                        return image.pixelColor(round(x * scale), round(y * scale)).getRgb()[:3]

                    background = color(row.right() - 12, row.center().y())
                    self.assertGreater(max(abs(a - b) for a, b in zip(background, normal_color)), 8,
                                       "The selected row must remain visibly highlighted")
                    if selected_background is not None:
                        self.assertEqual(background, selected_background,
                                         "Selection fill must remain consistent when focus changes")
                    selected_background = background

                    # The flat question list has no branch indentation. Sample
                    # the full safe interior to retain the continuous-fill check.
                    for y in (row.top() + 7, row.bottom() - 9):
                        reference = color(row.left() + 16, y)
                        for x in range(row.left() + 12, row.right() - 12, 8):
                            actual = color(x, y)
                            self.assertLessEqual(max(abs(a - b) for a, b in zip(actual, reference)), 3,
                                                 "Selection has a seam across the question row")

    def test_reference_answers_start_with_one_stored_response_per_part(self):
        path, data = read_record("586944/content_632489/content.json")
        browser = LocalTextBrowser()
        browser.setHtml(render_question(parse_document(data, path), BANK))
        text = browser.toPlainText()
        self.assertIn("What was your favorite place that you visited in Italy?", text)
        self.assertIn("What can I do the night before an exam to feel more relaxed?", text)
        self.assertNotIn("Who inspired you to choose Italy for your trip?", text)
        self.assertNotIn("Why does deep breathing help with stress?", text)
        self.assertIn("Show 51 more answers", text)
        self.assertIn("Show 50 more answers", text)

    def test_body_spacing_is_consistent_for_plain_paragraph_div_and_table_text(self):
        path, data = read_record("586944/content_632489/content.json")
        question = parse_document(data, path)
        prompt = plain_text(question.parts[0].prompt)
        browser = LocalTextBrowser()
        self.addCleanup(browser.close)
        browser.resize(760, 600)
        browser.show()
        for markup in ("{}", "<p>{}</p>", "<div>{}</div>", "<table><tr><td>{}</td></tr></table>"):
            with self.subTest(markup=markup):
                part = replace(question.parts[0], prompt=markup.format(escape(prompt)))
                browser.show_question(replace(question, parts=(part,)), BANK)
                cursor = browser.document().find(prompt)
                self.assertFalse(cursor.isNull())
                self.assertEqual(cursor.blockFormat().lineHeightType(),
                                 QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
                self.assertAlmostEqual(cursor.blockFormat().lineHeight(), 150)
                heading = browser.document().find(f"Question {question.id}")
                self.assertEqual(heading.blockFormat().headingLevel(), 1)
                self.assertNotEqual(heading.blockFormat().lineHeight(), 150)
                control = browser.document().find("Show 51 more answers")
                self.assertFalse(control.isNull())
                self.assertNotEqual(control.blockFormat().lineHeight(), 150)

    def test_reading_width_is_limited_on_wide_windows_and_fills_narrow_windows(self):
        path, data = read_record("586944/content_632489/content.json")
        browser = LocalTextBrowser()
        self.addCleanup(browser.close)
        browser.resize(1400, 600)
        browser.show()
        browser.show_question(parse_document(data, path), BANK)
        self.app.processEvents()
        self.assertGreaterEqual(browser.viewport().width(), 800)
        self.assertLessEqual(browser.viewport().width(), 820)
        self.assertGreater(browser.viewportMargins().left(), 200)
        self.assertLessEqual(browser.document().size().width(), browser.viewport().width() + 1)
        text = browser.toPlainText()
        changes = QSignalSpy(browser.document().contentsChanged)
        browser.resize(400, 600)
        self.app.processEvents()
        self.assertEqual(browser.viewportMargins().left(), 0)
        self.assertGreater(browser.viewport().width(), 350)
        self.assertLessEqual(browser.document().size().width(), browser.viewport().width() + 1)
        self.assertEqual(browser.horizontalScrollBar().maximum(), 0)
        self.assertEqual(browser.toPlainText(), text)
        self.assertEqual(changes.count(), 0, "Changing reading width must only reflow the document")

    def test_one_remaining_reference_uses_a_singular_count(self):
        path, data = read_record("586944/content_632489/content.json")
        question = parse_document(data, path)
        part = replace(question.parts[0], answers=question.parts[0].answers[:2])
        browser = LocalTextBrowser()
        self.addCleanup(browser.close)
        browser.show_question(replace(question, parts=(part,)), BANK)
        self.assertIn("Show 1 more answer", browser.toPlainText())
        self.assertNotIn("Show 2", browser.toPlainText())

    def test_bottom_collapse_returns_to_its_section_and_keeps_other_answers_expanded(self):
        path, data = read_record("586944/content_632489/content.json")
        question = parse_document(data, path)
        browser = LocalTextBrowser()
        self.addCleanup(browser.close)
        browser.resize(760, 600)
        browser.show()
        browser.show_question(question, BANK)
        browser.anchorClicked.emit(QUrl("ets-answer:1"))
        browser.anchorClicked.emit(QUrl("ets-answer:0"))
        self.app.processEvents()
        controls = browser.viewport().findChildren(QPushButton, "answerToggle")
        first_section = [button for button in controls if button.property("answerTarget") == "ets-answer:0"]
        self.assertEqual(len(first_section), 2, "An expanded section needs top and bottom collapse controls")
        top, bottom = sorted(first_section, key=lambda button: button.y())
        self.assertNotEqual(top.property("answerControl"), bottom.property("answerControl"))
        for part in question.parts:
            for answer in part.answers:
                self.assertIn(plain_text(answer.text), plain_text(browser.toPlainText()))
        bar = browser.verticalScrollBar()
        bar.setValue(bar.value() + bottom.y() - browser.viewport().height() + 60)
        self.app.processEvents()
        self.assertTrue(browser.viewport().rect().intersects(bottom.geometry()))
        bottom.setFocus()
        QTest.keyClick(bottom, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertEqual(top.accessibleName(), "Show 51 more answers")
        self.assertTrue(top.hasFocus(), "Focus must return to the surviving control for the same section")
        self.assertTrue(browser.viewport().rect().intersects(top.geometry()))
        self.assertNotIn(plain_text(question.parts[0].answers[1].text), plain_text(browser.toPlainText()))
        self.assertIn(plain_text(question.parts[0].answers[0].text), plain_text(browser.toPlainText()))
        self.assertIn(plain_text(question.parts[1].answers[-1].text), plain_text(browser.toPlainText()))
        self.assertEqual(sum(button.property("answerTarget") == "ets-answer:0"
                             for button in browser._answer_buttons.values()), 1)

    def test_answer_controls_expand_independently_and_reset_on_question_change(self):
        path, data = read_record("586944/content_632489/content.json")
        question = parse_document(data, path)
        browser = LocalTextBrowser()
        browser.show_question(question, BANK)
        browser.anchorClicked.emit(QUrl("ets-answer:0"))
        self.assertIn("Who inspired you to choose Italy for your trip?", browser.toPlainText())
        self.assertNotIn("Why does deep breathing help with stress?", browser.toPlainText())
        browser.anchorClicked.emit(QUrl("ets-answer:1"))
        self.assertIn("Why does deep breathing help with stress?", browser.toPlainText())
        browser.anchorClicked.emit(QUrl("ets-answer:0"))
        self.assertNotIn("Who inspired you to choose Italy for your trip?", browser.toPlainText())
        self.assertIn("Why does deep breathing help with stress?", browser.toPlainText())
        browser.show_question(question, BANK, image_width=480)
        self.assertIn("Why does deep breathing help with stress?", browser.toPlainText())
        path, data = read_record("586944/content_632481/content.json")
        browser.show_question(parse_document(data, path), BANK)
        browser.show_question(question, BANK)
        self.assertNotIn("Why does deep breathing help with stress?", browser.toPlainText())
        before = browser.toPlainText()
        for url in ("https://example.com", "ets-answer:99999", "ets-answer:invalid"):
            browser.anchorClicked.emit(QUrl(url))
        self.assertEqual(browser.toPlainText(), before)

    def test_window_resize_keeps_expanded_answers_and_reading_position(self):
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        window.load_folder(BANK / "586944")
        self.wait_for(lambda: window._worker is None)
        window.search.setText("632489")
        self.wait_for(lambda: not window._search_timer.isActive())
        window.question_tree.setCurrentIndex(window.proxy.index(0, 0))
        window.browser.anchorClicked.emit(QUrl("ets-answer:0"))
        window.browser.verticalScrollBar().setValue(200)
        window.resize(window.width() + 60, window.height() + 20)
        self.wait_for(lambda: not window._resize_timer.isActive())
        self.assertIn("Who inspired you to choose Italy for your trip?", window.browser.toPlainText())
        self.assertGreater(window.browser.verticalScrollBar().value(), 0)

    def test_answer_toggle_click_stays_in_place_when_variants_expand(self):
        path, data = read_record("586944/content_632489/content.json")
        browser = LocalTextBrowser()
        self.addCleanup(browser.close)
        browser.resize(760, 600)
        browser.show()
        browser.show_question(parse_document(data, path), BANK)
        cursor = browser.document().find("Show 51 more answers")
        cursor.setPosition(cursor.selectionStart() + 2)
        browser.setTextCursor(cursor)
        browser.ensureCursorVisible()
        before = browser.cursorRect(cursor).center()
        self.assertEqual(browser.anchorAt(before), "ets-answer:0")
        QTest.mouseClick(browser.viewport(), Qt.MouseButton.LeftButton, pos=before)
        self.app.processEvents()
        self.assertIn("Who inspired you to choose Italy for your trip?", browser.toPlainText())
        cursor = browser.document().find("Show fewer answers")
        cursor.setPosition(cursor.selectionStart() + 2)
        after = browser.cursorRect(cursor).center()
        self.assertLessEqual(abs(after.y() - before.y()), 24)
        QTest.mouseClick(browser.viewport(), Qt.MouseButton.LeftButton, pos=after)
        self.assertNotIn("Who inspired you to choose Italy for your trip?", browser.toPlainText())

    def test_native_answer_buttons_use_the_existing_toggle_behavior(self):
        path, data = read_record("586944/content_632489/content.json")
        browser = LocalTextBrowser()
        self.addCleanup(browser.close)
        browser.resize(760, 600)
        browser.show()
        browser.show_question(parse_document(data, path), BANK)
        self.app.processEvents()
        buttons = browser.viewport().findChildren(QPushButton, "answerToggle")
        self.assertEqual(len(buttons), 2)
        button = next(button for button in buttons if button.accessibleName() == "Show 51 more answers")
        self.assertTrue(button.isVisible())
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertIn("Who inspired you to choose Italy for your trip?", browser.toPlainText())
        self.assertNotIn("Why does deep breathing help with stress?", browser.toPlainText())
        self.assertEqual(button.accessibleName(), "Show fewer answers")
        button.setFocus()
        QTest.keyClick(button, Qt.Key.Key_Space)
        self.assertNotIn("Who inspired you to choose Italy for your trip?", browser.toPlainText())
        self.assertEqual(button.accessibleName(), "Show 51 more answers")
        browser.clear_question()
        self.assertFalse(button.isVisible())

    def test_source_markup_cannot_load_remote_or_outside_bank_resources(self):
        path, _ = read_record("586944/content_632490/content.json")
        html = safe_fragment('<script>untrusted()</script><b>Text</b><img src="https://example.com/x.png" onerror="untrusted()">', path, BANK)
        self.assertNotIn("<script", html)
        self.assertNotIn("untrusted()", html)
        self.assertNotIn("<img", html)
        browser = LocalTextBrowser()
        browser.root = BANK
        for url in (QUrl("https://example.com/x.png"), QUrl.fromLocalFile(str(Path(__file__)))):
            self.assertTrue(browser.loadResource(QTextDocument.ResourceType.ImageResource, url).isNull())

    def test_choice_question_text_is_shown_when_prompt_is_a_template_marker(self):
        path, data = read_record("586944/content_632484/content.json")
        question = parse_document(data, path)
        browser = LocalTextBrowser()
        browser.setHtml(render_question(question, BANK, shared_expanded=True))
        text = browser.toPlainText()
        self.assertIn("14. What is the main idea of the passage?", text)
        self.assertNotIn("\nTranscript\n", text)
        self.assertTrue(question.title.startswith("14. What is the main idea"))
        self.assertIn("Elephants have long been recognized", text)
        self.assertEqual([part.answers[0].text for part in question.parts], ["A", "B", "B"])

    def test_unicode_path_and_case_insensitive_keyword_search(self):
        _, data = read_record("586944/content_632481/content.json")
        with temporary_bank() as temporary:
            folder = Path(temporary) / "题库"
            folder.mkdir()
            (folder / "content.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            window = MainWindow()
            window.load_folder(folder)
            self.wait_for(lambda: window._worker is None)
            window.search.setText("题库")
            self.wait_for(lambda: not window._search_timer.isActive())
            self.assertEqual(window.proxy.rowCount(), 1)
            window.search.setText("WOMAN SURPRISED")
            self.wait_for(lambda: not window._search_timer.isActive())
            self.assertEqual(window.proxy.rowCount(), 1)
            window.close()

    def test_network_file_urls_are_rejected_before_any_filesystem_access(self):
        path, _ = read_record("586944/content_632490/content.json")
        with patch("ui.renderer.Path.resolve", side_effect=AssertionError("Network path accessed")):
            for reference in (
                "file://example.invalid/share/content.jpg",
                r"\\example.invalid\share\content.jpg",
                "file:////example.invalid/share/content.jpg",
                "file:///%5C%5Cexample.invalid/share/content.jpg",
            ):
                self.assertIsNone(local_image(reference, path, BANK))

    def test_replacement_and_close_during_loading_finish_without_stale_results(self):
        window = MainWindow()
        window.show()
        window.load_folder(BANK)
        window.load_folder(BANK / "586944")
        self.wait_for(lambda: window._worker is None)
        self.assertEqual(window.proxy.rowCount(), 1)
        self.assertEqual(window.proxy.rowCount(window.proxy.index(0, 0)), 12)
        self.assertEqual(window._result.root, (BANK / "586944").resolve())
        window.load_folder(BANK)
        window.close()
        self.wait_for(lambda: window._worker is None)
        self.wait_for(lambda: not window.isVisible())


if __name__ == "__main__":
    unittest.main()
