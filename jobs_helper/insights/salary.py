"""Salary ranges from DOU's open salary survey (github.com/devua/csv, ~14k answers per wave, USD per month, net).

Only people currently working in IT are counted; obvious outliers are dropped. Small samples are reported as such.
"""

from __future__ import annotations

import csv
import io
import json
import re
import statistics
import time
from pathlib import Path

import httpx

from jobs_helper import config

LIST_URL = "https://api.github.com/repos/devua/csv/contents/salaries"
RAW_URL = "https://raw.githubusercontent.com/devua/csv/master/salaries/{name}"
TTL = 30 * 86400
LEVELS = {"intern": "Intern", "junior": "Junior", "middle": "Middle", "senior": "Senior", "lead": "Lead", "head": "Head",
          "manager": "Manager", "architect": "Architect", "principal": "Principal"}
LANG_ALIASES = {"js": "JavaScript", "javascript": "JavaScript", "ts": "TypeScript", "typescript": "TypeScript", "python": "Python",
                "java": "Java", "c#": "C# / .NET", ".net": "C# / .NET", "dotnet": "C# / .NET", "php": "PHP", "go": "Go",
                "golang": "Go", "kotlin": "Kotlin", "swift": "Swift", "ruby": "Ruby", "rust": "Rust", "scala": "Scala",
                "c++": "C++", "dart": "Dart", "flutter": "Dart", "react": "JavaScript", "node": "JavaScript", "node.js": "JavaScript"}
SPHERE_PREFIX = "У якій сфері проєкт в якому ви зараз працюєте? ("


