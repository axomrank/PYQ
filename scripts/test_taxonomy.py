"""Run with: python -m unittest discover -s scripts -p 'test_taxonomy.py'."""
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import taxonomy


class TaxonomyTests(unittest.TestCase):
    def question(self, **changes):
        question = {
            "exam": "ADRE", "year": 2022, "paper_code": "G3-P3-HSSLC",
            "q_no": 1, "question": "Which colour is not present in the Olympic Flag?",
            "needs_review": False,
            "id": "ADRE-2022-G3-P3-HSSLC-Q001", "options": {},
        }
        return question | changes

    def test_known_question_and_csv_boolean(self):
        result = taxonomy.classify(self.question())
        self.assertEqual(result["topic_path"], "General Awareness > Sports > Olympics")
        self.assertFalse(result["classification_needs_review"])
        row = next(csv.DictReader(io.StringIO(taxonomy.export_csv([self.question()]))))
        self.assertEqual(row["classification_needs_review"], "0")
        self.assertEqual(row["classification_note"], "")

    def test_csv_round_trip_preserves_text_and_provenance(self):
        question = self.question(
            question='A "quoted", multiline\nquestion',
            options={"A": "0", "B": "A, B", "C": '"C"', "D": "two\nlines"},
            answer=None, source_pdf="exams/source.pdf", conducting_body="Board",
            visual_in=["question", "options"], images=["one.png", "two.png"],
            review_notes=["first note", "second note"],
        )
        row = next(csv.DictReader(io.StringIO(taxonomy.export_csv([question]))))
        self.assertEqual(row["question"], question["question"])
        for letter, value in question["options"].items():
            self.assertEqual(row[f"option_{letter.lower()}"], value)
        self.assertEqual(row["answer"], "")
        self.assertEqual(row["source_pdf"], "exams/source.pdf")
        self.assertEqual(row["conducting_body"], "Board")
        self.assertEqual(row["visual_in"], "question|options")
        self.assertEqual(row["images"], "one.png|two.png")
        self.assertEqual(row["review_notes"], "first note ; second note")

    def test_csv_rejects_duplicate_question_ids(self):
        with self.assertRaisesRegex(ValueError, "Duplicate question ID"):
            taxonomy.export_csv([self.question(), self.question()])

    def test_unknown_question_is_not_guessed(self):
        result = taxonomy.classify(self.question(year=2099))
        self.assertEqual([result[field] for field in taxonomy.FIELDS[:4]], [""] * 4)
        self.assertTrue(result["classification_needs_review"])
        self.assertIn("No curated label", result["classification_note"])

    def test_extraction_and_classification_review_remain_separate(self):
        question = self.question(question="", needs_review=True)
        result = taxonomy.classify(question)
        self.assertTrue(result["classification_needs_review"])
        self.assertIn("Question text is absent", result["classification_note"])
        self.assertIn("extracted question needs review", result["classification_note"])
        self.assertNotIn("classification_needs_review", question)

    def test_ranges_are_inclusive_and_overlaps_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            labels = root / "scripts/labels"
            labels.mkdir(parents=True)
            (root / "scripts/taxonomy.json").write_text(json.dumps({"T": ["Subject", "Topic"]}))
            path = labels / "EXAM__2024__Paper.txt"
            path.write_text("3-5|T|Subtopic\n")
            taxonomy.load_labels.cache_clear()
            try:
                with patch.object(taxonomy, "ROOT", root):
                    self.assertEqual({key[-1] for key in taxonomy.load_labels()}, {3, 4, 5})
                    path.write_text("3-5|T|Subtopic\n5|T|Overlap\n")
                    taxonomy.load_labels.cache_clear()
                    with self.assertRaisesRegex(ValueError, "duplicate label"):
                        taxonomy.load_labels()
            finally:
                taxonomy.load_labels.cache_clear()

    def test_saved_exports_have_complete_consistent_hierarchy(self):
        updates, questions = taxonomy.prepare_updates()
        self.assertTrue(questions)
        for path, expected in updates.items():
            with self.subTest(path=str(path)):
                self.assertEqual(path.read_bytes(), expected.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
