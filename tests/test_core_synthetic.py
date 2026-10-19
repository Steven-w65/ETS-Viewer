"""Core regression coverage that runs without distributing a private ETS bank."""

import copy
import json
from pathlib import Path
import unittest

from core.exporter import format_answers, write_answers
from core.parser import UnsupportedFormat, parse_document
from core.scanner import scan_bank
from tests.bank import temporary_bank


def choice_record():
    return {"structure_type": "collector.choose", "info": {"stid": "synthetic", "xtlist": [
        {"xt_xh": "1", "xt_nr": "ets_th1 Choose the river.", "answer": "B", "xxlist": [
            {"xx_mc": "A", "xx_nr": "A hill"}, {"xx_mc": "B", "xx_nr": "A river"}]},
        {"xt_xh": "2", "xt_nr": "Choose another response.", "xxlist": [
            {"xx_mc": "A", "xx_nr": "Original option"}]}]}}


class SyntheticCoreTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(temporary_bank()))
        self.source = self.root / "paper" / "content_synthetic" / "content.json"
        self.source.parent.mkdir(parents=True)

    def test_choice_metadata_joins_ids_without_overriding_explicit_answers(self):
        record = choice_record()
        original = copy.deepcopy(record)
        metadata = [{"code_id": "answer", "code_json_array": json.dumps([
            {"stid": "other", "xth": "2", "answer": "D"},
            {"stid": "synthetic", "xth": "2", "answer": "A"},
            {"stid": "synthetic", "xth": "1", "answer": "C"}])}]
        question = parse_document(record, self.source, metadata)
        self.assertEqual([part.answers[0].text for part in question.parts], ["B", "A"])
        self.assertEqual(question.parts[0].prompt, "Choose the river.")
        self.assertEqual([(o.label, o.text) for o in question.parts[0].options],
                         [("A", "A hill"), ("B", "A river")])
        self.assertEqual(record, original)
        self.assertEqual(parse_document(record, self.source).parts[1].answers, ())

    def test_dialogue_reference_order_and_reading_absence_are_preserved(self):
        question = parse_document({"structure_type": "collector.dialogue", "info": {
            "stid": "dialogue", "question": [{"xh": "1", "ask": "Original prompt",
                "answer": "Explicit", "std": [{"value": "First standard"}],
                "ref": [{"value": "Other reference"}]}]}}, self.source)
        self.assertEqual([a.text for a in question.parts[0].answers],
                         ["Explicit", "First standard", "Other reference"])
        for kind in ("collector.read", "collector.word"):
            reading = parse_document({"structure_type": kind, "info": {
                "stid": "reading", "value": "Original passage", "translate": "原文翻译",
                "ai": "Evaluator text", "answer": "Not a reference field"}}, self.source)
            self.assertEqual(reading.parts[0].answers, ())
            self.assertEqual(reading.parts[0].translation, "原文翻译")
        with self.assertRaises(UnsupportedFormat):
            parse_document({"structure_type": "collector.unknown"}, self.source)

    def test_scanner_uses_valid_alternate_without_modifying_bank_files(self):
        self.source.write_bytes(b'{"info":')
        alternate = self.source.with_name("content2.json")
        alternate.write_text(json.dumps(choice_record()), encoding="utf-8")
        before = {path: path.read_bytes() for path in (self.source, alternate)}
        result = scan_bank(self.root)
        self.assertEqual(len(result.questions), 1)
        self.assertEqual(result.questions[0].source, alternate)
        self.assertEqual(result.questions[0].parts[0].answers[0].text, "B")
        self.assertTrue(any(issue.path == self.source for issue in result.issues))
        self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_scanner_reports_unsupported_data_and_honors_cancellation(self):
        (self.root / "store.db").write_bytes(b"SQLite format 3\x00" + bytes(64))
        (self.root / "unknown.dat").write_bytes(bytes(range(64)))
        result = scan_bank(self.root)
        self.assertEqual(result.questions, ())
        self.assertEqual(len(result.issues), 2)
        self.assertTrue(any("SQLite" in issue.message for issue in result.issues))
        cancelled = scan_bank(self.root, cancelled=lambda: True)
        self.assertTrue(cancelled.cancelled)
        self.assertEqual(cancelled.questions, ())

    def test_export_preserves_options_first_answers_and_explicit_absence(self):
        choice = parse_document(choice_record(), self.source)
        dialogue = parse_document({"structure_type": "collector.dialogue", "info": {
            "stid": "dialogue", "question": [{"xh": "1", "ask": "Original prompt",
                "std": [{"value": "First reference"}, {"value": "Additional reference"}]}]}}, self.source)
        text = format_answers("试卷", (choice, dialogue))
        self.assertEqual(text.count("\nAnswer:\n"), 3)
        self.assertIn("A. A hill\nB. A river", text)
        self.assertIn("\nB\n", text)
        self.assertIn("Answer not found", text)
        self.assertIn("First reference", text)
        self.assertNotIn("Additional reference", text)

    def test_export_atomic_replacement_and_source_protection(self):
        self.source.write_text(json.dumps(choice_record()), encoding="utf-8")
        original = self.source.read_bytes()
        question = parse_document(choice_record(), self.source)
        with self.assertRaises(PermissionError):
            write_answers(self.source, "试卷", (question,), bank_root=self.root)
        self.assertEqual(self.source.read_bytes(), original)
        destination = self.root / "试卷.txt"
        write_answers(destination, "试卷", (question,), bank_root=self.root)
        self.assertTrue(destination.read_bytes().startswith(b"\xef\xbb\xbf"))
        write_answers(destination, "替换试卷", (), bank_root=self.root)
        self.assertIn("Task: 替换试卷", destination.read_text(encoding="utf-8-sig"))
        self.assertNotIn("Question group:", destination.read_text(encoding="utf-8-sig"))
        self.assertEqual(self.source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
