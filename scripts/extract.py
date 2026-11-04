#!/usr/bin/env python3
"""Extract MCQs from SLRC (ADRE Grade III/IV) question booklets.

The booklets are multilingual (English, Assamese, Bengali, Bodo, Hindi). Only
the English version, the authoritative one, is extracted. The other languages are
set in legacy fonts and are used only to tell the language blocks apart.

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

import ocr
import taxonomy
import answers

ROOT = Path(__file__).resolve().parent.parent

BENG_FONTS = re.compile(r"Geetanjali|SHREE-ASS|SHREE-BAN", re.I)
DEVA_FONTS = re.compile(r"Chanakya|SHREE-DEV|GS-Gitu", re.I)
MATH_FONTS = re.compile(r"[0-9A-F]{4}o00$|^Symbol$|MT-Extra|Math")
FOOTER = re.compile(
    r"^(Paper\s*[-–]?\s*[IVX]+\s*/\s*[A-Z]|SLRC-20\d\d/|SPACE FOR ROUGH WORK|\d{1,3}$|_+$)"
    r"|P\s*\.\s*T\s*\.\s*O|Pa\s*per\s*[-–]\s*[IVX]+\s*/\s*[A-Z]|^[A-Z]{3,6}-[IVX]+-\d\d/\d+"
)
VISUAL_WORDS = re.compile(
    r"\b(figure|figures|diagram|picture|image|graph|shown below|given below figure|mirror image|water image|venn|pie[- ]chart|bar[- ]chart|table)\b",
    re.I,
)
OPT = "ABCD"


SHREE_SIGNATURE = set("¡¹ìëãšÛ¢¤Åà[Îê³")
SHREE_DEV_SIGNATURE = set("Ê¥ÁÿŸ’ªÕ◊‹⁄ÒU")


def legacy_span_script(span):
    """Script of a span in an anonymously named legacy font (e.g. 'TT7B9FO00'), guessed from its glyphs."""
    if BENG_FONTS.search(span["font"]) or DEVA_FONTS.search(span["font"]):
        return None
    text = "".join(c["c"] for c in span["chars"] if not c["c"].isspace())
    if len(text) < 3:
        return None
    if sum(ch in SHREE_SIGNATURE for ch in text) / len(text) > 0.2:
        return "beng"
    if sum(ch in SHREE_DEV_SIGNATURE for ch in text) / len(text) > 0.2:
        return "deva"
    return None


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
    __slots__ = ("text", "x0", "x1", "y0", "y1", "script", "font", "math", "xoff")

    def __init__(self, chars):
        self.xoff = 0  # OCR pages: x is relative to the token's column, which starts at xoff
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
                span_script = legacy_span_script(s)
                for c in s["chars"]:
                    ch = c["c"]
                    x0, y0, x1, y1 = c["bbox"]
                    if not ch.strip() and ch != " ":
                        continue
                    ch = normalize_char(ch, s["font"])
                    if ch is None:
                        continue
                    chars.append((ch, x0, y0, x1, y1, s["font"], span_script or char_script(ch, s["font"]), s["size"]))
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


def ocr_page_rows(page):
    """Rows of word tokens from OCR, column by column (left column first)."""
    lines = ocr.page_lines(page)
    out = []
    for cx0, cx1 in ocr.columns(lines, page.rect.width):
        toks = []
        for x0, y0, x1, y1, text, score in lines:
            if not cx0 <= (x0 + x1) / 2 < cx1:
                continue
            if re.match(r"^[A-Z]{3,6}-[IVX1l]+-\d\d/\S+$|^\[?\s*P\s*\.?\s*T\s*\.?\s*O", text.strip()):
                continue  # page footer
            chars = [c[:5] + (f"OCR{score:.2f}",) + c[6:] for c in ocr.word_chars(x0, y0, x1, y1, text)]
            word = []
            for c in chars + [(" ",) + chars[-1][1:]] if chars else []:
                if c[0] == " ":
                    m = re.match(r"^(\d{1,3}\.)(\S+)$", "".join(ch[0] for ch in word))
                    if m:  # "9.India" -> "9." "India"
                        k = len(m.group(1))
                        word, rest = word[:k], word[k:]
                        t = Tok(word)
                        t.x0 -= cx0
                        t.x1 -= cx0
                        t.xoff = cx0
                        toks.append(t)
                        word = rest
                    if word:
                        t = Tok(word)
                        m = ocr.MARKER.match(t.text)
                        if m:
                            t.text = "(" + ((m.group(1) or m.group(2) or "?").upper()) + ")"
                        t.x0 -= cx0
                        t.x1 -= cx0
                        t.xoff = cx0
                        toks.append(t)
                    word = []
                else:
                    word.append(c)
        toks.sort(key=lambda t: ((t.y0 + t.y1) / 2, t.x0))
        rows = []
        for t in toks:
            cy = (t.y0 + t.y1) / 2
            if rows and abs(cy - rows[-1][0]) <= max(3.5, 0.4 * (t.y1 - t.y0)):
                rows[-1][1].append(t)
            else:
                rows.append([cy, [t]])
        out += [sorted(r, key=lambda t: t.x0) for _, r in rows]
    return out


def _tok_like(t, text, x0, x1):
    u = object.__new__(Tok)
    for k in Tok.__slots__:
        setattr(u, k, getattr(t, k))
    u.text, u.x0, u.x1 = text, x0, x1
    return u


def ocr_score(row):
    return min(float(t.font[3:]) for t in row if t.font.startswith("OCR"))


def drop_second_language(rows):
    """Bilingual scans: the English question is followed by a copy in another script, which the
    English OCR model reads with low confidence. Cut the block there, after the English options."""
    seen_mark = False
    for i, r in enumerate(rows):
        if any(re.fullmatch(r"\([A-D?]\)", t.text) for t in r):
            seen_mark = True
        elif seen_mark and ocr_score(r) < 0.7:
            return rows[:i]
    return rows


def repair_ocr_markers(rows):
    """Fix option labels that OCR glued to the text ("(Bthe") or dropped.

    A dropped label is restored where a row starts at the label column, or where a
    row in the options area follows a larger gap than wrapped lines have.
    """
    is_mark = lambda t: bool(re.fullmatch(r"\([A-D?]\)", t.text))
    out = []
    for r in rows:
        t = r[0]
        m = re.match(r"^[\(\[\{|]([A-D])(?:[\)\]\}|]([a-z\d'\"‘“(].*)|([a-z].*))$", t.text) or re.match(
            r"^[\(\[\{|]([a-d])[\)\]\}|]()(\S+)$", t.text)
        if m:
            rest = m.group(2) or m.group(3)
            w = (t.x1 - t.x0) / max(len(t.text), 1)
            k = len(t.text) - len(rest)
            r = [_tok_like(t, f"({m.group(1).upper()})", t.x0, t.x0 + k * w), _tok_like(t, rest, t.x0 + k * w, t.x1)] + r[1:]
        out.append(r)
    if sum(is_mark(r[0]) for r in out) < 3:
        # labels lost: the four rows after the line that ends the question are the options
        ends = [i for i, r in enumerate(out) if re.search(r"(\?|:|below\.?|correct\??|following)\s*$", row_text(r))]
        end = next((i for i in ends if len(out) - i - 1 == 4), None)
        if end is not None:
            for k, i in enumerate(range(end + 1, len(out))):
                r = [t for t in out[i] if not is_mark(t)]
                lab = OPT[k]
                t = r[0]
                m = re.match(rf"^[\(\[]?{lab}[\)\]]?(?=[A-Z(\d]|$)(.*)$", t.text) or (
                    re.match(r"^()$", "") if t.text == lab else None)
                if m:
                    r = ([_tok_like(t, m.group(1), t.x0, t.x1)] if m.group(1) else []) + r[1:]
                out[i] = [_tok_like(t, f"({lab})", t.x0 - 12, t.x0 - 2)] + r
            return out
    first = next((i for i, r in enumerate(out) if is_mark(r[0])), None)
    if first is None:
        return out
    marks = [r[0].x0 for r in out if is_mark(r[0])]
    col = statistics.median(marks)
    tops = [min(t.y0 for t in r) for r in out]
    gaps = [b - a for a, b in zip(tops, tops[1:]) if 0 < b - a < 40]
    line = statistics.median(gaps) if gaps else 12
    for i in range(first + 1, len(out)):
        r = out[i]
        if is_mark(r[0]) or re.match(r"^\(?(i{1,3}|iv|v)\)", r[0].text) and abs(r[0].x0 - col) > 5:
            continue
        new_option = abs(r[0].x0 - col) <= 5 or (0 < tops[i] - tops[i - 1] > 1.45 * line and tops[i] - tops[i - 1] < 60)
        if new_option and sum(is_mark(x[0]) for x in out) < 4:
            out[i] = [_tok_like(r[0], "(?)", r[0].x0 - 12, r[0].x0 - 2)] + r
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
    r"|Read the (following|passage|given)|Passage\b|Study the (following|given)|PART\s*[-–:]?\s*[IVX]+\b)",
    re.I,
)
RANGE = re.compile(r"Q?\s*\.?\s*(?:Nos?\.?)?\s*(\d{1,3})\s*(?:to|-|–|&|and)\s*(?:Q\s*\.?\s*(?:No\.?)?\s*)?(\d{1,3})", re.I)


def segment(doc, rows_fn=page_rows, ocr_mode=False, start_page=1):
    """Split document into question blocks: {num, parts:[(page_no, rows, top, bottom)]}."""
    pages = []
    for p in doc.pages(start_page - 1):
        all_rows = rows_fn(p)
        rows = [r for r in all_rows if not is_footer(r)]
        footer_tops = [min(t.y0 for t in r) for r in all_rows if is_footer(r) and min(t.y0 for t in r) > p.rect.height * 0.7]
        bottom = min(footer_tops) if footer_tops else p.rect.height - 40
        pages.append((p.number, rows, bottom))
    # question-number column: x of leading "N." tokens
    xs = [r[0].x0 for _, rows, _ in pages for r in rows if NUM.match(r[0].text)]
    col = statistics.mode(round(x) for x in xs)
    # scanned pages drift: numbers sit at the left edge of their own page column
    left_edge = {}
    for pno, rows, _ in pages:
        for r in rows:
            if NUM.match(r[0].text):
                k = (pno, r[0].xoff)
                left_edge[k] = min(left_edge.get(k, 1e9), r[0].x0)
    blocks, cur, expected = [], None, 1
    seen_options = False
    for pno, rows, bottom in pages:
        for r in rows:
            m = NUM.match(r[0].text)
            latin_row = " ".join(t.text for t in r if t.script == "latin")
            if not m and cur is not None and (
                (-8 < r[0].x0 - col < 40 and (CONTEXT.match(latin_row) or re.match(r"^(From\s+)?Q\s*\.\s*(Nos?\.?\s*)?\d", latin_row)))
                or re.match(r"^PART\s*[-–:]?\s*[IVX]+\b", latin_row)
            ):
                cur = {"num": None, "context": True, "parts": [], "top": (pno, r[0].y0, r[0].xoff)}
                blocks.append(cur)
            at_margin = (r[0].x0 <= left_edge.get((pno, r[0].xoff), 1e9) + 12) if ocr_mode else abs(r[0].x0 - col) < 8
            if m and at_margin:
                n = int(m.group(1))
                window = range(expected, expected + (7 if ocr_mode else 2))
                # numbering may restart at 1 after numbered cover-page instructions
                restart = n == 1 and expected > 3 and not seen_options
                if n in window or restart or (cur is None and n <= (3 if ocr_mode else 1)):
                    top = min(t.y0 for t in r)
                    # rows that overlap the number row vertically (fraction numerators,
                    # stacked equations) belong to the new question
                    carried = []
                    if cur is not None and cur["parts"] and cur["parts"][-1][0] == pno:
                        prev_rows = cur["parts"][-1][1]
                        while prev_rows and prev_rows[-1][0].xoff == r[0].xoff and max(t.y1 for t in prev_rows[-1]) > top + 1:
                            carried.insert(0, prev_rows.pop())
                    cur = {"num": n, "parts": [], "top": (pno, min([top] + [t.y0 for c in carried for t in c]), r[0].xoff)}
                    blocks.append(cur)
                    expected = n
                    if carried:
                        cur["parts"].append([pno, carried, bottom])
                    r = r[1:]
            if cur is None:
                continue
            col_of = lambda part: part[1][0][0].xoff if part[1] else None
            if not cur["parts"] or cur["parts"][-1][0] != pno or (
                r and cur["parts"][-1][1] and col_of(cur["parts"][-1]) != r[0].xoff
            ):
                cur["parts"].append([pno, [], bottom])
            if r:
                cur["parts"][-1][1].append(r)
                seen_options = seen_options or any(re.fullmatch(r"\([A-Da-d?]\)", t.text) for t in r)
    return blocks, pages


def block_lang(block):
    cnt = {"latin": 0, "beng": 0, "deva": 0}
    for _, rows, _ in block["parts"]:
        for r in rows:
            for t in r:
                if re.fullmatch(r"\([A-Da-d?]\)", t.text):
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
            m = re.match(r"^\(([A-Da-d?])\)(.*)$", t.text)
            if m and t.script == "latin":
                label = m.group(1).upper()
                if label == "?":  # OCR lost the letter: it is the next one in sequence
                    prev = [e[1] for _, e in seq if e[0] == "MARK"]
                    label = OPT[min(OPT.index(prev[-1]) + 1, 3)] if prev and prev[-1] in OPT else "A"
                seq.append((i, ("MARK", label, t)))
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


def ocr_visual_info(doc, block, en_toks):
    """Scanned pages: a figure shows up as a tall blank band between text rows."""
    kinds, regions = set(), []
    for pno, rows, bottom in block["parts"]:
        col = rows[0][0].xoff if rows else 0
        top = block["top"][1] - 3 if block["top"][:3:2] == (pno, col) else (min(t.y0 for r in rows for t in r) - 3 if rows else 0)
        bot = block.get("bottoms", {}).get((pno, col), bottom)
        toks = [t for r in rows for t in r]
        xoff = toks[0].xoff if toks else 0
        width = doc[pno].rect.width
        x1 = xoff + width / 2 if two_column(doc[pno]) else width
        regions.append((pno, top, bot, max(xoff, 20), x1 - 10))
        ys = sorted((t.y0, t.y1) for t in toks)
        for (a0, a1), (b0, b1) in zip(ys, ys[1:]):
            if b0 - a1 > 28:
                kinds.add("figure_gap")
    return kinds, regions


def two_column(page):
    return len(ocr.columns(ocr.page_lines(page), page.rect.width)) == 2


def visual_info(doc, block, rep_xrefs, en_toks):
    """Detect non-text content inside the block's page regions."""
    kinds = set()
    regions = []
    for pno, rows, bottom in block["parts"]:
        page = doc[pno]
        col = rows[0][0].xoff if rows else 0
        top = block["top"][1] - 3 if block["top"][:3:2] == (pno, col) else (min(t.y0 for r in rows for t in r) - 3 if rows else 0)
        bot = bottom
        regions.append((pno, top, bot))
    for idx, (pno, top, bot) in enumerate(regions):
        # bottom of this part = start of the next block on same page, set by caller
        page = doc[pno]
        bot = block.get("bottoms", {}).get((pno, 0), bot)
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


