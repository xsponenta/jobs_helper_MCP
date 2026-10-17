"""Polite HTTP: per-host rate limit, on-disk cache, redirect resolution without bodies."""

from __future__ import annotations

import hashlib
import threading
import time
from urllib.parse import urlparse

import httpx

from jobs_helper import config

_lock = threading.Lock()
_last_hit: dict[str, float] = {}
_client = httpx.Client(
    headers={"User-Agent": config.USER_AGENT, "Accept-Language": "uk,en;q=0.8"},
    timeout=25,
)


class FetchError(Exception):
    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"{url}: {message}")
        self.url, self.status = url, status


def _throttle(host: str) -> None:
    with _lock:
        wait = config.MIN_DELAY_S - (time.monotonic() - _last_hit.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.monotonic()


def _cache_path(url: str):
    return config.HTTP_CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".body")


def fetch(url: str, ttl: int = config.PAGE_TTL_S, use_cache: bool = True) -> bytes:
    """GET with redirects. Cached responses younger than `ttl` seconds are reused."""
    path = _cache_path(url)
    if use_cache and path.exists() and time.time() - path.stat().st_mtime < ttl:
        return path.read_bytes()
    _throttle(urlparse(url).netloc)
    try:
        r = _client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        raise FetchError(url, None, f"{type(e).__name__}: {e}") from e
    if r.status_code >= 400:
        raise FetchError(url, r.status_code, f"HTTP {r.status_code}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return r.content


def fetch_text(url: str, ttl: int = config.PAGE_TTL_S, use_cache: bool = True) -> str:
    return fetch(url, ttl, use_cache).decode("utf-8", errors="replace")


def resolve_redirects(url: str, max_hops: int = 6) -> str:
    """Follow redirects hop by hop (HEAD, falling back to GET) and return the final URL."""
    current = url
    for _ in range(max_hops):
        _throttle(urlparse(current).netloc)
        try:
            r = _client.head(current, follow_redirects=False)
            if r.status_code in (403, 405):
                r = _client.get(current, follow_redirects=False)
        except httpx.HTTPError:
            break
        loc = r.headers.get("location")
        if r.status_code not in (301, 302, 303, 307, 308) or not loc:
            break
        current = str(httpx.URL(current).join(loc))
    return current
