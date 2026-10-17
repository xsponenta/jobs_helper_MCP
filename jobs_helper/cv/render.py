"""Designed CV / cover letter PDFs from markdown.

All themes are single-column, real text and standard headings, so ATS parsers read them correctly.
Entry lines like "**Python Developer — BrightCart** · Mar 2024 – present" get the dates aligned right.
`max_pages` shrinks font and spacing step by step until the CV fits.
"""

from __future__ import annotations

import html
import re
from io import BytesIO
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright
from pypdf import PdfReader

from jobs_helper.apply.browser_env import ensure_chromium

THEMES = {
    "modern": {
        "font": "'Inter', 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
        "css": """
        header { border-bottom: 3px solid var(--accent); padding-bottom: 8pt; margin-bottom: 4pt; }
        h1 { color: var(--accent); letter-spacing: .3px; }
        h2 { color: var(--accent); text-transform: uppercase; letter-spacing: 1.2px; font-size: 1.0em;
             border-bottom: 1px solid #d9dee5; padding-bottom: 2pt; }
        """,
    },
    "classic": {
        "font": "Georgia, 'Times New Roman', Times, serif",
        "css": """
        header { text-align: center; margin-bottom: 6pt; }
        header .meta { justify-content: center; }
        h1 { font-variant: small-caps; letter-spacing: 1px; }
        h2 { font-size: 1.05em; border-bottom: 1.5px solid #222; padding-bottom: 1pt; }
        """,
    },
    "compact": {
        "font": "'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
        "css": """
        header { display: flex; justify-content: space-between; align-items: flex-end; border-bottom: 2px solid var(--accent);
                 padding-bottom: 4pt; }
        header .meta { flex-direction: column; align-items: flex-end; gap: 0; }
        h2 { color: var(--accent); font-size: .95em; text-transform: uppercase; letter-spacing: 1px; margin-top: 8pt; }
        """,
    },
}
BASE_CSS = """
@page { size: A4; margin: 14mm 15mm; }
:root { --accent: ACCENT; --scale: SCALE; }
* { box-sizing: border-box; }
body { font-family: FONT; font-size: calc(10.2pt * var(--scale)); line-height: 1.38; color: #1d232b; margin: 0; }
h1 { font-size: 2.05em; margin: 0 0 2pt; line-height: 1.1; }
header .title { font-size: 1.12em; color: #3c4653; margin: 0 0 4pt; }
header .meta { display: flex; flex-wrap: wrap; gap: 2pt 12pt; color: #4a5562; font-size: .92em; }
header .meta span { white-space: nowrap; }
h2 { margin: calc(11pt * var(--scale)) 0 4pt; page-break-after: avoid; }
h3 { font-size: 1.02em; margin: 6pt 0 1pt; }
p { margin: 2pt 0; } ul { margin: 2pt 0 4pt; padding-left: 0; list-style: none; }
li { margin: 1.5pt 0; padding-left: 11pt; text-indent: -11pt; }
li .bul { color: var(--accent); display: inline-block; width: 11pt; text-indent: 0; }
.entry { display: flex; justify-content: space-between; gap: 10pt; margin-top: 6pt; page-break-after: avoid; }
.entry .role { font-weight: 600; } .entry .dates { color: #5b6673; white-space: nowrap; font-size: .93em; }
a { color: inherit; text-decoration: none; }
strong { font-weight: 600; }
@media screen { body { padding: 14mm 15mm; } }
"""
LETTER_CSS = """
@page { size: A4; margin: 22mm 22mm; }
body { font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 11pt; line-height: 1.55; color: #1d232b; }
p { margin: 0 0 9pt; }
"""
_DATES = re.compile(r"((?:\b[A-Za-zА-Яа-яІіЇїЄєҐґ]{3,9}\.?\s)?\b(19|20)\d{2}\s*[–—-]\s*(?:(?:[A-Za-zА-Яа-яІіЇїЄєҐґ]{3,9}\.?\s)?(19|20)\d{2}|present|now|current|теперішній час|дотепер|зараз))", re.I)


