#!/usr/bin/env python3
"""Extract MCQs from SLRC (ADRE Grade III/IV) question booklets.

The booklets are multilingual (English, Assamese, Bengali, Bodo, Hindi). Only
English and Assamese are kept. English text is extracted directly. Assamese is
set in legacy (non-Unicode) fonts, so its raw glyph text is stored with the
question and converted later by scripts/legacy_assamese.py.

Questions containing figures, equations, symbol images, tables or underlines
cannot be represented as plain text. They are flagged (has_visual) and the
English block is cropped to PNG under figures/<paper>/.

Usage: python scripts/extract_slrc.py exams/ADRE/2024/source/G4-P1-HSLC.pdf ...
"""
import csv
import json
import re
import shutil
import statistics
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent

PAPERS = {
    "G3-P3-HSSLC": ("III", "III", "HSSLC (Class XII) passed"),
    "G3-P4-Degree": ("III", "IV", "Graduate"),
    "G3-P5-Driver": ("III", "V", "Driver"),
    "G4-P1-HSLC": ("IV", "I", "HSLC (Class X) passed"),
    "G4-P2-ClassVIII": ("IV", "II", "Class VIII passed"),
}

BENG_FONTS = re.compile(r"Geetanjali|SHREE-ASS|SHREE-BAN", re.I)
DEVA_FONTS = re.compile(r"Chanakya|SHREE-DEV|GS-Gitu", re.I)
MATH_FONTS = re.compile(r"[0-9A-F]{4}o00$|^Symbol$|MT-Extra|Math")
FOOTER = re.compile(
    r"^(Paper\s*[-–]?\s*[IVX]+\s*/\s*[A-Z]|SLRC-20\d\d/|SPACE FOR ROUGH WORK|\d{1,3}$|_+$)"
    r"|P\s*\.\s*T\s*\.\s*O|Pa\s*per\s*[-–]\s*[IVX]+\s*/\s*[A-Z]"
)
VISUAL_WORDS = re.compile(
    r"\b(figure|figures|diagram|picture|image|graph|shown below|given below figure|mirror image|water image|venn|pie[- ]chart|bar[- ]chart|table)\b",
    re.I,
)
OPT = "ABCD"


def char_script(ch, font):
    if ch == "₹":
        return "latin"
    o = ord(ch)
    if 0xE600 <= o <= 0xE7FF:
        return "beng"  # 2022 booklets: Bengali-script glyphs (Assamese/Bengali)
    if 0xE000 <= o <= 0xE1FF:
        return "deva"  # 2022 booklets: Devanagari glyphs (Bodo/Hindi)
    if BENG_FONTS.search(font):
        return "beng"
    if DEVA_FONTS.search(font):
        return "deva"
    return "latin"


SUP = str.maketrans("0123456789+-−=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ⁿ")
SUB = str.maketrans("0123456789+-−=()", "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎")


def normalize_char(ch, font):
    """Map decorative/symbol-font glyphs to Unicode; None drops the glyph."""
    if re.search(r"Dingbat|Wingding", font):
        return None
    if "Symbol" in font and ord(ch) < 256:
        return SYMBOL_PUA.get(ord(ch), ch)
    if "Gitu" in font and ch == "R":
        return "₹"
    return ch


def mark_scripts(cs):
    """Rewrite small raised/lowered Latin digits as Unicode super/subscripts (x2 -> x²)."""
    lat = [c for c in cs if c[0].strip() and c[6] == "latin"]
    if not lat:
        return cs
    med = statistics.median(c[7] for c in lat)
    base = [(c[2] + c[4]) / 2 for c in lat if c[7] >= 0.9 * med]
    if not base:
        return cs
    bcy = statistics.median(base)
    out = []
    for c in cs:
        if c[6] == "latin" and c[0] in "0123456789+-−" and c[7] < 0.86 * med:
            cy = (c[2] + c[4]) / 2
            if cy < bcy - 0.12 * med:
                c = (c[0].translate(SUP),) + c[1:]
            elif cy > bcy + 0.12 * med:
                c = (c[0].translate(SUB),) + c[1:]
        out.append(c)
    return out


