"""Question-card typography without changing source text or reader behavior."""
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QImage, QTextDocument
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from core.parser import Option, Question, StoredAnswer, SubQuestion
from tests.bank import temporary_bank
from ui.renderer import LocalTextBrowser
from ui.theme import DARK_COLORS, LIGHT_COLORS


class QuestionFormattingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.root = Path(self.enterContext(temporary_bank()))
        self.browser = LocalTextBrowser()
        self.addCleanup(self.browser.close)
        self.browser.resize(440, 620)
        self.browser.set_theme(DARK_COLORS)
        self.browser.show()
        self.app.processEvents()

    def show(self, context="", options=()):
        part = SubQuestion("1", "Which response follows the conversation?", context=context, options=options,
                           answers=(StoredAnswer("B", "answer"), StoredAnswer("Additional original reference", "ref")))
        question = Question("formatting", "collector.choose", self.root / "content.json", (part,))
        self.browser.show_question(question, self.root)
        self.app.processEvents()
        return question

    def cursor(self, text):
        cursor = self.browser.document().find(text, 0, QTextDocument.FindFlag.FindCaseSensitively)
        self.assertFalse(cursor.isNull(), text)
        cursor.setPosition(cursor.selectionStart())
        return cursor

    def test_question_label_is_small_muted_and_prompt_stays_readable(self):
        self.show()
        table = next(table for table in self.browser._card_tables if table.property("cardTone") == "question")
        heading = table.firstCursorPosition()
        self.assertEqual(heading.block().text(), "QUESTION")
        prompt = self.cursor("Which response")
        self.assertLess(heading.charFormat().fontPointSize(), prompt.charFormat().fontPointSize())
        self.assertEqual(heading.charFormat().foreground().color(), QColor(DARK_COLORS["text_muted"]))

    def test_dialogue_speakers_and_wrapped_text_align_with_native_hanging_indents(self):
        first = "Alpha " + "A longer dialogue line that naturally wraps. " * 8
        second = "Bravo " + "A second dialogue line that naturally wraps. " * 8
        for context in (f"<p>M: {first}</p><p>W: {second}</p>",
                        f"M: {first}\nW: {second}", f"M: {first}<br>W: {second}",
                        f"<p><b>M:</b> {first}</p><p>W：{second}</p>"):
            with self.subTest(context=context[:30]):
                self.show(context)
                starts = []
                for speaker, text in (("M:", "Alpha"), ("W" if "W：" in context else "W:", "Bravo")):
                    body = self.cursor(text)
                    label = self.cursor(speaker)
                    body_rect = self.browser.cursorRect(body)
                    label_rect = self.browser.cursorRect(label)
                    self.assertGreaterEqual(body_rect.left() - label_rect.left(), 24)
                    starts.append(body_rect.left())
                    block = body.block()
                    self.assertGreater(block.layout().lineCount(), 1)
                    continuation_x = (self.browser.document().documentLayout().blockBoundingRect(block).left()
                                      + block.layout().lineAt(1).x())
                    self.assertAlmostEqual(continuation_x, body_rect.left(), delta=1)
                    self.assertLessEqual(block.blockFormat().bottomMargin(), 4)
                self.assertAlmostEqual(starts[0], starts[1], delta=1)

    def test_dialogue_line_breaks_inline_emphasis_and_non_speaker_prose_survive(self):
        context = ("<p>M: First line.<br>Meaningful continuation.<br><br>After blank line.</p>"
                   "<p>W: A <b>bold word</b> stays.</p><p>This prose is not a speaker.</p>"
                   "<blockquote><p>M: Quoted original dialogue.</p></blockquote>")
        self.show(context)
        text = self.browser.toPlainText()
        self.assertIn("M: First line.\nMeaningful continuation.\n\nAfter blank line.", text)
        self.assertIn("W: A bold word stays.", text)
        document = self.browser.document()
        self.assertGreater(document.find("bold word").charFormat().fontWeight(),
                           document.find("stays.").charFormat().fontWeight())
        prose = self.cursor("This prose")
        self.assertEqual(prose.blockFormat().leftMargin(), 0)
        self.assertGreater(self.cursor("Quoted original").blockFormat().leftMargin(),
                           self.cursor("First line").blockFormat().leftMargin())

    def test_options_use_equal_fixed_label_columns_and_hanging_body_alignment(self):
        options = (Option("A", "Alpha " + "Long option text wraps consistently. " * 9),
                   Option("B", "<p>Bravo " + "Formatted option text wraps consistently. " * 9 + "</p>"),
                   Option("C", "Charlie\nOriginal continuation."), Option("D", "Delta"))
        self.show(options=options)
        starts, labels = [], []
        for label, text in (("A.", "Alpha"), ("B.", "Bravo"), ("C.", "Charlie"), ("D.", "Delta")):
            body = self.cursor(text)
            marker = self.cursor(label)
            body_table = body.currentTable()
            marker_table = marker.currentTable()
            self.assertIs(marker_table, body_table)
            self.assertEqual(body_table.columns(), 2)
            self.assertEqual(body_table.format().border(), 0)
            self.assertGreaterEqual(self.browser.cursorRect(body).left() - self.browser.cursorRect(marker).left(), 24)
            starts.append(self.browser.cursorRect(body).left())
            labels.append(self.browser.document().find(label).charFormat())
            if text in ("Alpha", "Bravo"):
                block = body.block()
                self.assertGreater(block.layout().lineCount(), 1)
                x = self.browser.document().documentLayout().blockBoundingRect(block).left() + block.layout().lineAt(1).x()
                self.assertAlmostEqual(x, self.browser.cursorRect(body).left(), delta=1)
        self.assertLessEqual(max(starts) - min(starts), 1)
        for fmt in labels[1:]:
            self.assertEqual(fmt.foreground(), labels[0].foreground())
            self.assertEqual(fmt.background(), labels[0].background())
            self.assertEqual(fmt.fontWeight(), labels[0].fontWeight())
        self.assertIn("Charlie\nOriginal continuation.", self.browser.toPlainText())
        self.assertEqual(self.browser.horizontalScrollBar().maximum(), 0)

    def test_formatted_sources_keep_exact_search_counts_and_answer_controls(self):
        self.show("<p>M: A cobalt line.<br>W: Another cobalt line.</p>",
                  (Option("A", "A cobalt option."), Option("B", "Another cobalt option.")))
        self.browser.set_search("cobalt")
        self.assertEqual(self.browser.match_count, 4)
        positions = []
        for _ in range(4):
            self.browser.navigate_match(1)
            cursor = self.browser.textCursor()
            self.assertEqual(cursor.selectedText(), "cobalt")
            positions.append(cursor.selectionStart())
        self.assertEqual(len(set(positions)), 4)
        self.browser.anchorClicked.emit(QUrl("ets-answer:0"))
        self.assertIn("Additional original reference", self.browser.toPlainText())
        self.assertEqual(self.browser.match_count, 4)

    def test_search_positions_in_option_columns_preserve_unicode_and_rich_text(self):
        self.show(options=(Option("A", "前面😀 <b>Straße</b> 与 中文。STRASSE"),
                           Option("B", "<p>Another STRASSE</p>")))
        self.browser.set_search("STRASSE 中文")
        self.assertEqual(self.browser.match_count, 4)
        selected = []
        for _ in range(4):
            self.browser.navigate_match(0 if not selected else 1)
            selected.append(self.browser.textCursor().selectedText())
        self.assertEqual(selected, ["Straße", "中文", "STRASSE", "STRASSE"])

    def test_images_in_option_body_fit_the_remaining_column_on_resize(self):
        image = QImage(1200, 600, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        self.assertTrue(image.save(str(self.root / "option.png")))
        self.show(options=(Option("A", '<img src="option.png">'), Option("B", "Original text")))
        for width in (600, 400):
            self.browser.resize(width, 620)
            self.app.processEvents()
            document = self.browser.document()
            self.assertLessEqual(document.size().width(), self.browser.viewport().width() + 1)
            images = [fmt.toImageFormat() for fmt in document.allFormats() if fmt.isImageFormat()]
            self.assertEqual(len(images), 1)
            self.assertAlmostEqual(images[0].height() / images[0].width(), 0.5, delta=0.01)
            self.assertFalse(document.resource(QTextDocument.ResourceType.ImageResource,
                                               QUrl(images[0].name())).isNull())
    def test_formatting_reflows_without_rebuild_and_keeps_search_selection_and_theme(self):
        self.show("<p>M: " + "cobalt conversation " * 18 + "</p><p>W: A reply.</p>",
                  (Option("A", "cobalt " * 35), Option("B", "Other original option")))
        self.browser.set_search("cobalt")
        self.browser.anchorClicked.emit(QUrl("ets-answer:0"))
        self.browser.setTextCursor(self.cursor("Other original option"))
        changes = QSignalSpy(self.browser.document().contentsChanged)
        text = self.browser.toPlainText()
        self.browser.resize(600, 620)
        self.app.processEvents()
        self.assertEqual(changes.count(), 0)
        self.assertEqual(self.browser.toPlainText(), text)
        self.assertIn(0, self.browser._expanded_answers)
        body = self.cursor("cobalt conversation")
        continuation_x = (self.browser.document().documentLayout().blockBoundingRect(body.block()).left()
                          + body.block().layout().lineAt(1).x())
        self.assertAlmostEqual(continuation_x, self.browser.cursorRect(body).left(), delta=1)
        count = self.browser.match_count
        self.browser.set_theme(LIGHT_COLORS)
        self.assertEqual(self.browser.toPlainText(), text)
        self.assertEqual(self.browser.match_count, count)


if __name__ == "__main__":
    unittest.main()