def crop(doc, regions, out_base, zoom=2.0, scanned=False):
    """PNG crops for text PDFs; smaller grayscale JPEGs for scanned pages."""
    paths = []
    for k, (pno, top, bot, *xr) in enumerate(regions):
        page = doc[pno]
        x0, x1 = xr if xr else (40, page.rect.width - 40)
        clip = pymupdf.Rect(x0, max(top - 4, 0), x1, min(bot, page.rect.height))
        if clip.height < 8:
            continue
        z = 1.6 if scanned else zoom
        pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, colorspace=pymupdf.csGRAY if scanned else None)
        path = out_base.with_name(out_base.name + (f"_{k + 1}" if len(regions) > 1 else "") + (".jpg" if scanned else ".png"))
        path.parent.mkdir(parents=True, exist_ok=True)
        if scanned:
            path.write_bytes(pix.tobytes("jpg", jpg_quality=70))
        else:
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
    if len(text.strip()) < 50:  # scanned key: read it with OCR, row by row, left to right
        rows = []
        for p in kd:
            lines = sorted(ocr.page_lines(p), key=lambda l: ((l[1] + l[3]) / 2, l[0]))
            for l in lines:
                if rows and abs((l[1] + l[3]) / 2 - rows[-1][0]) < 6:
                    rows[-1][1].append(l)
                else:
                    rows.append([(l[1] + l[3]) / 2, [l]])
        text = "\n".join(" ".join(l[4] for l in sorted(r, key=lambda l: l[0])) for _, r in rows)
    toks = text.split()
    key = {}
    for a, b in zip(toks, toks[1:]):
        if a.isdigit() and re.fullmatch(r"[A-D]{1,4}|G", b):
            key.setdefault(int(a), b)
    return key, "pdf"


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
    meta = json.loads((ROOT / "scripts" / "booklets.json").read_text())[f"{exam}/{year}/{paper_code}"]
    exam_dir = pdf_path.parent.parent
    doc = pymupdf.open(pdf_path)

    from collections import Counter

    xref_count = Counter(im[0] for p in doc for im in p.get_images(full=True))
    rep_xrefs = {x for x, c in xref_count.items() if c >= 5}

    ocr_mode = meta.get("mode") == "ocr"
    blocks, pages = segment(doc, ocr_page_rows if ocr_mode else page_rows, ocr_mode, meta.get("start_page", 1))
    if ocr_mode:
        for b in blocks:
            if meta.get("second_language"):
                kept, cut = [], False
                for part in b["parts"]:
                    if cut:
                        part[1] = []
                        continue
                    trimmed = drop_second_language(kept + part[1])[len(kept):]
                    cut = len(trimmed) < len(part[1])
                    kept += trimmed
                    part[1] = trimmed
                b["parts"] = [p for p in b["parts"] if p[1]]
            for part in b["parts"]:
                part[1] = repair_ocr_markers(part[1])
    # each block part ends where the next block starts on the same page
    for i, b in enumerate(blocks):
        b["bottoms"] = {}
        nxt = blocks[i + 1] if i + 1 < len(blocks) else None
        last_pno = b["parts"][-1][0]
        last_col = b["parts"][-1][1][0][0].xoff if b["parts"] and b["parts"][-1][1] else 0
        if nxt and nxt["top"][0] == last_pno and nxt["top"][2] == last_col:
            b["bottoms"][(last_pno, last_col)] = nxt["top"][1] - 3
        b["lang"] = block_lang(b)
        b["has_opts"] = sum(bool(re.fullmatch(r"\([A-Da-d?]\).*", t.text)) for _, rows, _ in b["parts"] for r in rows for t in r) >= 3

    groups = {}
    for b in blocks:
        lo, hi = meta.get("question_range", [1, 10**6])
        if b["has_opts"] and b["num"] is not None and lo <= b["num"] <= hi:
            groups.setdefault(b["num"], []).append(b)
    if ocr_mode:  # keep OCR questions whose option labels were unreadable; their image carries them
        first = next((i for i, b in enumerate(blocks) if b["has_opts"]), len(blocks))
        for b in blocks[first:]:
            if b["num"] is not None and b["num"] not in groups and lo <= b["num"] <= hi:
                groups[b["num"]] = [b]

    fig_dir = exam_dir / "figures" / paper_code
    if fig_dir.is_dir():
        shutil.rmtree(fig_dir)

    # shared instructions / passages, attached to the questions they govern
    contexts = {}
    first_q = next((i for i, b in enumerate(blocks) if b.get("has_opts") and b["num"] is not None), 0)
    for i, b in enumerate(blocks):
        if not b.get("context") or i < first_q:
            continue
        rows = [r for _, rs, _ in b["parts"] for r in rs]
        latin = [[t for t in r if t.script == "latin"] for r in rows]
        text = rows_to_text(latin)
        lang = block_lang(b)
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
        kinds, regions = (ocr_visual_info(doc, b, []) if ocr_mode else visual_info(doc, b, rep_xrefs, [t for r in latin for t in r]))
        imgs = []
        if kinds - {"table_layout"}:
            base = fig_dir / f"context_q{targets[0]:03d}-{targets[-1]:03d}"
            imgs = [str(p.relative_to(ROOT)) for p in crop(doc, regions, base, scanned=ocr_mode)]
        for n in targets:
            c = contexts.setdefault(n, {"text": [], "kinds": set(), "images": []})
            c["text"].append(text)
            c["kinds"] |= kinds
            c["images"] += imgs


    key, key_source = load_key(pdf_path)
    if key_source == "pdf":
        key_source = f"official_{meta.get('key_status', 'final')}_key"
    order = meta["language_order"]
    en_first = meta["language_order"][0] == "en"

    questions, skipped = [], []
    for n in sorted(groups):
        g = groups[n]
        langs = [b["lang"] for b in g]
        notes = []
        en = next((b for b in g if b["lang"] == "en"), None)
        if len(g) == len(order) > 1:  # full translation set; trust booklet language order
            en = g[order.index("en")]
        elif en is None:
            neutral = [b for b in g if b["lang"] == "neutral"]
            if neutral:
                en = neutral[0] if en_first else neutral[-1]
        if len(g) not in (1, len(order)):
            notes.append(f"unusual number of language versions ({len(g)}: {','.join(langs)})")
        if en is None:
            # an English copy whose options did not parse (e.g. set partly in another font)
            en = next((b for b in blocks if b["num"] == n and b["lang"] == "en" and not b.get("context")), None)
            if en is None:
                skipped.append(n)  # language-subject question with no English version
                continue
            notes.append("English version found but its options were not recognised")

        rows = [r for _, rs, _ in en["parts"] for r in rs]
        stem, opts, ok = parse_options(rows, keep=lambda t: t.script == "latin")
        if not ok:
            notes.append("could not locate options (A)-(D)")
        en_toks = [t for r in rows for t in r if t.script == "latin"]
        kinds, regions = ocr_visual_info(doc, en, en_toks) if ocr_mode else visual_info(doc, en, rep_xrefs, en_toks)
        if not ok:
            kinds.add("parse_failed")  # crop the original so the question can be read from the image
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
        # scanned papers: figures cannot be told apart from text in a page image, and OCR text
        # may be wrong, so every question keeps an image of the original
        if kinds or ocr_mode:
            base = exam_dir / "figures" / paper_code / f"q{n:03d}"
            image_paths = [str(p.relative_to(ROOT)) for p in crop(doc, regions, base, scanned=ocr_mode)]
        if ctx:
            image_paths = ctx["images"] + image_paths

        ans = key.get(n) if key else None
        q = {
            "id": qid,
            "exam": exam,
            "year": year,
            "conducting_body": meta["conducting_body"],
            "paper_code": paper_code,
            "paper_title": meta["paper_title"],
            "booklet_set": meta["booklet_set"],
            "q_no": n,
            "context": "\n\n".join(ctx["text"]) if ctx else None,
            "question": stem,
            "options": opts,
            "answer": answer_value(ans),
            "answer_note": answer_note(ans),
            "answer_source": key_source if ans else None,
            "has_visual": has_visual,
            "text_source": "ocr" if ocr_mode else "pdf_text",
            "visual_types": sorted(kinds),
            "visual_in": visual_in if kinds else [],
            "images": image_paths,
            "source_pdf": str(pdf_path.relative_to(ROOT)),
            "page": en["parts"][0][0] + 1,
            "needs_review": bool(notes) or "text_mentions_visual" in kinds or "math" in kinds or "figure_gap" in kinds,
            "review_notes": notes,
        }
        questions.append(q)

    expected = min(meta["total_questions"], meta.get("question_range", [1, 10**6])[1])
    got = {q["q_no"] for q in questions}
    missing = sorted(set(range(1, expected + 1)) - got - set(skipped))
    if skipped:
        print(f"  skipped {len(skipped)} questions with no English version: {skipped}")
    return questions, missing


