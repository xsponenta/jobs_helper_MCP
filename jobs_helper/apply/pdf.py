"""Markdown -> PDF for uploading the tailored CV / cover letter (headless Chromium, A4)."""

from __future__ import annotations

from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

CSS = """
body { font: 10.5pt/1.4 -apple-system, 'Segoe UI', Roboto, Arial, sans-serif; color: #111; margin: 0; }
h1 { font-size: 20pt; margin: 0 0 4pt; } h2 { font-size: 12.5pt; margin: 14pt 0 4pt; border-bottom: 1px solid #ccc; }
h3 { font-size: 11pt; margin: 10pt 0 2pt; } p, li { margin: 2pt 0; } ul { padding-left: 16pt; margin: 2pt 0; }
a { color: #111; text-decoration: none; }
"""


def render_pdfs(docs: dict[str, str], out_dir: Path) -> dict[str, Path]:
    """{'cv': markdown, 'cover_letter': markdown} -> {'cv': cv.pdf, ...} in out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        for name, md in docs.items():
            html = markdown.markdown(md, extensions=["extra", "sane_lists"])
            page.set_content(f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{html}</body></html>")
            path = out_dir / f"{name}.pdf"
            page.pdf(path=str(path), format="A4", margin={"top": "16mm", "bottom": "16mm", "left": "16mm", "right": "16mm"})
            paths[name] = path
        browser.close()
    return paths
