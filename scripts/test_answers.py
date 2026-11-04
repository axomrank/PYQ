"""Validate solution annotations without changing the dataset."""
import json
import unittest

import answers


class AnswerTests(unittest.TestCase):
    def test_existing_official_answer_is_preserved(self):
        question = dict(id="ADRE-2022-G3-P3-HSSLC-Q001", exam="ADRE", year=2022,
                        paper_code="G3-P3-HSSLC", q_no=1, answer="A",
                        answer_source="official_final_key", answer_note="Original key")
        answers.apply_solution(question)
        self.assertEqual((question["answer"], question["answer_source"], question["answer_note"]),
                         ("A", "official_final_key", "Original key"))

    def test_solution_and_issue_references_exist_and_do_not_overlap(self):
        questions = [q for p in (answers.ROOT / "exams").glob("*/*/data/*.json")
                     for q in json.loads(p.read_text(encoding="utf-8"))]
        ids = {q["id"] for q in questions}
        keys = {(q["exam"], int(q["year"]), q["paper_code"], q["q_no"]) for q in questions}
        self.assertFalse(answers.load_solutions().keys() - keys)
        self.assertFalse(answers.load_issues().keys() - ids)
        self.assertFalse(answers.load_corrections().keys() - ids)
        for question in questions:
            if question["id"] in answers.load_issues() and question["id"] not in answers.load_revisions():
                self.assertFalse(question["answer"])
                self.assertIn("Unresolved question issue:", question["answer_note"])
            if question["answer_source"] == "worked_solution":
                self.assertTrue(question["answer_note"])
                for letter in question["answer"].split(","):
                    self.assertIn(letter, question["options"])


    def test_editorial_revisions_have_answers_and_originals(self):
        revisions = answers.load_revisions()
        self.assertEqual(set(revisions), set(answers.load_issues()))
        questions = {q['id']: q for p in (answers.ROOT / 'exams').glob('*/*/data/*.json')
                     for q in json.loads(p.read_text(encoding='utf-8'))}
        for qid, revision in revisions.items():
            with self.subTest(question=qid):
                question = questions[qid]
                self.assertTrue(revision['original']['question'])
                self.assertTrue(revision['original_issue'])
                self.assertEqual(len(set(question['options'].values())), 4)
                self.assertIn(question['answer'], 'ABCD')
                self.assertEqual(question['answer'], revision['revised']['answer'])
                self.assertEqual(question['answer_source'], 'editorial_solution')
                self.assertTrue(question['editorially_revised'])
                self.assertFalse(question['images'])
                before = json.dumps(question, sort_keys=True)
                answers.apply_solution(question)
                self.assertEqual(json.dumps(question, sort_keys=True), before)

    def test_revised_numerical_answers(self):
        from fractions import Fraction
        from math import gcd, lcm
        a, b = 2**7, 2**5 * 7**3
        self.assertEqual((gcd(a,b), lcm(a,b)), (2**5,2**7*7**3))
        self.assertEqual(Fraction(5,100) * 2**8, Fraction(128,10))
        self.assertEqual(round((6*500+17*600+6*850)/29,2), 631.03)


if __name__ == "__main__":
    unittest.main()
