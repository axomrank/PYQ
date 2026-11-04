#!/usr/bin/env python3
"""Apply the curated question hierarchy without rerunning PDF extraction.

Usage: python scripts/taxonomy.py [--check]
Labels are keyed by exam, year, paper code, and booklet question number.
"""
import argparse
import csv
import io
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = [
    "subject", "topic", "subtopic", "topic_path",
    "classification_needs_review", "classification_note",
]
CSV_COLS = [
    "id", "exam", "year", "paper_code", "paper_title", "booklet_set", "q_no",
    "context", "question", "option_a", "option_b", "option_c", "option_d",
    "answer", "answer_note", "answer_source", "has_visual", "visual_types", "images", "text_source",
    "page", "needs_review", "review_notes", "conducting_body", "source_pdf", "visual_in",
] + FIELDS


@lru_cache(maxsize=1)
def load_labels():
    """Read and validate codes and inclusive question-number ranges once."""
    taxonomy = json.loads((ROOT / "scripts/taxonomy.json").read_text(encoding="utf-8"))
    if any(not isinstance(v, list) or len(v) != 2 or not all(v) for v in taxonomy.values()):
        raise ValueError("Each taxonomy code must define a subject and a topic")
    labels = {}
    for path in sorted((ROOT / "scripts/labels").glob("*.txt")):
        exam, year, paper = path.stem.split("__")
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) not in (3, 4):
                raise ValueError(f"{path}:{line_no}: expected q_no|code|subtopic[|review note]")
            numbers, code, subtopic = parts[:3]
            if code not in taxonomy or not subtopic:
                raise ValueError(f"{path}:{line_no}: unknown code or empty subtopic")
            bounds = [int(n) for n in numbers.split("-")]
            if len(bounds) not in (1, 2) or bounds[0] < 1 or bounds[-1] < bounds[0]:
                raise ValueError(f"{path}:{line_no}: invalid question-number range")
            for number in range(bounds[0], bounds[-1] + 1):
                key = (exam, int(year), paper, number)
                if key in labels:
                    raise ValueError(f"{path}:{line_no}: duplicate label for {key}")
                labels[key] = (*taxonomy[code], subtopic, parts[3] if len(parts) == 4 else "")
    return labels


def question_key(question):
    return (question["exam"], int(question["year"]), question["paper_code"], int(question["q_no"]))


def classify(question):
    """Return hierarchy fields; never guess a label for a newly added question."""
    label = load_labels().get(question_key(question))
    notes = []
    if label is None:
        subject = topic = subtopic = ""
        notes.append("No curated label yet; assign subject, topic and subtopic.")
    else:
        subject, topic, subtopic, note = label
        if note:
            notes.append(note)
    if not (question.get("question") or "").strip():
        notes.append("Question text is absent; verify the label against the image and context.")
    if question.get("needs_review"):
        notes.append("The extracted question needs review; verify its classification against the image.")
    return dict(zip(FIELDS, [
        subject, topic, subtopic,
        " > ".join((subject, topic, subtopic)) if subject else "",
        bool(notes), " ".join(notes),
    ]))


def export_csv(questions):
    """Flatten canonical JSON records into the single, deterministic CSV export."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_COLS, lineterminator="\n")
    writer.writeheader()
    seen = set()
    for question in questions:
        if question["id"] in seen:
            raise ValueError(f"Duplicate question ID: {question['id']}")
        seen.add(question["id"])
        row = {field: question.get(field) for field in CSV_COLS}
        row.update({f"option_{letter.lower()}": question["options"].get(letter, "") for letter in "ABCD"})
        row.update(classify(question))
        for field in ("has_visual", "needs_review", "classification_needs_review"):
            row[field] = int(bool(row[field]))
        for field in ("visual_types", "images", "visual_in"):
            row[field] = "|".join(question.get(field) or [])
        row["review_notes"] = " ; ".join(question.get("review_notes") or [])
        writer.writerow(row)
    return output.getvalue()


def prepare_updates():
    """Validate canonical records and prepare JSON, unified CSV, and catalog updates."""
    updates, by_id, keys = {}, {}, set()
    for path in sorted((ROOT / "exams").glob("*/*/data/*.json")):
        questions = json.loads(path.read_text(encoding="utf-8"))
        for question in questions:
            if question["id"] in by_id:
                raise ValueError(f"Duplicate question ID: {question['id']}")
            keys.add(question_key(question))
            question.update(classify(question))
            if not question["subject"]:
                raise ValueError(f"Missing label for {question['id']}")
            by_id[question["id"]] = question
        updates[path] = json.dumps(questions, ensure_ascii=False, indent=1) + "\n"
    if not by_id:
        raise ValueError("No per-paper JSON question files found")
    extra = load_labels().keys() - keys
    if extra:
        raise ValueError(f"Labels without extracted questions: {sorted(extra)}")

    updates[ROOT / "questions.csv"] = export_csv(by_id.values())
    catalog_path = ROOT / "catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    for paper in catalog:
        paper["csv"] = "questions.csv"
    updates[catalog_path] = json.dumps(catalog, indent=1) + "\n"
    return updates, by_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check saved hierarchy and coverage without writing")
    args = parser.parse_args()
    updates, questions = prepare_updates()
    changed = [path for path, content in updates.items() if not path.exists() or path.read_bytes() != content.encode("utf-8")]
    if args.check and changed:
        parser.exit(1, f"Hierarchy is missing or stale in {len(changed)} files. Run python scripts/taxonomy.py\n")
    if not args.check:
        for path in changed:
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(updates[path].encode("utf-8"))
            temporary.replace(path)
    subjects = Counter(q["subject"] for q in questions.values())
    review = sum(q["classification_needs_review"] for q in questions.values())
    print(f"{'Checked' if args.check else 'Updated'} {len(questions)} questions, "
          f"{len(updates) - 2} paper JSON files, one unified CSV, {len(subjects)} subjects; "
          f"{review} classifications flagged for review.")


if __name__ == "__main__":
    main()