class Tok:
    __slots__ = ("text", "x0", "x1", "y0", "y1", "script", "font", "math")

    def __init__(self, chars):
        self.text = "".join(c[0] for c in chars)
        self.x0 = min(c[1] for c in chars)
        self.x1 = max(c[3] for c in chars)
        self.y0 = min(c[2] for c in chars)
        self.y1 = max(c[4] for c in chars)
        self.font = chars[0][5]
        scripts = [c[6] for c in chars if not c[0].isspace()]
        self.script = max(set(scripts), key=scripts.count) if scripts else "latin"
        self.math = any(MATH_FONTS.search(c[5]) or 0xF000 <= ord(c[0]) <= 0xF8FF for c in chars)

    def __repr__(self):
        return f"{self.text!r}@{self.x0:.0f}"


def page_rows(page):
    """Return rows (top to bottom) of tokens (left to right) built from chars."""
    chars = []
    for b in page.get_text("rawdict")["blocks"]:
        for l in b.get("lines", []):
            if abs(l["dir"][1]) > 0.1:  # skip rotated text (watermarks)
                continue
            for s in l["spans"]:
                for c in s["chars"]:
                    ch = c["c"]
                    x0, y0, x1, y1 = c["bbox"]
                    if not ch.strip() and ch != " ":
                        continue
                    ch = normalize_char(ch, s["font"])
                    if ch is None:
                        continue
                    chars.append((ch, x0, y0, x1, y1, s["font"], char_script(ch, s["font"]), s["size"]))
    # group normal-size chars into rows by centre line, then attach small chars
    # (superscripts/subscripts) to the row they overlap most
    chars.sort(key=lambda c: ((c[2] + c[4]) / 2, c[1]))
    page_med = statistics.median(c[7] for c in chars) if chars else 0
    big = [c for c in chars if c[7] >= 0.85 * page_med]
    small = [c for c in chars if c[7] < 0.85 * page_med]
    rows = []
    for c in big:
        cy = (c[2] + c[4]) / 2
        if rows and abs(cy - rows[-1]["cy"]) <= max(3.5, 0.45 * c[7]):
            rows[-1]["chars"].append(c)
            rows[-1]["y0"] = min(rows[-1]["y0"], c[2])
            rows[-1]["y1"] = max(rows[-1]["y1"], c[4])
        else:
            rows.append({"cy": cy, "chars": [c], "y0": c[2], "y1": c[4]})
    for c in small:
        h = max(c[4] - c[2], 0.1)
        best, best_ov = None, 0.3
        for r in rows:
            ov = (min(r["y1"], c[4]) - max(r["y0"], c[2])) / h
            if ov > best_ov:
                best, best_ov = r, ov
        if best is None:
            cy = (c[2] + c[4]) / 2
            best = {"cy": cy, "chars": [], "y0": c[2], "y1": c[4]}
            rows.append(best)
        best["chars"].append(c)
    rows.sort(key=lambda r: r["cy"])
    out = []
    for r in rows:
        cs = mark_scripts(sorted(r["chars"], key=lambda c: c[1]))
        toks, cur = [], []
        for c in cs:
            if c[0] == " ":
                if cur:
                    toks.append(Tok(cur))
                    cur = []
                continue
            if cur:
                gap = c[1] - cur[-1][3]
                if gap > 0.25 * c[7] or c[6] != cur[-1][6]:
                    toks.append(Tok(cur))
                    cur = []
            cur.append(c)
        if cur:
            toks.append(Tok(cur))
        if toks:
            out.append(toks)
    # merge rows whose vertical spans overlap heavily (e.g. equations)
    out.sort(key=lambda t: min(x.y0 for x in t))
    return out


