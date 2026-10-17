import copy
import json
import unittest

from core.parser import UnsupportedFormat, parse_document
from tests.bank import BANK, read_record


@unittest.skipUnless(BANK.is_dir(), "Set ETS_TEST_BANK to the inspected ETS bank")
class ParserTests(unittest.TestCase):
    def test_choice_pairs_real_options_and_answer(self):
        path, data = read_record("586944/content_632481/content.json")
        question = parse_document(data, path)
        self.assertEqual(question.id, "632481")
        self.assertEqual(len(question.parts), 5)
        part = question.parts[0]
        self.assertEqual(part.prompt, "Why is the woman surprised?")
        self.assertIn("You've only been here for two months", part.context)
        self.assertEqual(part.answers[0].text, "C")
        self.assertEqual(part.answers[0].field, "info.xtlist[0].answer")
        self.assertEqual(part.options[2].label, "C")
        self.assertEqual(part.options[2].text.strip(), "The man has learned names quickly.")

    def test_dialogue_preserves_every_stored_reference(self):
        path, data = read_record("586944/content_632489/content.json")
        question = parse_document(data, path)
        self.assertEqual(len(question.parts), 2)
        self.assertEqual([len(p.answers) for p in question.parts], [52, 51])
        self.assertEqual(
            question.parts[0].answers[0].text,
            "What was your favorite place that you visited in Italy? Did you try any special Italian food during your trip?</br>",
        )
        self.assertIn("managing stress", question.parts[1].prompt)
        self.assertEqual(question.parts[1].answers[0].field, "info.question[1].std[0].value")

    def test_picture_keeps_image_keypoints_and_original_explanation(self):
        path, data = read_record("586944/content_632490/content.json")
        question = parse_document(data, path)
        self.assertEqual(question.title, "Shared umbrellas.")
        self.assertEqual(question.images, ("content.jpg",))
        self.assertEqual(len(question.parts[0].answers), 6)
        self.assertIn("Xiao Ming", question.parts[0].answers[0].text)
        self.assertEqual(question.explanation, "<p>It started to rain after school.</p>")
        self.assertIn("scanned the QR code", question.key_points)

    def test_reading_text_and_evaluator_fields_never_become_answers(self):
        path, data = read_record("586944/content_632488/content.json")
        data = copy.deepcopy(data)
        data["info"]["ai"] = data["info"]["value"]
        question = parse_document(data, path)
        self.assertIn("Earworms", question.parts[0].prompt)
        self.assertEqual(question.parts[0].answers, ())

    def test_missing_choice_answer_is_not_guessed_from_options(self):
        path, data = read_record("586944/content_632481/content.json")
        del data["info"]["xtlist"][0]["answer"]
        question = parse_document(data, path)
        self.assertEqual(question.parts[0].answers, ())
        self.assertEqual(question.parts[1].answers[0].text, "C")

    def test_metadata_answers_join_by_group_and_part_id(self):
        path, data = read_record("586944/content_632481/content.json")
        _, metadata = read_record("586944/content_632481/info.json")
        _, other_metadata = read_record("586944/content_632482/info.json")
        for part in data["info"]["xtlist"]:
            part.pop("answer")
        answers = json.loads(metadata[1]["code_json_array"])
        other = json.loads(other_metadata[1]["code_json_array"])
        metadata[1]["code_json_array"] = json.dumps(other + list(reversed(answers)))
        question = parse_document(data, path, metadata=metadata)
        self.assertEqual([p.answers[0].text for p in question.parts], ["C", "C", "B", "B", "C"])
        self.assertIn("info.json", question.parts[0].answers[0].field)

    def test_choice_metadata_wrapper_is_a_real_fallback(self):
        path, data = read_record("586944/content_632481/info.json")
        question = parse_document(data, path)
        self.assertEqual(question.id, "632481")
        self.assertEqual(len(question.parts), 5)
        self.assertEqual(question.parts[0].answers[0].text, "C")

    def test_unknown_collector_is_reported(self):
        path, data = read_record("586944/content_632481/content.json")
        data["structure_type"] = "collector.unsupported"
        with self.assertRaisesRegex(UnsupportedFormat, "collector.unsupported"):
            parse_document(data, path)


if __name__ == "__main__":
    unittest.main()