def _cache_dir() -> Path:
    d = config.CACHE_DIR / "salaries"
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest_file() -> Path:
    """Newest survey wave, downloaded once and cached for 30 days."""
    cache = _cache_dir()
    index = cache / "index.json"
    names: list[str] = []
    if index.exists() and time.time() - index.stat().st_mtime < TTL:
        names = json.loads(index.read_text(encoding="utf-8"))
    else:
        try:
            r = httpx.get(LIST_URL, timeout=30, headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            names = sorted(x["name"] for x in r.json() if re.match(r"\d{4}_(june|dec)_raw\.csv$", x["name"]))
            index.write_text(json.dumps(names), encoding="utf-8")
        except Exception:  # noqa: BLE001 — fall back to whatever is cached
            names = sorted(p.name for p in cache.glob("*_raw.csv"))
    if not names:
        raise RuntimeError("DOU salary data is unavailable (no network and no cache).")
    order = lambda n: (int(n[:4]), 1 if "_dec_" in n else 0)  # noqa: E731
    name = max(names, key=order)
    path = cache / name
    if not path.exists():
        r = httpx.get(RAW_URL.format(name=name), timeout=120, follow_redirects=True)
        r.raise_for_status()
        path.write_bytes(r.content)
    return path


_rows_cache: dict[str, list[dict]] = {}


def load_rows(path: Path | None = None) -> list[dict]:
    path = path or latest_file()
    if str(path) in _rows_cache:
        return _rows_cache[str(path)]
    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8", errors="replace")))
    rows = []
    for r in reader:
        if not (r.get("Ваша основна зайнятість зараз") or "").startswith("Працюю в ІТ"):
            continue
        try:
            income = float(re.sub(r"[^\d.]", "", r.get("ЗАРПЛАТА СУМАРНИЙ ДОХІД в ІТ") or "") or 0)
        except ValueError:
            continue
        if not 150 <= income <= 30000:
            continue
        exp_raw = (r.get("Загальний стаж роботи за нинішньою ІТ-спеціальністю") or "").lower()
        m = re.search(r"\d+", exp_raw)
        exp = float(m.group(0)) if m else (0.5 if "менше" in exp_raw or "пів" in exp_raw else None)
        spheres = [k[len(SPHERE_PREFIX):-1] for k, v in r.items() if k and k.startswith(SPHERE_PREFIX) and v == "обрано"]
        rows.append({"income": income, "level": r.get("Title_clean") or "", "category": r.get("Категорії") or "",
                     "position": r.get("Посади") or "", "language": r.get("Основна мова програмування") or "",
                     "company_type": r.get("Основний напрям роботи компанії в якій працюєте") or "",
                     "size": r.get("Кількість спеціалістів у вашій компанії в Україні") or "", "experience": exp,
                     "english": r.get("Знання англійської мови") or "", "spheres": spheres,
                     "abroad": (r.get("Де ви зараз живете?") or "").startswith("За кордоном")})
    _rows_cache[str(path)] = rows
    return rows


def _stats(values: list[float]) -> dict:
    v = sorted(values)
    if not v:
        return {"n": 0}
    q = statistics.quantiles(v, n=20) if len(v) >= 20 else None
    pick = lambda p: round(q[int(p / 5) - 1]) if q else round(v[min(len(v) - 1, int(len(v) * p / 100))])  # noqa: E731
    return {"n": len(v), "p25": pick(25), "median": round(statistics.median(v)), "p75": pick(75), "p90": pick(90)}


def query(role: str | None = None, level: str | None = None, language: str | None = None, sphere: str | None = None,
          company_type: str | None = None, min_years: float | None = None, max_years: float | None = None,
          in_ukraine_only: bool = True) -> dict:
    rows = load_rows()
    filters = []

    def keep(r: dict) -> bool:
        if in_ukraine_only and r["abroad"]:
            return False
        if role:
            hay = f"{r['category']} {r['position']} {r['language']}".lower()
            if not all(t in hay for t in re.findall(r"[\w#+.]+", role.lower()) if t not in ("developer", "engineer", "specialist")):
                return False
        if level and LEVELS.get(level.lower(), level).lower() not in r["level"].lower():
            return False
        if language and LANG_ALIASES.get(language.lower(), language).lower() != r["language"].lower():
            return False
        if sphere and not any(sphere.lower() in s.lower() for s in r["spheres"]):
            return False
        if company_type and company_type.lower()[:6] not in r["company_type"].lower():
            return False
        if min_years is not None and (r["experience"] is None or r["experience"] < min_years):
            return False
        if max_years is not None and (r["experience"] is None or r["experience"] > max_years):
            return False
        return True

    for name, val in (("role", role), ("level", level), ("language", language), ("sphere", sphere), ("company_type", company_type),
                      ("min_years", min_years), ("max_years", max_years)):
        if val not in (None, ""):
            filters.append(f"{name}={val}")
    sel = [r for r in rows if keep(r)]
    out = {"source": f"DOU salary survey, wave {latest_file().name[:-8]} (net USD / month, people working in IT"
                     + (", living in Ukraine)" if in_ukraine_only else ")"),
           "filters": filters, "overall": _stats([r["income"] for r in sel])}
    if out["overall"]["n"] and out["overall"]["n"] < 15:
        out["warning"] = "Small sample: treat the numbers as indicative only."
    if sel and not level:
        out["by_level"] = {lvl: _stats([r["income"] for r in sel if r["level"].startswith(lvl)])
                           for lvl in ("Junior", "Middle", "Senior", "Tech Lead", "Team Lead") if any(r["level"].startswith(lvl) for r in sel)}
    if sel:
        types = {}
        for t in ("Продуктова", "Сервісна", "Аутстафінгова", "Стартап"):
            st = _stats([r["income"] for r in sel if r["company_type"].startswith(t)])
            if st["n"] >= 10:
                types[t] = st
        out["by_company_type"] = types
        sph: dict[str, list[float]] = {}
        for r in sel:
            for s in r["spheres"]:
                sph.setdefault(s, []).append(r["income"])
        out["by_sphere"] = dict(sorted(((s, _stats(v)) for s, v in sph.items() if len(v) >= 10),
                                       key=lambda kv: -kv[1]["median"])[:8])
    return out
