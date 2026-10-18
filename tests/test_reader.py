"""Synthetic reading-workspace coverage, independent of a private ETS bank."""

import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QTextDocument, QWheelEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QPushButton

from core.parser import Option, Question, StoredAnswer, SubQuestion
from tests.bank import temporary_bank
from ui.renderer import LocalTextBrowser, local_image
from ui.theme import DARK_COLORS, LIGHT_COLORS


class ReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.root = Path(self.enterContext(temporary_bank()))
        self.source = self.root / "content.json"
        self.source.write_text("{}", encoding="utf-8")
        self.browser = LocalTextBrowser()
        self.addCleanup(self.browser.close)
        self.browser.resize(900, 560)
        self.browser.show()
        self.app.processEvents()

    def question(self, **kwargs):
        parts = (
            SubQuestion("1", "First prompt", context="Part-owned transcript",
                        options=(Option("A", "Alpha option"), Option("B", "Beta option")),
                        answers=(StoredAnswer("B", "answer"), StoredAnswer("Hidden jade answer", "ref[0]"),
                                 StoredAnswer("Last reference", "ref[1]")),
                        explanation="Part explanation", translation="Part translation"),
            SubQuestion("2", "Second prompt", answers=(StoredAnswer("Primary second", "answer"),
                                                         StoredAnswer("Hidden amber answer", "ref[0]"))),
            SubQuestion("3", "Third prompt"),
        )
        return Question("synthetic", "collector.choose", self.source, kwargs.pop("parts", parts), **kwargs)

    def outer_card(self, text):
        cursor = self.browser.document().find(text)
        self.assertFalse(cursor.isNull(), text)
        table = cursor.currentTable()
        while table is not None and not table.property("cardTone"):
            table = table.parentFrame()
        self.assertIsNotNone(table, text)
        return table

    def require_search(self):
        self.assertTrue(callable(getattr(self.browser, "set_search", None)), "reader search is missing")

    def test_options_are_neutral_inside_one_question_card_and_answers_are_separate(self):
        self.browser.show_question(self.question(), self.root)
        question_card = self.outer_card("First prompt")
        self.assertIs(self.outer_card("Alpha option"), question_card)
        self.assertIs(self.outer_card("Beta option"), question_card)
        self.assertEqual(question_card.property("cardTone"), "question")
        # Locate the stored B after the question/options area, rather than its label.
        primary = next(table for table in self.browser._card_tables if table.property("cardTone") == "answer")
        self.assertIsNot(primary, question_card)
        a = self.browser.document().find("Alpha option").charFormat()
        b = self.browser.document().find("Beta option").charFormat()
        self.assertEqual(a.foreground(), b.foreground())
        self.assertEqual(a.background(), b.background())
        self.assertEqual(a.fontWeight(), b.fontWeight())
        self.assertNotIn("Hidden jade answer", self.browser.toPlainText())

    def test_additional_references_share_one_card_and_expansion_is_independent(self):
        self.browser.show_question(self.question(), self.root)
        self.browser._toggle_answers(QUrl("ets-answer:0"))
        self.assertIs(self.outer_card("Hidden jade answer"), self.outer_card("Last reference"))
        self.assertIsNot(self.outer_card("Hidden jade answer"), self.outer_card("First prompt"))
        self.assertNotIn("Hidden amber answer", self.browser.toPlainText())
        self.browser._toggle_answers(QUrl("ets-answer:1"))
        self.browser._toggle_answers(QUrl("ets-answer:0"))
        self.assertIn("Hidden amber answer", self.browser.toPlainText())
        self.assertNotIn("Hidden jade answer", self.browser.toPlainText())

    def test_missing_answer_is_a_separate_neutral_card(self):
        self.browser.show_question(self.question(), self.root)
        missing = self.outer_card("Answer not found")
        self.assertEqual(missing.property("cardTone"), "missing")
        self.assertIsNot(missing, self.outer_card("Third prompt"))

    def test_only_group_owned_material_is_collapsed_and_real_entry_resets_it(self):
        question = self.question(context="Shared indigo passage", instructions="Visible instruction", topic="Visible topic")
        self.browser.show_question(question, self.root)
        self.assertNotIn("Shared indigo passage", self.browser.toPlainText())
        for text in ("Visible instruction", "Visible topic", "Part-owned transcript", "Part translation", "Part explanation"):
            self.assertIn(text, self.browser.toPlainText())
        buttons = self.browser.viewport().findChildren(QPushButton, "sharedToggle")
        self.assertEqual(len(buttons), 1)
        QTest.mouseClick(buttons[0], Qt.MouseButton.LeftButton)
        self.assertIn("Shared indigo passage", self.browser.toPlainText())
        self.browser.show_question(question, self.root, image_width=480)
        self.assertIn("Shared indigo passage", self.browser.toPlainText())
        self.browser.show_question(self.question(), self.root)
        self.browser.show_question(question, self.root)
        self.assertNotIn("Shared indigo passage", self.browser.toPlainText())

    def test_plain_resize_reflows_without_rebuild_and_retains_selection(self):
        part = SubQuestion("1", "A long source paragraph. " * 150, answers=(StoredAnswer("First", "answer"), StoredAnswer("Extra", "ref")))
        self.browser.show_question(self.question(parts=(part,)), self.root)
        self.browser._toggle_answers(QUrl("ets-answer:0"))
        selection = self.browser.document().find("source paragraph")
        self.browser.setTextCursor(selection)
        self.browser.verticalScrollBar().setValue(200)
        changes = QSignalSpy(self.browser.document().contentsChanged)
        self.browser.resize(440, 560)
        self.app.processEvents()
        self.assertEqual(changes.count(), 0)
        self.assertEqual(self.browser.textCursor().selectedText(), "source paragraph")
        self.assertIn("Extra", self.browser.toPlainText())
        self.assertGreater(self.browser.verticalScrollBar().value(), 0)
        self.assertEqual(self.browser.horizontalScrollBar().maximum(), 0)

    def test_wide_reading_column_is_centered_without_narrow_overflow(self):
        self.browser.resize(1500, 560)
        self.browser.show_question(self.question(), self.root)
        self.app.processEvents()
        self.assertLessEqual(self.browser.viewport().width(), 822)
        self.assertGreater(self.browser.viewportMargins().left(), 250)
        self.browser.resize(400, 560)
        self.app.processEvents()
        self.assertEqual(self.browser.viewportMargins().left(), 0)
        self.assertEqual(self.browser.horizontalScrollBar().maximum(), 0)

    def test_native_card_gaps_keep_question_and_reference_visually_separate(self):
        self.browser.show_question(self.question(), self.root)
        question, answer = self.browser._card_tables[:2]
        gap = self.browser._card_bounds(answer).top() - self.browser._card_bounds(question).bottom()
        self.assertGreaterEqual(gap, 10)
        self.assertLessEqual(gap, 18)

    def test_native_collapse_controls_use_drawn_icons_without_symbol_glyphs(self):
        self.browser.show_question(self.question(context="Shared source"), self.root)
        for button in (*self.browser._answer_buttons.values(), *self.browser._shared_buttons.values()):
            self.assertEqual(button.text(), button.accessibleName())
            self.assertFalse(button.icon().isNull())

    def test_primary_answer_header_has_a_drawn_semantic_icon(self):
        self.browser.show_question(self.question(parts=(SubQuestion("1", "Prompt", answers=(StoredAnswer("B", "answer"),)),)), self.root)
        primary = next(table for table in self.browser._card_tables if table.property("cardTone") == "answer")
        header = self.browser.cursorRect(primary.firstCursorPosition())
        bounds = self.browser._card_bounds(primary)
        self.assertGreaterEqual(header.left() - bounds.left(), 32)
        snapshot = self.browser.viewport().grab()
        scale = snapshot.devicePixelRatio()
        image = snapshot.toImage()
        accent = QColor(LIGHT_COLORS["answer_accent"])
        colored = 0
        for x in range(header.left() - 20, header.left() - 4):
            for y in range(header.top(), header.bottom() + 1):
                color = image.pixelColor(round(x * scale), round(y * scale))
                if max(abs(color.red() - accent.red()), abs(color.green() - accent.green()),
                       abs(color.blue() - accent.blue())) < 30:
                    colored += 1
        self.assertGreater(colored, 6)

    def test_visible_original_ids_are_searchable_without_matching_generated_headings(self):
        self.browser.show_question(self.question(), self.root, query="synthetic")
        self.assertEqual(self.browser.match_count, 1)
        self.browser.navigate_match(0)
        self.assertEqual(self.browser.textCursor().selectedText(), "synthetic")
        self.browser.set_search("Question")
        self.assertEqual(self.browser.match_count, 0)

    def test_search_autoexpands_hidden_sources_counts_terms_and_ignores_controls(self):
        self.require_search()
        self.browser.show_question(self.question(context="Shared indigo passage"), self.root,
                                   query="INDIGO JADE amber")
        self.assertTrue(self.browser._shared_expanded)
        self.assertEqual(self.browser._expanded_answers, {0, 1})
        self.assertEqual(self.browser.match_count, 3)
        self.assertEqual(self.browser.current_match, 1)
        self.assertEqual(len(self.browser.extraSelections()), 3)
        self.browser.set_search("Show")
        self.assertEqual(self.browser.match_count, 0)

    def test_match_count_survives_manual_collapse_and_navigation_reopens_exact_target(self):
        self.require_search()
        self.browser.show_question(self.question(context="Shared indigo passage"), self.root,
                                   query="indigo jade amber")
        self.browser.anchorClicked.emit(QUrl("ets-shared:0"))
        self.browser._toggle_answers(QUrl("ets-answer:0"))
        self.browser._toggle_answers(QUrl("ets-answer:1"))
        self.assertEqual(self.browser.match_count, 3)
        self.assertEqual(len(self.browser.extraSelections()), 0)
        self.browser.navigate_match(1)
        self.assertIn(0, self.browser._expanded_answers)
        self.assertEqual(self.browser.current_match, 2)
        self.assertEqual(self.browser.textCursor().selectedText().casefold(), "jade")
        self.browser.navigate_match(1)
        self.assertEqual(self.browser.current_match, 3)
        self.assertEqual(self.browser.textCursor().selectedText().casefold(), "amber")
        self.browser.navigate_match(1)
        self.assertTrue(self.browser._shared_expanded)
        self.assertEqual(self.browser.current_match, 1)
        self.assertEqual(self.browser.textCursor().selectedText().casefold(), "indigo")
        self.browser.navigate_match(-1)
        self.assertEqual(self.browser.current_match, 3)

    def test_unicode_casefold_and_rich_markup_matches_have_exact_positions(self):
        self.require_search()
        part = SubQuestion("1", "前面😀 <b>Straße</b> 与 中文。STRASSE", answers=())
        self.browser.show_question(self.question(parts=(part,)), self.root, query="STRASSE 中文")
        self.assertEqual(self.browser.match_count, 3)
        self.browser.navigate_match(0)
        self.assertEqual(self.browser.textCursor().selectedText(), "Straße")
        self.browser.navigate_match(1)
        self.assertEqual(self.browser.textCursor().selectedText(), "中文")
        self.browser.navigate_match(1)
        self.assertEqual(self.browser.textCursor().selectedText(), "STRASSE")

    def test_metadata_only_search_has_zero_matches_and_clear_preserves_expansion_selection(self):
        self.require_search()
        self.browser.show_question(self.question(), self.root, query=str(self.root))
        self.assertEqual(self.browser.match_count, 0)
        self.assertEqual(self.browser.current_match, 0)
        self.assertEqual(self.browser.extraSelections(), [])
        self.browser.set_search("jade")
        selection = self.browser.document().find("First prompt")
        self.browser.setTextCursor(selection)
        self.browser.set_search("")
        self.assertEqual(self.browser.match_count, 0)
        self.assertEqual(self.browser.extraSelections(), [])
        self.assertEqual(self.browser.textCursor().selectedText(), "First prompt")
        self.assertIn(0, self.browser._expanded_answers)

    def test_part_navigation_and_manual_scroll_keep_independent_expansions(self):
        self.assertTrue(callable(getattr(self.browser, "scroll_to_part", None)), "part navigation is missing")
        long = "Long passage line.<br>" * 90
        question = self.question(parts=(SubQuestion("1", long), SubQuestion("2", long), SubQuestion("3", long)))
        self.browser.show_question(question, self.root)
        active = QSignalSpy(self.browser.active_part_changed)
        self.browser.scroll_to_part(1)
        self.assertGreater(self.browser.verticalScrollBar().value(), 0)
        cursor = self.browser.document().find("Question 2")
        self.assertGreaterEqual(self.browser.cursorRect(cursor).top(), 0)
        self.assertLess(self.browser.cursorRect(cursor).top(), 60)
        self.browser.verticalScrollBar().setValue(self.browser.verticalScrollBar().maximum())
        self.app.processEvents()
        self.assertEqual(active.at(active.count() - 1)[0], 2)

    def test_local_images_resize_preserve_ratio_selection_and_expansion(self):
        image = QImage(1200, 600, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        self.assertTrue(image.save(str(self.root / "picture.png")))
        part = SubQuestion("1", "Image prompt", images=("picture.png",),
                           answers=(StoredAnswer("First answer", "answer"), StoredAnswer("Extra answer", "ref")))
        self.browser.show_question(self.question(parts=(part,)), self.root)
        self.browser._toggle_answers(QUrl("ets-answer:0"))
        self.browser.setTextCursor(self.browser.document().find("First answer"))
        self.browser.resize(400, 560)
        self.app.processEvents()
        formats = [fmt.toImageFormat() for fmt in self.browser.document().allFormats() if fmt.isImageFormat()]
        self.assertEqual(len(formats), 1)
        self.assertLessEqual(formats[0].width(), 400 - 48 - 32)
        self.assertAlmostEqual(formats[0].height() / formats[0].width(), 0.5, delta=0.01)
        self.assertEqual(self.browser.textCursor().selectedText(), "First answer")
        self.assertIn("Extra answer", self.browser.toPlainText())

    def test_adjacent_source_table_images_fit_their_cells_on_narrow_resize(self):
        image = QImage(1200, 600, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        self.assertTrue(image.save(str(self.root / "large.png")))
        prompt = ('<table><tr><td><img src="large.png"></td>'
                  '<td><img src="large.png"></td></tr></table>')
        self.browser.show_question(self.question(parts=(SubQuestion("1", prompt),)), self.root)
        self.browser.resize(400, 560)
        self.app.processEvents()
        self.assertLessEqual(self.browser.document().size().width(), self.browser.viewport().width() + 1)
        self.assertEqual(self.browser.horizontalScrollBar().maximum(), 0)
        formats = [fmt.toImageFormat() for fmt in self.browser.document().allFormats() if fmt.isImageFormat()]
        self.assertTrue(formats)
        for fmt in formats:
            self.assertLessEqual(fmt.width(), 160)
            self.assertAlmostEqual(fmt.height() / fmt.width(), 0.5, delta=0.01)

    def test_nested_table_image_spans_resize_without_losing_search_or_source_selection(self):
        for name, width, height in (("large.png", 1200, 600), ("small.png", 24, 12)):
            image = QImage(width, height, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.white)
            self.assertTrue(image.save(str(self.root / name)))
        prompt = ('<table><tr><td rowspan="2"><img src="small.png"></td><td colspan="2">'
                  '<table><tr><td><img src="large.png"></td><td><img src="large.png"></td></tr></table>'
                  '</td></tr><tr><td><img src="large.png"></td><td>Exact cobalt source</td></tr></table>')
        part = SubQuestion("1", prompt, answers=(StoredAnswer("Primary", "answer"), StoredAnswer("Hidden jade", "ref")))
        self.browser.show_question(self.question(parts=(part,), context="Shared indigo"), self.root,
                                   query="cobalt jade indigo")
        self.browser.setTextCursor(self.browser.document().find("Exact cobalt source"))
        for width in (400, 900, 400):
            with self.subTest(width=width):
                self.browser.resize(width, 560)
                self.app.processEvents()
                self.assertLessEqual(self.browser.document().size().width(), self.browser.viewport().width() + 1)
                self.assertEqual(self.browser.horizontalScrollBar().maximum(), 0)
                self.assertEqual(self.browser.textCursor().selectedText(), "Exact cobalt source")
                self.assertEqual(self.browser.match_count, 3)
                self.assertTrue(self.browser._shared_expanded)
                self.assertEqual(self.browser._expanded_answers, {0})
                formats = [fmt.toImageFormat() for fmt in self.browser.document().allFormats() if fmt.isImageFormat()]
                for fmt in formats:
                    self.assertAlmostEqual(fmt.height() / fmt.width(), 0.5, delta=0.02)
                    if fmt.name().endswith("small.png"):
                        self.assertLessEqual(fmt.width(), 24)
        self.browser.set_theme(DARK_COLORS)
        self.browser.navigate_match(1)
        self.assertEqual(self.browser.textCursor().selectedText(), "cobalt")
        self.assertLessEqual(self.browser.document().size().width(), self.browser.viewport().width() + 1)

    def test_sanitization_and_resource_containment_survive_search_and_controls(self):
        self.require_search()
        part = SubQuestion("1", '<script>badscript()</script>Safe needle <img src="https://invalid.example/x.png">')
        self.browser.show_question(self.question(parts=(part,)), self.root, query="needle")
        self.assertNotIn("badscript", self.browser.toPlainText())
        self.assertNotIn("<img", self.browser.toHtml())
        self.assertEqual(self.browser.match_count, 1)
        for url in (QUrl("https://invalid.example/x.png"), QUrl.fromLocalFile(str(Path(__file__)))):
            self.assertTrue(self.browser.loadResource(QTextDocument.ResourceType.ImageResource, url).isNull())
        for reference in ("../outside.png", "file://host/share/image.png", r"\\host\share\image.png"):
            self.assertIsNone(local_image(reference, self.source, self.root))

    def test_search_in_source_tables_and_repeated_part_text_is_not_lost_or_deduplicated(self):
        table = "<table><tr><td>Nested <b>cobalt</b> source</td><td>Other cell</td></tr></table>"
        parts = (SubQuestion("1", table), SubQuestion("2", table))
        self.browser.show_question(self.question(parts=parts), self.root, query="COBALT")
        self.assertEqual(self.browser.match_count, 2)
        self.browser.navigate_match(0)
        self.assertEqual(self.browser.textCursor().selectedText(), "cobalt")
        first = self.browser.textCursor().selectionStart()
        self.browser.navigate_match(1)
        self.assertEqual(self.browser.textCursor().selectedText(), "cobalt")
        self.assertGreater(self.browser.textCursor().selectionStart(), first)

    def test_theme_refresh_preserves_scroll_selection_search_and_expansions(self):
        part = SubQuestion("1", "Original selectable text.<br>" * 60,
                           answers=(StoredAnswer("Primary", "answer"), StoredAnswer("Hidden jade", "ref")))
        question = self.question(parts=(part,), context="Shared indigo passage")
        self.browser.show_question(question, self.root, query="jade indigo")
        self.browser.navigate_match(1)
        selection = self.browser.document().find("Original selectable text.")
        self.browser.setTextCursor(selection)
        self.browser.verticalScrollBar().setValue(500)
        before = self.browser.verticalScrollBar().value()
        self.browser.set_theme(DARK_COLORS)
        self.app.processEvents()
        self.assertEqual(self.browser.textCursor().selectedText(), "Original selectable text.")
        self.assertLessEqual(abs(self.browser.verticalScrollBar().value() - before), 2)
        self.assertTrue(self.browser._shared_expanded)
        self.assertEqual(self.browser._expanded_answers, {0})
        self.assertEqual(self.browser.match_count, 2)
        self.assertEqual(self.browser.current_match, 2)
        self.assertEqual(len(self.browser.extraSelections()), 2)
        body = self.browser.document().find("Original selectable text.")
        self.assertEqual(body.charFormat().foreground().color().name(), DARK_COLORS["text_primary"].lower())
        self.browser.set_theme(LIGHT_COLORS)
        self.assertEqual(self.browser.textCursor().selectedText(), "Original selectable text.")

    def test_bottom_collapse_returns_focus_to_its_surviving_top_control(self):
        answers = tuple(StoredAnswer(f"Reference {index}.<br>" * 10, f"ref[{index}]") for index in range(8))
        question = self.question(parts=(SubQuestion("1", "First prompt", answers=answers),
                                        SubQuestion("2", "Second prompt", answers=answers)))
        self.browser.show_question(question, self.root)
        self.browser._toggle_answers(QUrl("ets-answer:0"))
        self.browser._toggle_answers(QUrl("ets-answer:1"))
        top = self.browser._answer_buttons["ets-answer:0"]
        bottom = self.browser._answer_buttons["ets-answer:0#bottom"]
        bar = self.browser.verticalScrollBar()
        bar.setValue(bar.value() + bottom.y() - self.browser.viewport().height() + 60)
        bottom.setFocus()
        QTest.keyClick(bottom, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertTrue(top.hasFocus())
        self.assertTrue(self.browser.viewport().rect().intersects(top.geometry()))
        self.assertEqual(self.browser._expanded_answers, {1})

    def test_focused_answer_control_wheel_scroll_is_monotonic(self):
        answers = tuple(StoredAnswer(f"Reference {index}.<br>" * 15, f"ref[{index}]") for index in range(20))
        self.browser.show_question(self.question(parts=(SubQuestion("1", "Prompt", answers=answers),)), self.root)
        button = self.browser._answer_buttons["ets-answer:0"]
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        self.assertTrue(button.hasFocus())
        bar = self.browser.verticalScrollBar()
        point = QPoint(250, 250)
        for delta in (-120, 120):
            for _ in range(20):
                before = bar.value()
                event = QWheelEvent(QPointF(point), QPointF(self.browser.viewport().mapToGlobal(point)),
                                    QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                                    Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
                QApplication.sendEvent(self.browser.viewport(), event)
                self.app.processEvents()
                after = bar.value()
                self.assertLessEqual(abs(after - before), bar.pageStep())
                self.assertGreaterEqual(after, before) if delta < 0 else self.assertLessEqual(after, before)


if __name__ == "__main__":
    unittest.main()
