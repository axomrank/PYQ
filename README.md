# PYQ — Assam recruitment exam question bank

Previous-year question papers (PYQs) of Assam recruitment exams: the original PDFs
plus machine-readable JSON/CSV. Every question has its four options, the answer
key where one is available, and an image of the question whenever it cannot be
represented as plain text. English only.

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
  <YEAR>/data/<paper>.json | .csv           one record per question
  <YEAR>/figures/<paper>/qNNN.png           images of questions (see below)
  <EXAM>_all.json | .csv                    every paper of the exam combined
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
