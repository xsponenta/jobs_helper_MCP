"""Makes sure Playwright's Chromium is present, installing it on first use (once per process).

Output goes to stderr: inside an MCP server stdout carries the protocol."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

_ready = False


def ensure_chromium() -> None:
    global _ready
    if _ready:
        return
    with sync_playwright() as p:
        present = Path(p.chromium.executable_path).exists()
    if not present:
        print("Installing Chromium for Playwright (one time, ~150 MB)…", file=sys.stderr, flush=True)
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True,
                       stdout=sys.stderr, stderr=sys.stderr)
    _ready = True