def write_outputs(questions, pdf_path):
    pdf_path = Path(pdf_path).resolve()
    out_dir = pdf_path.parent.parent / "data"
    out_dir.mkdir(exist_ok=True)
    stem = pdf_path.stem
    for q in questions:
        answers.apply_solution(q)
        q.update(taxonomy.classify(q))
    (out_dir / f"{stem}.json").write_text(json.dumps(questions, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def build_index():
    """Write catalog.json and questions.csv from every per-paper JSON."""
    catalog = []
    all_questions = []
    booklets = json.loads((ROOT / "scripts" / "booklets.json").read_text())
    for exam_dir in sorted(p for p in (ROOT / "exams").iterdir() if p.is_dir()):
        for f in sorted(exam_dir.glob("*/data/*.json")):
            qs = json.loads(f.read_text(encoding="utf-8"))
            all_questions.extend(qs)
            meta = booklets.get(f"{exam_dir.name}/{f.parent.parent.name}/{f.stem}", {})
            lo, hi = meta.get("question_range", [1, meta.get("total_questions", 0)])
            catalog.append({
                "exam": exam_dir.name, "year": int(f.parent.parent.name), "paper_code": f.stem,
                "paper_title": meta.get("paper_title"),
                "questions": len(qs),
                "missing_q_nos": sorted(set(range(lo, min(hi, meta.get("total_questions", hi)) + 1)) - {q["q_no"] for q in qs}),
                "text_source": qs[0]["text_source"] if qs else None,
                "booklet_source": meta.get("source"),
                "note": meta.get("note"),
                "with_answer": sum(q["answer"] is not None for q in qs),
                "with_visual": sum(q["has_visual"] for q in qs),
                "needs_review": sum(q["needs_review"] for q in qs),
                "json": str(f.relative_to(ROOT)), "csv": "questions.csv",
                "source_pdf": qs[0]["source_pdf"] if qs else None,
            })
    (ROOT / "questions.csv").write_text(taxonomy.export_csv(all_questions), encoding="utf-8", newline="")
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