def row_text(toks, gap_tab=30):
    s, prev = "", None
    for t in toks:
        if prev is not None:
            s += "\t" if t.x0 - prev.x1 > gap_tab else " "
        s += t.text
        prev = t
    return s


def is_footer(toks):
    txt = row_text(toks).strip()
    return bool(FOOTER.search(txt)) or txt in {"", "P.T.O."}


NUM = re.compile(r"^(\d{1,3})\.$")
CONTEXT = re.compile(
    r"^((From\s+)?Q\s*\.?\s*(Nos?\.?)?\s*\d+\s*(to|-|–|&|and)|Questions?\s+(Nos?\.?\s*)?\d+|Directions?\b|Instructions?\s*(\(|:|for)"
    r"|Read the (following|passage|given)|Passage\b|Study the (following|given))",
    re.I,
)
RANGE = re.compile(r"Q?\s*\.?\s*(?:Nos?\.?)?\s*(\d{1,3})\s*(?:to|-|–|&|and)\s*(?:Q\s*\.?\s*(?:No\.?)?\s*)?(\d{1,3})", re.I)


def segment(doc):
    """Split document into question blocks: {num, parts:[(page_no, rows, top, bottom)]}."""
    pages = []
    for p in doc:
        rows = [r for r in page_rows(p) if not is_footer(r)]
        footer_tops = [min(t.y0 for t in r) for r in page_rows(p) if is_footer(r) and min(t.y0 for t in r) > p.rect.height * 0.7]
        bottom = min(footer_tops) if footer_tops else p.rect.height - 40
        pages.append((p.number, rows, bottom))
    # question-number column: x of leading "N." tokens
    xs = [r[0].x0 for _, rows, _ in pages for r in rows if NUM.match(r[0].text)]
    col = statistics.mode(round(x) for x in xs)
    blocks, cur, expected = [], None, 1
    for pno, rows, bottom in pages:
        for r in rows:
            m = NUM.match(r[0].text)
            latin_row = " ".join(t.text for t in r if t.script == "latin")
            if not m and cur is not None and -8 < r[0].x0 - col < 40 and (
                CONTEXT.match(latin_row) or re.match(r"^(From\s+)?Q\s*\.\s*(Nos?\.?\s*)?\d", latin_row)
            ):
                cur = {"num": None, "context": True, "parts": [], "top": (pno, r[0].y0)}
                blocks.append(cur)
            if m and abs(r[0].x0 - col) < 8:
                n = int(m.group(1))
                if n in (expected, expected + 1) or (cur is None and n == 1):
                    top = min(t.y0 for t in r)
                    # rows that overlap the number row vertically (fraction numerators,
                    # stacked equations) belong to the new question
                    carried = []
                    if cur is not None and cur["parts"] and cur["parts"][-1][0] == pno:
                        prev_rows = cur["parts"][-1][1]
                        while prev_rows and max(t.y1 for t in prev_rows[-1]) > top + 1:
                            carried.insert(0, prev_rows.pop())
                    cur = {"num": n, "parts": [], "top": (pno, min([top] + [t.y0 for c in carried for t in c]))}
                    blocks.append(cur)
                    expected = n
                    if carried:
                        cur["parts"].append([pno, carried, bottom])
                    r = r[1:]
            if cur is None:
                continue
            if not cur["parts"] or cur["parts"][-1][0] != pno:
                cur["parts"].append([pno, [], bottom])
            if r:
                cur["parts"][-1][1].append(r)
    return blocks, pages


def block_lang(block):
    cnt = {"latin": 0, "beng": 0, "deva": 0}
    for _, rows, _ in block["parts"]:
        for r in rows:
            for t in r:
                if re.fullmatch(r"\([A-D]\)", t.text):
                    continue
                if t.script == "latin":
                    cnt["latin"] += sum(ch.isalpha() for ch in t.text)
                else:
                    cnt[t.script] += len(t.text)
    if cnt["beng"] > max(cnt["deva"], cnt["latin"]) and cnt["beng"] >= 3:
        return "beng"
    if cnt["deva"] > max(cnt["beng"], cnt["latin"]) and cnt["deva"] >= 3:
        return "deva"
    if cnt["latin"] >= 3:
        return "en"
    return "neutral"


