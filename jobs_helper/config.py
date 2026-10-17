"""Project paths and tunables. Everything can be overridden through environment variables.

Two locations:
- ROOT: the source tree (snapshots, evaluation, runs) — only meaningful in a git checkout;
- HOME: the candidate's own data (candidate.yaml, settings.yaml, browser sessions, tokens, tracker).
  In a git checkout HOME = ROOT (development); when installed (uvx, Claude Desktop extension)
  HOME = ~/.jobs_helper. JOBS_HOME overrides both.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IS_CHECKOUT = (ROOT / ".git").exists()
HOME = Path(os.environ.get("JOBS_HOME") or (ROOT if IS_CHECKOUT else Path.home() / ".jobs_helper")).expanduser()

CACHE_DIR = Path(os.environ.get("JOBS_CACHE_DIR", HOME / ".cache"))
HTTP_CACHE_DIR = CACHE_DIR / "http"
HM_INDEX_PATH = CACHE_DIR / "happymonday_index.json"

DATA_DIR = Path(os.environ.get("JOBS_DATA_DIR", ROOT / "data"))
SNAPSHOT_DIR = DATA_DIR / "snapshots"

USER_AGENT = os.environ.get(
    "JOBS_USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36",
)
MIN_DELAY_S = float(os.environ.get("JOBS_MIN_DELAY", "1.0"))  # between requests to one host
FEED_TTL_S = 30 * 60
PAGE_TTL_S = 24 * 3600