def cv_html(md_text: str, theme: str = "modern", accent: str = "#1f5fbf", scale: float = 1.0) -> str:
    th = THEMES.get(theme, THEMES["modern"])
    lines = md_text.strip().splitlines()
    name, head_lines, body_start = "", [], 0
    for i, line in enumerate(lines):
        if line.startswith("# ") and not name:
            name = line[2:].strip()
            continue
        if name and line.startswith("## "):
            body_start = i
            break
        if name and line.strip():
            head_lines.append(line.strip())
    else:
        body_start = len(lines) if name else 0
    title = head_lines[0] if head_lines else ""
    meta_items = [x.strip() for line in head_lines[1:] for x in re.split(r"\s[·|•]\s", line) if x.strip()]
    if len(head_lines) == 1 and "·" in title:            # "Role · City · email" on one line
        parts = [x.strip() for x in re.split(r"\s[·|•]\s", title)]
        title, meta_items = parts[0], parts[1:]
    body = markdown.markdown(_loosen_lists(lines[body_start:]), extensions=["extra", "sane_lists"])
    body = re.sub(r"<p><strong>(.+?)</strong>\s*(?:·|\||—|-)?\s*(.*?)</p>", _entry, body)
    body = body.replace("<li>", "<li><span class='bul'>•</span>")     # a real bullet character in the text layer
    header = (f"<header><div><h1>{html.escape(name)}</h1>" + (f"<div class='title'>{markdown.markdown(title)[3:-4]}</div>" if title else "")
              + "</div><div class='meta'>" + "".join(f"<span>{markdown.markdown(m)[3:-4]}</span>" for m in meta_items) + "</div></header>")
    css = (BASE_CSS.replace("ACCENT", accent).replace("SCALE", f"{scale:.3f}").replace("FONT", th["font"])) + th["css"]
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body>{header}{body}</body></html>"


_LIST_ITEM = re.compile(r"^\s*([-*+]|\d+\.)\s+")


def _loosen_lists(lines: list[str]) -> str:
    """Python-Markdown needs a blank line before a list; CVs rarely have one."""
    out: list[str] = []
    for line in lines:
        if _LIST_ITEM.match(line) and out and out[-1].strip() and not _LIST_ITEM.match(out[-1]):
            out.append("")
        out.append(line)
    return "\n".join(out)


def _entry(m: re.Match) -> str:
    role, rest = m.group(1), m.group(2).strip()
    dates = _DATES.search(rest) or _DATES.search(role)
    if not dates:
        return m.group(0)
    d = dates.group(1)
    rest = rest.replace(d, "").strip(" ·|—-")
    role_txt = role.replace(d, "").strip(" ·|—-") + (f" <span style='font-weight:400'>· {rest}</span>" if rest else "")
    return f"<div class='entry'><span class='role'>{role_txt}</span><span class='dates'>{d}</span></div>"


def letter_html(text: str) -> str:
    paras = "".join(f"<p>{html.escape(p.strip())}</p>" for p in re.split(r"\n\s*\n", text.strip()) if p.strip())
    return f"<!doctype html><html><head><meta charset='utf-8'><style>{LETTER_CSS}</style></head><body>{paras}</body></html>"


def _pages(pdf: bytes) -> int:
    return len(PdfReader(BytesIO(pdf)).pages)


def render_cv(md_text: str, out_pdf: Path, theme: str = "modern", accent: str = "#1f5fbf", max_pages: int | None = 2,
              preview_png: Path | None = None) -> dict:
    """Renders the CV, shrinking it (down to 82%) until it fits max_pages. Returns {path, pages, scale, preview}."""
    ensure_chromium()
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 794, "height": 1123})
        scale, pdf = 1.0, b""
        for scale in (1.0, 0.95, 0.9, 0.86, 0.82, 0.78):
            page.set_content(cv_html(md_text, theme, accent, scale))
            pdf = page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
            if not max_pages or _pages(pdf) <= max_pages:
                break
        out_pdf.write_bytes(pdf)
        if preview_png:
            page.screenshot(path=str(preview_png), full_page=False)
        browser.close()
    pages = _pages(pdf)
    out = {"path": str(out_pdf), "pages": pages, "scale": scale, "theme": theme,
           "preview": str(preview_png) if preview_png else None}
    if max_pages and pages > max_pages:
        out["does_not_fit"] = (f"Even at {int(scale * 100)}% the CV takes {pages} pages (limit {max_pages}). "
                               "Shorten it: cut old or irrelevant roles and long bullets — smaller text would hurt readability.")
    return out


def render_letter(text: str, out_pdf: Path) -> str:
    ensure_chromium()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(letter_html(text))
        out_pdf.write_bytes(page.pdf(format="A4", prefer_css_page_size=True))
        browser.close()
    return str(out_pdf)


def pdf_preview(pdf_path: Path, out_png: Path, page_no: int = 0, scale: float = 1.4) -> Path | None:
    """First page of any PDF (e.g. the candidate's own CV) as PNG, for a visual design review."""
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(pdf_path))
        if page_no >= len(doc):
            return None
        img = doc[page_no].render(scale=scale).to_pil()
        out_png.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_png)
        return out_png
    except Exception:  # noqa: BLE001 — previews are optional
        return None
