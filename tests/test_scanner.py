import json
import os
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from core.scanner import scan_bank
from tests.bank import BANK, read_record, temporary_bank


@unittest.skipUnless(BANK.is_dir(), "Set ETS_TEST_BANK to the inspected ETS bank")
class ScannerTests(unittest.TestCase):
    def test_paper_date_uses_the_task_folder_mtime_even_when_opening_one_question_folder(self):
        _, data = read_record("586944/content_632481/content.json")
        modified = datetime(2024, 1, 2, 15, 4, 5)
        timestamp = modified.timestamp()
        with temporary_bank() as temporary:
            task = Path(temporary) / "task"
            content = task / "content_632481"
            content.mkdir(parents=True)
            source = content / "content.json"
            source.write_text(json.dumps(data), encoding="utf-8")
            os.utime(source, (timestamp - 86400, timestamp - 86400))
            os.utime(content, (timestamp + 86400, timestamp + 86400))
            os.utime(task, (timestamp, timestamp))
            for root in (Path(temporary), content):
                with self.subTest(root=root):
                    result = scan_bank(root)
                    self.assertEqual(getattr(result, "folder_modified", {}).get(task), modified)
                    self.assertEqual(task.stat().st_mtime, timestamp)

    def test_unreadable_task_timestamp_does_not_hide_its_questions(self):
        _, data = read_record("586944/content_632481/content.json")
        with temporary_bank() as temporary:
            task = Path(temporary) / "task"
            content = task / "content_632481"
            content.mkdir(parents=True)
            (content / "content.json").write_text(json.dumps(data), encoding="utf-8")
            original_stat = Path.stat

            def read_stat(path, *args, **kwargs):
                if path == task and kwargs.get("follow_symlinks", True):
                    raise PermissionError("Task timestamp unavailable")
                return original_stat(path, *args, **kwargs)

            with patch("core.scanner.Path.stat", new=read_stat):
                result = scan_bank(Path(temporary))
            self.assertEqual(len(result.questions), 1)
            self.assertIsNone(getattr(result, "folder_modified", {}).get(task))
            self.assertTrue(any(issue.path == task and "modification date" in issue.message for issue in result.issues))

    def test_complete_real_bank_has_no_duplicate_display_copies(self):
        result = scan_bank(BANK)
        original = [q for q in result.questions if q.source.parent.parent.name in {"392791", "581015", "586944"}]
        self.assertEqual(len(original), 36)
        parts = [p for q in original for p in q.parts]
        self.assertEqual(len(parts), 96)
        self.assertEqual(sum(not p.answers for p in parts), 9)
        self.assertEqual(sum(len(p.answers) for p in parts if p.options), 60)
        self.assertEqual(sum(len(p.answers) for p in parts if not p.options), 1189)
        self.assertGreaterEqual(result.files_scanned, 617)
        self.assertTrue(any("question ID" in issue.message for issue in result.issues))

    def test_known_template_metadata_is_not_reported_as_an_unknown_question(self):
        result = scan_bank(BANK)
        self.assertFalse(any(any(part.startswith("template_") for part in issue.path.parts)
                             for issue in result.issues))

    @unittest.skipUnless((BANK / "2674").is_dir(), "Additional real bank is not installed")
    def test_additional_actual_bank_uses_the_same_observed_schemas(self):
        result = scan_bank(BANK / "2674")
        parts = [part for question in result.questions for part in question.parts]
        self.assertEqual(len(result.questions), 7)
        self.assertEqual(len(parts), 12)
        self.assertEqual(sum(not part.answers for part in parts), 3)
        self.assertEqual(sum(len(part.answers) for part in parts), 110)

    def test_corrupt_primary_uses_display_copy_without_touching_inputs(self):
        _, data = read_record("586944/content_632481/content2.json")
        with temporary_bank() as temporary:
            folder = Path(temporary) / "nested" / "content_632481"
            folder.mkdir(parents=True)
            primary = folder / "content.json"
            primary.write_bytes(b'{"info":')
            alternate = folder / "content2.json"
            alternate.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            before = {p: p.read_bytes() for p in (primary, alternate)}
            result = scan_bank(Path(temporary))
            self.assertEqual(len(result.questions), 1)
            self.assertEqual(result.questions[0].source, alternate)
            self.assertEqual(result.questions[0].parts[0].answers[0].text, "C")
            self.assertTrue(result.issues)
            self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_format_is_identified_from_contents_without_json_extension(self):
        _, data = read_record("586944/content_632481/content.json")
        with temporary_bank() as temporary:
            folder = Path(temporary)
            (folder / "bank.dat").write_text(json.dumps(data), encoding="utf-8")
            result = scan_bank(folder)
            self.assertEqual(len(result.questions), 1)
            self.assertEqual(result.questions[0].id, "632481")

    def test_corrupt_supplemental_metadata_keeps_explicit_primary_answers(self):
        _, data = read_record("586944/content_632481/content.json")
        _, metadata = read_record("586944/content_632481/info.json")
        metadata[1]["code_json_array"] = "not JSON"
        with temporary_bank() as temporary:
            folder = Path(temporary)
            (folder / "content.json").write_text(json.dumps(data), encoding="utf-8")
            (folder / "info.json").write_text(json.dumps(metadata), encoding="utf-8")
            result = scan_bank(folder)
            self.assertEqual(len(result.questions), 1)
            self.assertEqual(result.questions[0].parts[0].answers[0].text, "C")
            self.assertTrue(result.issues)

    def test_unsupported_database_and_unknown_binary_are_reported(self):
        with temporary_bank() as temporary:
            folder = Path(temporary)
            (folder / "store.db").write_bytes(b"SQLite format 3\x00" + bytes(64))
            (folder / "locked.dat").write_bytes(bytes(range(64)))
            result = scan_bank(folder)
            self.assertEqual(result.questions, ())
            self.assertEqual(len(result.issues), 2)
            self.assertTrue(any("SQLite" in issue.message for issue in result.issues))
            self.assertTrue(any("unsupported" in issue.message.lower() for issue in result.issues))

    def test_incomplete_primary_parts_use_the_complete_alternate_copy(self):
        records = (
            ("586944/content_632481", "xtlist"),
            ("586944/content_632489", "question"),
            ("586944/content_632488", None),
            ("586944/content_632490", None),
        )
        for relative, parts_key in records:
            with self.subTest(record=relative):
                _, primary = read_record(relative + "/content.json")
                _, alternate = read_record(relative + "/content2.json")
                if parts_key:
                    primary["info"][parts_key][0] = {}
                else:
                    primary["info"] = {"stid": primary["info"]["stid"]}
                with temporary_bank() as temporary:
                    folder = Path(temporary)
                    (folder / "content.json").write_text(json.dumps(primary), encoding="utf-8")
                    alternate_path = folder / "content2.json"
                    alternate_path.write_text(json.dumps(alternate), encoding="utf-8")
                    result = scan_bank(folder)
                    self.assertEqual(len(result.questions), 1)
                    self.assertEqual(result.questions[0].source, alternate_path)
                    self.assertTrue(result.issues)

    def test_same_ids_in_separate_bank_directories_remain_distinct(self):
        _, data = read_record("586944/content_632481/content.json")
        with temporary_bank() as temporary:
            root = Path(temporary)
            for name in ("one", "two"):
                folder = root / name
                folder.mkdir()
                (folder / "content.json").write_text(json.dumps(data), encoding="utf-8")
            result = scan_bank(root)
            self.assertEqual(len(result.questions), 2)

    def test_invalid_folder_and_interruption_finish_cleanly(self):
        with temporary_bank() as temporary:
            with self.assertRaises(NotADirectoryError):
                scan_bank(Path(temporary) / "absent")
        result = scan_bank(BANK, cancelled=lambda: True)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.questions, ())


if __name__ == "__main__":
    unittest.main()
