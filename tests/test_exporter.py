from pathlib import Path
import unittest

from core.exporter import export_filename, format_answers, write_answers
from core.parser import plain_text
from core.scanner import scan_bank
from tests.bank import BANK, temporary_bank


class ExportFilenameTests(unittest.TestCase):
    def test_windows_filename_rules_and_unicode(self):
        cases = {
            '试卷 <一>:"/\\|?*\x00\x1f. ': '试卷 _一__________.txt',
            '试卷 2026': '试卷 2026.txt',
            'CON': '_CON.txt',
            'com1.paper': '_com1.paper.txt',
            'LPT²': '_LPT².txt',
            '... ': 'ETS-answers.txt',
        }
        for original, expected in cases.items():
            with self.subTest(original=original):
                self.assertEqual(export_filename(original), expected)


@unittest.skipUnless(BANK.is_dir(), 'Set ETS_TEST_BANK to the inspected ETS bank')
class AnswerExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = scan_bank(BANK / '586944')

    def test_entire_real_paper_and_only_first_answer_per_part(self):
        text = format_answers('586944', self.result.questions)
        self.assertIn('Task: 586944', text)
        self.assertEqual(text.count('Question group: '), 12)
        self.assertEqual(text.count('\nAnswer:\n'), 32)
        self.assertIn('Why is the woman surprised?', text)
        self.assertIn('The man has learned names quickly.', text)
        self.assertIn('\nC\n', text)
        self.assertIn('Answer not found', text)
        self.assertIn('=' * 72, text)
        self.assertIn('-' * 72, text)
        for question in self.result.questions:
            self.assertIn(f'Question group: {question.id}', text)
            for part in question.parts:
                if part.answers:
                    self.assertIn(plain_text(part.answers[0].text), text)
                if len(part.answers) > 1:
                    self.assertNotIn(plain_text(part.answers[-1].text), text)
        self.assertNotIn('</br>', text)

    def test_utf8_export_and_atomic_replacement_of_an_existing_export(self):
        with temporary_bank() as temporary:
            destination = Path(temporary) / '英语试卷.txt'
            write_answers(destination, '英语试卷', self.result.questions, bank_root=Path(temporary))
            text = destination.read_text(encoding='utf-8-sig')
            self.assertIn('Task: 英语试卷', text)
            self.assertEqual(text.count('\nAnswer:\n'), 32)
            write_answers(destination, '英语试卷', self.result.questions[:1], bank_root=Path(temporary))
            self.assertEqual(destination.read_text(encoding='utf-8-sig').count('\nAnswer:\n'), 5)
            self.assertEqual(list(Path(temporary).iterdir()), [destination])

    def test_io_error_is_reported_and_original_bank_files_cannot_be_overwritten(self):
        with temporary_bank() as temporary:
            root = Path(temporary)
            with self.assertRaises(OSError):
                write_answers(root / 'missing' / 'paper.txt', '586944', self.result.questions, bank_root=root)
            original = root / 'content.json'
            source = self.result.questions[0].source.read_bytes()
            original.write_bytes(source)
            with self.assertRaises(PermissionError):
                write_answers(original, '586944', self.result.questions, bank_root=root)
            self.assertEqual(original.read_bytes(), source)


if __name__ == '__main__':
    unittest.main()
