"""Curated worked solutions, kept separate from official examination keys."""
import re
import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def load_solutions():
    solutions = {}
    for path in sorted((ROOT / "scripts/solutions").glob("*.txt")):
        exam, year, paper = path.stem.split("__")
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) not in (3, 4) or not re.fullmatch(r"[A-D](?:,[A-D])*", parts[1]) or not parts[2].strip():
                raise ValueError(f"Invalid solution: {path}:{line_no}")
            key = (exam, int(year), paper, int(parts[0]))
            if key in solutions:
                raise ValueError(f"Duplicate solution: {key}")
            solutions[key] = (parts[1], parts[2] + (f" Source: {parts[3]}" if len(parts) == 4 else ""))
    return solutions


def apply_solution(question):
    correction = load_corrections().get(question["id"])
    if correction:
        question.update(correction)
    revision = load_revisions().get(question["id"])
    if revision:
        revised = revision["revised"]
        question.update(
            question=revised["question"], options=dict(revised["options"]), context="",
            answer=revised["answer"], answer_note=revised["explanation"],
            answer_source="editorial_solution", editorially_revised=True,
            revision_note=revision["revision_note"],
            text_source="editorial_revision",
            has_visual=False, visual_types=[], visual_in=[], images=[],
            needs_review=False, review_notes=[],
        )
        return
    key = (question["exam"], int(question["year"]), question["paper_code"], int(question["q_no"]))
    solution = load_solutions().get(key)
    if solution and (not question.get("answer") or question.get("answer_source") == "worked_solution"):
        question.update(answer=solution[0], answer_note=solution[1], answer_source="worked_solution")
    if not question.get("answer") and question["id"] in load_issues():
        question["answer_note"] = "Unresolved question issue: " + load_issues()[question["id"]]


@lru_cache(maxsize=1)
def load_issues():
    path = ROOT / "scripts/answer_issues.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@lru_cache(maxsize=1)
def load_corrections():
    path = ROOT / "scripts/question_corrections.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@lru_cache(maxsize=1)
def load_revisions():
    path = ROOT / "scripts/editorial_revisions.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


if __name__ == "__main__":
    import taxonomy
    taxonomy.main()
