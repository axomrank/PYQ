# PYQ — Assam recruitment exam question bank

Previous-year question papers (PYQs) of Assam recruitment exams: the original PDFs
plus machine-readable JSON/CSV. Every question has its four options, the answer
key where one is available, and an image of the question whenever it cannot be
represented as plain text. English only.

**Start with [questions.csv](questions.csv)** — all 2,398 extracted questions from
23 papers across six exams, with subject/topic/subtopic hierarchy, answer keys,
review flags, and paths to the source PDFs and question images. Filter `exam`,
`year`, and `paper_code` to select a paper. CSV booleans use `0`/`1`; list fields
use `|` separators (`review_notes` uses ` ; `). Empty answers mean no key is available.

| Exam | Papers | Source of the booklets | Answer key |
|------|--------|------------------------|------------|
| ADRE (SLRC Grade III & IV) | 2022, 2024: 10 papers | SLRC booklets | 2024 official final; 2022 none |
| APSC CCE Prelims | 2023–2025: GS-I and GS-II | apsc.nic.in (scanned, OCR) | official provisional |
| Assam Police Constable | CWT-2025 Paper II | slprbassam.in | official provisional |
| Assam Police SI | CWT-2025 Paper I | slprbassam.in | official final |
| Assam TET | 2021 Paper I, Paper II, Special TET Paper I | AglaSem Docs (unofficial copies) | none |
| DME (Grade III) | 2025 Technical, Non-Technical (partial scans: Q1–50 with gaps) | third-party copies (scanned, OCR) | none |

`catalog.json` lists every extracted paper with its counts and any question numbers that could not be read (`missing_q_nos`); `scripts/booklets.json`
records each paper's title, conducting body, source URL and key status.

## Layout

```
exams/<EXAM>/
  <YEAR>/source/<paper>.pdf                 question booklet
  <YEAR>/source/answer_keys/<paper>_key.pdf official key (or a hand-made <paper>_key.csv)
  <YEAR>/data/<paper>.json                 canonical records for one paper
  <YEAR>/figures/<paper>/qNNN.png           images of questions (see below)
questions.csv         unified export of all papers; one row per question ID
scripts/extract.py     extractor (text PDFs and, via scripts/ocr.py, scanned PDFs)
scripts/booklets.json  per-paper metadata
catalog.json
```

## Question record

| Field | Meaning |
|-------|---------|
| `id` | `<EXAM>-<YEAR>-<paper_code>-Q<NNN>` |
| `exam`, `year`, `conducting_body`, `paper_code`, `paper_title`, `booklet_set`, `q_no` | identity; `q_no` is the number in the booklet set stored here |
| `context` | shared instruction or reading passage for this question (e.g. "Q. 71-80 : Read the passage…"), else null |
| `question`, `options` (`A`–`D`) | English text; lowercase `(a)`–`(d)` labels are normalised to A–D |
| `answer` | `A`–`D`; `A,C` when the key accepts several; `ALL` when the key graced the question; null without a key |
| `answer_note`, `answer_source` | why an answer is not a single letter; `official_final_key`, `official_provisional_key` or a key CSV's source |
| `has_visual`, `visual_types`, `visual_in`, `images` | see below |
| `text_source` | `pdf_text` (exact text from the PDF) or `ocr` (read from a scanned page; check against the image) |
| `source_pdf`, `page` | where the question is in the PDF (1-based page) |
| `needs_review`, `review_notes` | the extractor was unsure |
| `subject`, `topic`, `subtopic` | broad subject → subject area → specific question category |
| `topic_path` | the complete hierarchy joined with ` > `, e.g. `History > Assam History > Ahom rulers` |
| `classification_needs_review`, `classification_note` | classification needs checking against the question image or context; separate from extraction review |

## Subject hierarchy

Every extracted question has a curated three-level classification in the unified
CSV and per-paper JSON. For example:

| Subject | Topic | Subtopic |
|---------|-------|----------|
| History | Assam History | Ahom rulers |
| Mathematics | Arithmetic | Percentage |
| English | Grammar | Tenses |
| Pedagogy | Child Development | Adolescence |

`scripts/taxonomy.json` defines the subject/topic pairs. Files under
`scripts/labels/` assign a topic code and descriptive subtopic to each booklet
question number, using `q_no|code|subtopic` (inclusive ranges such as `61-64` are
supported). An optional fourth field records a classification review note.
Labels describe the tested subject rather than asserting the answer or updating
historical current-affairs facts. They are content annotations, not official
exam-board subject labels. Image-dependent, incomplete, or merged extractions may
need a classification check; see `classification_needs_review` and its note.

After editing labels, refresh all existing exports without PDF/OCR dependencies:

```bash
python scripts/taxonomy.py
python scripts/taxonomy.py --check
python -m unittest discover -s scripts -p 'test_*.py'
```

The exporter also attaches the labels on future extraction runs. Questions added
without a curated label receive empty hierarchy fields and a classification
review note; the coverage check fails until labels have been supplied.

The per-paper JSON files are the canonical data. `questions.csv` is the only
question CSV; redundant per-paper CSVs and per-exam combined exports are omitted.
`catalog.json` points each paper to its JSON and to the unified CSV. Rebuilding the
CSV reads only canonical records, so combined copies cannot duplicate questions.

## Questions with visual content

Figures, equations, symbol pictures and tables cannot be stored as text.

* **Text PDFs** (ADRE, Police, TET): figures are detected from the PDF itself
  (embedded images, vector drawings, equation fonts, picture options, columnar
  layout). Such questions get `has_visual: true`, their `visual_types`, and a PNG
  crop of the whole question in `images`.
* **Scanned PDFs** (APSC, DME): a page is a single picture, so figures cannot be
  told apart from text reliably and OCR may misread words. **Every** question from
  a scanned paper therefore has its image in `images`; `has_visual` and
  `figure_gap` mark the ones where a figure was detected (a tall blank band
  inside the question).

Text fields always hold whatever text exists. Show the image whenever
`has_visual` is true, and use it to verify any `ocr` question.

## Notes

* Multilingual booklets (ADRE, Police, TET) print each question in several
  languages; only the English version, the authoritative one, is extracted.
  Language-subject questions with no English version (e.g. Special TET Part V,
  Q121–150) are left out.
* Answer keys: add `exams/<EXAM>/<YEAR>/source/answer_keys/<paper>_key.csv` with
  columns `q_no,answer[,source]` to fill missing answers, then re-run.

## Regenerating

```bash
uv venv .venv && uv pip install -r scripts/requirements.txt
.venv/bin/python scripts/extract.py exams/*/*/source/*.pdf
```

OCR results are cached in `.ocr_cache/` (not committed); the first OCR run takes
about 15 s per scanned page.
