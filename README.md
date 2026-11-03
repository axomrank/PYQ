# PYQ — Assam recruitment exam question bank

Previous-year question papers (PYQs) for Assam government recruitment exams, as the
original PDFs plus machine-readable JSON/CSV. Every question has its four
options, the official answer key where one exists, and a pointer to an image when
the question cannot be represented as plain text.

Languages: **English** and **Assamese** only. The booklets also carry Bengali,
Bodo and Hindi versions; those are deliberately not extracted.

| Exam | Status |
|------|--------|
| ADRE (SLRC Grade III & IV) | 2022 + 2024, all 10 papers extracted |
| DME | planned |
| TET | planned |
| APSC (Prelims) | planned |
| Assam Police Constable | planned |
| Assam Police SI | planned |

`catalog.json` lists every extracted paper with its counts.

## Layout

```
exams/
  <EXAM>/
    <YEAR>/
      source/                 original question booklets (PDF)
        answer_keys/          official keys (<paper>_key.pdf) or hand-entered <paper>_key.csv
      data/<paper>.json       one record per question (full detail)
      data/<paper>.csv        same, flattened for spreadsheets
      figures/<paper>/        cropped images for questions with visual content
    <EXAM>_all.json / .csv    every paper of the exam combined
    legacy/                   older hand-made CSVs kept for reference (not authoritative)
scripts/
  extract_slrc.py             extractor for SLRC (ADRE) booklets
  booklets.json               per-paper metadata (question count, language order, set)
catalog.json
```

Paper codes: `G3-P3-HSSLC`, `G3-P4-Degree`, `G3-P5-Driver` (Grade III) and
`G4-P1-HSLC`, `G4-P2-ClassVIII` (Grade IV). IDs look like
`ADRE-2024-G4-P1-HSLC-Q042`.

## Question record

| Field | Meaning |
|-------|---------|
| `id`, `exam`, `year`, `grade`, `paper`, `paper_title`, `booklet_set`, `q_no` | identity; `q_no` is the number in booklet set A |
| `context_en` | shared instruction or reading passage that applies to this question (e.g. "Q. 71-80 : Read the passage…"), else null |
| `question_en`, `options_en` (`A`–`D`) | English text, the authoritative version per the booklet instructions |
| `context_as`, `question_as`, `options_as` | Assamese text in Unicode (see *Assamese* below) |
| `answer` | `A`–`D`; `A,C` when the key accepts several; `ALL` when the key graced the question (`G`); null when no key is available |
| `answer_note`, `answer_source` | explanation of non-single answers; `official_final_key` or the source named in a key CSV |
| `has_visual`, `visual_types`, `visual_in`, `images` | see below |
| `source_pdf`, `page_en`, `page_as` | where to find the question in the PDF (1-based pages) |
| `needs_review`, `review_notes` | the extractor was unsure; check against the image/PDF |

CSV columns flatten `options_*` into `option_a_en` … `option_d_as`; list
fields are joined with `|`.

## Questions with visual content

Figures, equations, symbol images and tabular layouts cannot be stored as text. For
every such question the extractor:

1. sets `has_visual: true` and records what was found in `visual_types`:
   `image` (raster figure), `drawing` (vector figure, fraction bar, radical,
   underline), `math` (equation fonts), `symbol_image` (an operator printed as a
   picture, e.g. `72 ■ 96`), `option_images` (options are pictures, so their text
   is empty), `table_layout` (columnar text), `text_mentions_visual` (the
   wording refers to a figure), and `context_*` when the shared passage or
   instruction has one;
2. crops the English block (question with its options) from the PDF at 144 dpi
   into `figures/<paper>/qNNN.png` (`qNNN_1.png`, `qNNN_2.png` when it spans a
   page break, `context_qNNN-MMM.png` for shared figures) and lists the paths in
   `images`.

Text fields are still filled with whatever text exists. For equations the text is
approximate (stacked fractions come out flattened), so those questions are also
marked `needs_review`. Show the image whenever `has_visual` is true.

## Answer keys

* **2024**: official final keys from SLRC, merged automatically.
* **2022**: no official key is in the repository, so `answer` is empty. To add one, put
  `exams/ADRE/2022/source/answer_keys/<paper>_key.csv` with columns
  `q_no,answer[,source]` and re-run the extractor.

## Assamese

The booklets use legacy (pre-Unicode) Assamese fonts: *Geetanjali* (2024 Paper
I, IV, V), *Shree-Lipi* (2024 Paper II, III), and fonts with no character map at
all (2022). Copy-pasting from the PDF gives garbage. The extractor locates each
question's Assamese version and keeps its raw glyph text in the JSON field
`_as_legacy` (`stem`, `options`, `context`, `fonts`, `page`). Converting that to
Unicode, which fills `question_as`, `options_as` and `context_as`, is a separate
step that has not been done yet. Questions from the English-language section have
no Assamese version.

## Regenerating

```bash
uv venv .venv && uv pip install -r scripts/requirements.txt
.venv/bin/python scripts/extract_slrc.py exams/ADRE/*/source/G*.pdf
```

Output is deterministic, so a re-run only changes files when the PDFs, keys or
extractor change. Other exams will get their own extractor (booklet layouts
differ) but must write the same record format into the same folder layout.