def parse_options(rows, keep):
    """rows: list of token lists. keep(tok)->bool. Returns stem, {A..D}, ok."""
    seq = []  # (row_idx, tok)
    for i, r in enumerate(rows):
        for t in r:
            m = re.match(r"^\(([A-D])\)(.*)$", t.text)
            if m and t.script == "latin":
                seq.append((i, ("MARK", m.group(1), t)))
                if m.group(2):
                    seq.append((i, ("TXT", m.group(2), t)))
            elif keep(t):
                seq.append((i, ("TXT", t.text, t)))
    marks = [k for k, (_, e) in enumerate(seq) if e[0] == "MARK"]
    # pick the last A whose B, C, D follow in order
    start = None
    for k in reversed(marks):
        if seq[k][1][1] == "A":
            labels = [seq[j][1][1] for j in marks if j > k]
            if labels[:3] == ["B", "C", "D"]:
                start = k
                break
    if start is None:
        stem = rows_to_text([[e[2] for (i, e) in seq if i == ri and e[0] == "TXT"] for ri in range(len(rows))])
        return stem, {o: "" for o in OPT}, False
    stem_rows = {}
    for i, e in seq[:start]:
        stem_rows.setdefault(i, []).append(e[2] if e[0] == "TXT" else e[2])
    stem = rows_to_text([stem_rows[k] for k in sorted(stem_rows)])
    opts = {o: [] for o in OPT}
    cur_row_marks = []  # (x, label) of most recent marker row
    last_label, last_mark_row = None, None
    for i, e in seq[start:]:
        if e[0] == "MARK":
            if i != last_mark_row:
                cur_row_marks = []
                last_mark_row = i
            cur_row_marks.append((e[2].x0, e[1]))
            last_label = e[1]
            if e[1] == "D":
                seen_d = True
            continue
        tok = e[2]
        if i == last_mark_row:
            label = last_label
        else:  # continuation row: assign by column
            cands = [lab for x, lab in cur_row_marks if x <= tok.x0 + 4]
            label = cands[-1] if cands else cur_row_marks[0][1]
        opts[label].append(e[1])
    return stem, {o: clean(" ".join(v)) for o, v in opts.items()}, True


def rows_to_text(rows):
    return clean("\n".join(row_text(r) for r in rows if r))


# Adobe Symbol-font codes that appear as private-use chars (U+F0xx)
SYMBOL_PUA = {
    0x20: " ", 0x22: "∀", 0x24: "∃", 0x27: "∋", 0x2A: "∗", 0x2B: "+", 0x2D: "−", 0x3C: "<", 0x3D: "=", 0x3E: ">",
    0x40: "≅", 0x5C: "∴", 0x5E: "⊥", 0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x66: "φ", 0x67: "γ",
    0x6C: "λ", 0x6D: "μ", 0x70: "π", 0x71: "θ", 0x72: "ρ", 0x73: "σ", 0x77: "ω", 0x44: "Δ", 0x53: "Σ", 0x57: "Ω",
    0xA3: "≤", 0xB3: "≥", 0xB0: "°", 0xB1: "±", 0xB4: "×", 0xB8: "÷", 0xB9: "≠", 0xBA: "≡", 0xBB: "≈",
    0xD6: "√", 0xD7: "·", 0xA5: "∞", 0xAE: "→", 0xAC: "←", 0xDE: "⇒", 0xC8: "∪", 0xC7: "∩", 0xCE: "∈", 0xCC: "⊂",
}


def fix_symbols(s):
    out = []
    for ch in s:
        o = ord(ch)
        if 0xF000 <= o <= 0xF0FF:
            out.append(SYMBOL_PUA.get(o - 0xF000, ""))
        elif 0xF100 <= o <= 0xF8FF:
            continue  # unmapped symbol-font glyph (part of an equation)
        else:
            out.append(ch)
    return "".join(out)


