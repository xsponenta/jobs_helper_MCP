"""Command line entry point:  uv run python -m jobs_helper <command> ..."""

from __future__ import annotations

import argparse
import json
import sys

from jobs_helper.models import ALL_SOURCES, SearchQuery
from jobs_helper.snapshot import SnapshotStore, latest_snapshot, take_snapshot
from jobs_helper.sources import get_job, get_source, search_all
from jobs_helper.sources.base import SourceError

DEFAULT_SNAPSHOT_QUERIES = ["python", "react", "qa", "data science", "devops", "java"]


def _query(args) -> SearchQuery:
    return SearchQuery(
        keywords=args.keywords, sources=args.sources, remote=args.remote or None, city=args.city,
        min_salary=args.min_salary, max_experience_years=args.max_exp, limit=args.limit,
    )


def _print_jobs(jobs) -> None:
    for j in jobs:
        s = j.short()
        salary = f" | {s['salary']}" if s["salary"] else ""
        exp = f" | {s['experience_years']}y" if s["experience_years"] else ""
        print(f"{s['job_id']:<48} {s['title'][:55]:<55} | {s['company'] or '?'} | {s['work_mode']}{salary}{exp}")


def cmd_search(args) -> int:
    q = _query(args)
    if args.snapshot:
        path = latest_snapshot() if args.snapshot == "latest" else args.snapshot
        jobs, errors = SnapshotStore(path).search(q), {}
    else:
        jobs, errors = search_all(q)
    if args.json:
        print(json.dumps({"jobs": [j.short() for j in jobs], "errors": errors}, ensure_ascii=False, indent=1))
    else:
        _print_jobs(jobs)
        print(f"\n{len(jobs)} jobs" + (f"; source errors: {errors}" if errors else ""))
    return 0


def cmd_get(args) -> int:
    try:
        job = get_job(args.job_id)
    except SourceError as e:
        print(json.dumps({"error": e.code, "message": e.message, "hint": e.hint}, ensure_ascii=False))
        return 1
    data = job.model_dump()
    if not args.full:
        data["description"] = data["description"][:600] + ("…" if len(data["description"]) > 600 else "")
    print(json.dumps(data, ensure_ascii=False, indent=1))
    return 0


def cmd_hm_sync(args) -> int:
    print(get_source("happymonday").sync(args.budget))
    return 0


def cmd_snapshot(args) -> int:
    queries = [SearchQuery(keywords=k, limit=args.limit) for k in (args.query or DEFAULT_SNAPSHOT_QUERIES)]
    print(f"Taking snapshot for {len(queries)} queries…")
    path = take_snapshot(queries, name=args.name, with_details=not args.no_details)
    print(f"Saved {path}")
    return 0


def cmd_policy(args) -> int:
    from jobs_helper import policy as pol
    if args.action == "set":
        if not args.site or not args.value:
            print("usage: policy set <site> <manual|confirm|auto>")
            return 2
        if args.site == "dou" and args.value == "auto":
            print("Увага: " + pol.SITE_NOTES["dou"])
        pol.set_policy(args.site, args.value)
    explicit = pol.explicit_policies()
    for site, value in pol.load_policies().items():
        mark = "" if site in explicit else "(default)"
        print(f"{site:<12} {value:<8} {mark:<10} {pol.SITE_NOTES[site]}")
    return 0


LOGIN_URLS = {
    "djinni": "https://djinni.co/login",
    "dou": "https://dou.ua/login/",
    "happymonday": "https://happymonday.ua/profile/login",
}


def cmd_login(args) -> int:
    """Opens a visible browser with the candidate's persistent profile; the human logs in and closes the window.
    The password never reaches the agents or this program."""
    from playwright.sync_api import sync_playwright

    from jobs_helper.apply.browser import PROFILES_DIR
    if args.site == "linkedin":
        from jobs_helper.linkedin.oauth import oauth_login
        print(oauth_login())
        return 0
    profile = PROFILES_DIR / args.site
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(profile), headless=False, locale="uk-UA")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(LOGIN_URLS[args.site])
        print(f"Увійди в акаунт {args.site} у відкритому вікні, потім закрий вікно браузера.")
        try:
            page.wait_for_event("close", timeout=0)
        finally:
            ctx.close()
    print(f"Сесію збережено в {profile}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jobs_helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="search vacancies (live or in a snapshot)")
    s.add_argument("keywords")
    s.add_argument("--sources", type=lambda v: v.split(","), default=list(ALL_SOURCES))
    s.add_argument("--remote", action="store_true")
    s.add_argument("--city")
    s.add_argument("--min-salary", type=int)
    s.add_argument("--max-exp", type=float, help="candidate experience in years")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--snapshot", help="path to a snapshot file or 'latest'")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_search)

    g = sub.add_parser("get", help="full vacancy by id")
    g.add_argument("job_id")
    g.add_argument("--full", action="store_true", help="do not truncate the description")
    g.set_defaults(fn=cmd_get)

    h = sub.add_parser("hm-sync", help="refresh the Happy Monday index")
    h.add_argument("--budget", type=int, default=100)
    h.set_defaults(fn=cmd_hm_sync)

    n = sub.add_parser("snapshot", help="freeze current vacancies for evaluation")
    n.add_argument("--query", action="append", help="repeatable; default: a fixed set of popular queries")
    n.add_argument("--limit", type=int, default=25)
    n.add_argument("--name")
    n.add_argument("--no-details", action="store_true")
    n.set_defaults(fn=cmd_snapshot)

    pp = sub.add_parser("policy", help="show or set the per-site submit policy (settings.yaml)")
    pp.add_argument("action", choices=["show", "set"])
    pp.add_argument("site", nargs="?", choices=["dou", "djinni", "happymonday", "ats", "linkedin"])
    pp.add_argument("value", nargs="?", choices=["manual", "confirm", "auto"])
    pp.set_defaults(fn=cmd_policy)

    lg = sub.add_parser("login", help="log in to a job board (browser) or LinkedIn (OAuth) as the candidate")
    lg.add_argument("site", choices=["djinni", "dou", "happymonday", "linkedin"])
    lg.set_defaults(fn=cmd_login)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