def clean(s):
    s = fix_symbols(s)
    s = re.sub(r"[  ]+", " ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    s = s.replace(" ,", ",").replace(" .", ".").replace("( ", "(").replace(" )", ")")
    return s.strip()


def visual_info(doc, block, rep_xrefs, en_toks):
    """Detect non-text content inside the block's page regions."""
    kinds = set()
    regions = []
    for pno, rows, bottom in block["parts"]:
        page = doc[pno]
        top = block["top"][1] - 3 if block["top"][0] == pno else (min(t.y0 for r in rows for t in r) - 3 if rows else 0)
        bot = bottom
        regions.append((pno, top, bot))
    for idx, (pno, top, bot) in enumerate(regions):
        # bottom of this part = start of the next block on same page, set by caller
        page = doc[pno]
        bot = block.get("bottoms", {}).get(pno, bot)
        regions[idx] = (pno, top, bot)
        for img in page.get_images(full=True):
            if img[0] in rep_xrefs:
                continue
            for r in page.get_image_rects(img[0]):
                if r.y0 >= top - 2 and r.y1 <= bot + 2:
                    kinds.add("symbol_image" if r.width < 20 and r.height < 20 else "image")
        for g in page.get_drawings():
            r = g["rect"]
            if not (r.y0 >= top - 2 and r.y1 <= bot + 2):
                continue
            fill, stroke = g.get("fill"), g.get("color")
            if fill and all(v > 0.95 for v in fill) and not stroke:
                continue
            if r.height < 1.5 and r.width > 250:
                continue  # separator rule
            if r.width < 0.5 and r.height < 0.5:
                continue
            kinds.add("drawing")
    if any(t.math for t in en_toks):
        kinds.add("math")
    return kinds, regions


def crop(doc, regions, out_base, zoom=2.0):
    paths = []
    for k, (pno, top, bot) in enumerate(regions):
        page = doc[pno]
        clip = pymupdf.Rect(40, max(top - 4, 0), page.rect.width - 40, min(bot, page.rect.height))
        if clip.height < 8:
            continue
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
        path = out_base.with_name(out_base.name + (f"_{k + 1}" if len(regions) > 1 else "") + ".png")
        path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(path)
        paths.append(path)
    return paths


def load_key(pdf_path):
    """Answer key: <paper>_key.csv (q_no,answer[,source]) wins over the official <paper>_key.pdf."""
    key_csv = pdf_path.parent / "answer_keys" / (pdf_path.stem + "_key.csv")
    if key_csv.exists():
        with open(key_csv, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        return {int(r["q_no"]): r["answer"].strip().upper() for r in rows if r.get("answer", "").strip()}, (
            rows[0].get("source") or "manual_key_csv" if rows else "manual_key_csv"
        )
    key_pdf = pdf_path.parent / "answer_keys" / (pdf_path.stem + "_key.pdf")
    if not key_pdf.exists():
        return None, None
    kd = pymupdf.open(key_pdf)
    text = "\n".join(p.get_text() for p in kd)
    toks = text.split()
    key = {}
    for a, b in zip(toks, toks[1:]):
        if a.isdigit() and re.fullmatch(r"[A-D]{1,4}|G", b):
            key.setdefault(int(a), b)
    return key, "official_final_key"


def answer_value(raw):
    """Single letter, comma-joined letters when several are accepted, ALL when graced."""
    if not raw:
        return None
    if raw == "G":
        return "ALL"
    return ",".join(raw)


def answer_note(raw):
    if raw == "G":
        return "Official key 'G': question found ambiguous, all options treated as correct"
    if raw and len(raw) > 1:
        return f"Official key accepts multiple options: {','.join(raw)}"
    return None


def extract(pdf_path):
    pdf_path = Path(pdf_path).resolve()
    paper_code = pdf_path.stem
    year = int(pdf_path.parent.parent.name)
    exam = pdf_path.parent.parent.parent.name
    grade, paper_no, paper_title = PAPERS[paper_code]
    exam_dir = pdf_path.parent.parent
    doc = pymupdf.open(pdf_path)

    from collections import Counter

    xref_count = Counter(im[0] for p in doc for im in p.get_images(full=True))
    rep_xrefs = {x for x, c in xref_count.items() if c >= 5}

    blocks, pages = segment(doc)
    # each block part ends where the next block starts on the same page
    for i, b in enumerate(blocks):
        b["bottoms"] = {}
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        last_pno = b["parts"][-1][0]
        if nxt and nxt["top"][0] == last_pno:
            b["bottoms"][last_pno] = nxt["top"][1] - 3
        b["lang"] = block_lang(b)
        b["has_opts"] = any(re.fullmatch(r"\(D\).*", t.text) for _, rows, _ in b["parts"] for r in rows for t in r)

    groups = {}
    for b in blocks:
        if b["has_opts"] and b["num"] is not None:
            groups.setdefault(b["num"], []).append(b)

    fig_dir = exam_dir / "figures" / paper_code
    if fig_dir.is_dir():
        shutil.rmtree(fig_dir)

    # shared instructions / passages, attached to the questions they govern
    contexts = {}
    pending_as = []
    for i, b in enumerate(blocks):
        if not b.get("context"):
            continue
        rows = [r for _, rs, _ in b["parts"] for r in rs]
        latin = [[t for t in r if t.script == "latin"] for r in rows]
        text = rows_to_text(latin)
        lang = block_lang(b)
        if lang == "beng":
            # first Bengali-script copy of an instruction is the Assamese one
            prev = blocks[i - 1] if i else None
            if not (prev and prev.get("context") and block_lang(prev) == "beng"):
                nxt = next((x["num"] for x in blocks[i + 1:] if x["num"] is not None), None)
                if nxt:
                    as_ctx = rows_to_text([[t for t in r if t.script in ("beng", "latin")] for r in rows])
                    pending_as.append((nxt, as_ctx, sorted({t.font for r in rows for t in r if t.script == "beng"})))
            continue
        if lang != "en":
            continue  # Bengali/Bodo/Hindi copy of the instruction
        m = RANGE.search(text[:140])
        if m and int(m.group(1)) <= int(m.group(2)) <= int(m.group(1)) + 40:
            targets = list(range(int(m.group(1)), int(m.group(2)) + 1))
        else:
            nxt = next((x["num"] for x in blocks[i + 1:] if x["num"] is not None), None)
            targets = [nxt] if nxt else []
        if not targets:
            continue
        kinds, regions = visual_info(doc, b, rep_xrefs, [t for r in latin for t in r])
        imgs = []
        if kinds - {"table_layout"}:
            base = fig_dir / f"context_q{targets[0]:03d}-{targets[-1]:03d}"
            imgs = [str(p.relative_to(ROOT)) for p in crop(doc, regions, base)]
        for n in targets:
            c = contexts.setdefault(n, {"text": [], "kinds": set(), "images": []})
            c["text"].append(text)
            c["kinds"] |= kinds
            c["images"] += imgs
    # Assamese instructions apply to the same questions as the English one that follows
    for first_q, as_text, fonts in pending_as:
        span = contexts.get(first_q)
        if span is None:
            continue
        for c in contexts.values():
            if c["text"] == span["text"]:
                c["as"] = {"text": as_text, "fonts": fonts}

    key, key_source = load_key(pdf_path)
    meta = json.loads((ROOT / "scripts" / "booklets.json").read_text())[f"{exam}/{year}/{paper_code}"]
    en_first = meta["language_order"][0] == "en"

    questions = []
    for n in sorted(groups):
        g = groups[n]
        langs = [b["lang"] for b in g]
        notes = []
        en = next((b for b in g if b["lang"] == "en"), None)
        beng = [b for b in g if b["lang"] == "beng"]
        asm = beng[0] if beng else None
        if len(g) == 5:  # full translation set; trust booklet language order
            order = meta["language_order"]
            en = g[order.index("en")]
            asm = g[order.index("as")]
        elif en is None:
            neutral = [b for b in g if b["lang"] == "neutral"]
            if neutral:
                en = neutral[0] if en_first else neutral[-1]
        if len(g) not in (1, 5):
            notes.append(f"unusual number of language versions ({len(g)}: {','.join(langs)})")
        if en is None:
            notes.append("no English version found")
            en = g[0]

        rows = [r for _, rs, _ in en["parts"] for r in rs]
        stem, opts, ok = parse_options(rows, keep=lambda t: t.script == "latin")
        if not ok:
            notes.append("could not locate options (A)-(D)")
        en_toks = [t for r in rows for t in r if t.script == "latin"]
        kinds, regions = visual_info(doc, en, rep_xrefs, en_toks)
        if VISUAL_WORDS.search(stem):
            kinds.add("text_mentions_visual")
        if any("\t" in line for line in stem.split("\n")[1:]):
            kinds.add("table_layout")
        empty_opts = [o for o in OPT if not opts[o]]
        if empty_opts and ok:
            kinds.add("option_images")

        visual_in = []
        if kinds:
            # where does the visual sit? options rows start at first (A) marker
            visual_in = ["question"] + (["options"] if empty_opts or "option_images" in kinds else [])

        ctx = contexts.get(n)
        if ctx and ctx["kinds"] - {"table_layout"}:
            kinds |= {"context_" + k for k in ctx["kinds"]}

        qid = f"{exam}-{year}-{paper_code}-Q{n:03d}"
        image_paths = []
        has_visual = bool(kinds - {"text_mentions_visual"}) or bool(kinds and empty_opts)
        if kinds:
            base = exam_dir / "figures" / paper_code / f"q{n:03d}"
            image_paths = [str(p.relative_to(ROOT)) for p in crop(doc, regions, base)]
        if ctx:
            image_paths = ctx["images"] + image_paths

        as_raw = None
        if asm is not None:
            arows = [r for _, rs, _ in asm["parts"] for r in rs]
            astem, aopts, aok = parse_options(arows, keep=lambda t: t.script in ("beng", "latin"))
            fonts = sorted({t.font for r in arows for t in r if t.script == "beng"})
            as_raw = {"stem": astem, "options": aopts, "fonts": fonts, "page": asm["parts"][0][0] + 1}
            if ctx and ctx.get("as"):
                as_raw["context"] = ctx["as"]["text"]
            if not aok:
                notes.append("Assamese options not parsed")
        elif len(g) > 1:
            notes.append("no Assamese version found")

        ans = key.get(n) if key else None
        q = {
            "id": qid,
            "exam": exam,
            "year": year,
            "conducting_body": meta["conducting_body"],
            "grade": grade,
            "paper": paper_no,
            "paper_title": paper_title,
            "booklet_set": meta["booklet_set"],
            "q_no": n,
            "context_en": "\n\n".join(ctx["text"]) if ctx else None,
            "question_en": stem,
            "options_en": opts,
            "context_as": None,
            "question_as": None,
            "options_as": None,
            "answer": answer_value(ans),
            "answer_note": answer_note(ans),
            "answer_source": key_source if ans else None,
            "has_visual": has_visual,
            "visual_types": sorted(kinds),
            "visual_in": visual_in if kinds else [],
            "images": image_paths,
            "source_pdf": str(pdf_path.relative_to(ROOT)),
            "page_en": en["parts"][0][0] + 1,
            "page_as": as_raw["page"] if as_raw else None,
            "needs_review": bool(notes) or "text_mentions_visual" in kinds or "math" in kinds,
            "review_notes": notes,
            "_as_legacy": as_raw,
        }
        questions.append(q)

    expected = meta["total_questions"]
    got = {q["q_no"] for q in questions}
    missing = sorted(set(range(1, expected + 1)) - got)
    return questions, missing


CSV_COLS = [
    "id", "exam", "year", "grade", "paper", "booklet_set", "q_no",
    "context_en", "question_en", "option_a_en", "option_b_en", "option_c_en", "option_d_en",
    "context_as", "question_as", "option_a_as", "option_b_as", "option_c_as", "option_d_as",
    "answer", "answer_note", "answer_source", "has_visual", "visual_types", "images",
    "page_en", "page_as", "needs_review", "review_notes",
]


def write_outputs(questions, pdf_path):
    pdf_path = Path(pdf_path).resolve()
    out_dir = pdf_path.parent.parent / "data"
    out_dir.mkdir(exist_ok=True)
    stem = pdf_path.stem
    (out_dir / f"{stem}.json").write_text(json.dumps(questions, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with open(out_dir / f"{stem}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLS)
        for q in questions:
            oe, oa = q["options_en"], q["options_as"] or {}
            w.writerow([
                q["id"], q["exam"], q["year"], q["grade"], q["paper"], q["booklet_set"], q["q_no"],
                q["context_en"] or "", q["question_en"], oe["A"], oe["B"], oe["C"], oe["D"],
                q["context_as"] or "", q["question_as"] or "", oa.get("A", ""), oa.get("B", ""), oa.get("C", ""), oa.get("D", ""),
                q["answer"] or "", q["answer_note"] or "", q["answer_source"] or "", int(q["has_visual"]), "|".join(q["visual_types"]),
                "|".join(q["images"]), q["page_en"], q["page_as"] or "", int(q["needs_review"]), " ; ".join(q["review_notes"]),
            ])


def build_index():
    """Write catalog.json and exams/<EXAM>/<EXAM>_all.{csv,json} from every per-paper JSON."""
    catalog = []
    for exam_dir in sorted(p for p in (ROOT / "exams").iterdir() if p.is_dir()):
        all_q = []
        for f in sorted(exam_dir.glob("*/data/*.json")):
            qs = json.loads(f.read_text(encoding="utf-8"))
            all_q += qs
            catalog.append({
                "exam": exam_dir.name, "year": int(f.parent.parent.name), "paper_code": f.stem,
                "questions": len(qs),
                "with_answer": sum(q["answer"] is not None for q in qs),
                "with_assamese": sum(q["question_as"] is not None for q in qs),
                "with_visual": sum(q["has_visual"] for q in qs),
                "needs_review": sum(q["needs_review"] for q in qs),
                "json": str(f.relative_to(ROOT)), "csv": str(f.with_suffix(".csv").relative_to(ROOT)),
                "source_pdf": qs[0]["source_pdf"] if qs else None,
            })
        if all_q:
            slim = [{k: v for k, v in q.items() if not k.startswith("_")} for q in all_q]
            (exam_dir / f"{exam_dir.name}_all.json").write_text(json.dumps(slim, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            with open(exam_dir / f"{exam_dir.name}_all.csv", "w", newline="", encoding="utf-8") as out:
                w = csv.writer(out)
                w.writerow(CSV_COLS)
                for f in sorted(exam_dir.glob("*/data/*.csv")):
                    with open(f, encoding="utf-8") as inp:
                        w.writerows(list(csv.reader(inp))[1:])
    (ROOT / "catalog.json").write_text(json.dumps(catalog, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    for path in sys.argv[1:]:
        qs, missing = extract(path)
        write_outputs(qs, path)
        nv = sum(q["has_visual"] for q in qs)
        nr = sum(q["needs_review"] for q in qs)
        na = sum(q["answer"] is not None for q in qs)
        print(f"{Path(path).parent.parent.name}/{Path(path).stem}: {len(qs)} questions, {nv} visual, {na} answered, {nr} to review, missing={missing}")
    build_index()
