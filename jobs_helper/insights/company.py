"""Company insight from DOU: profile, employee rating, reviews analysed by theme and tone, business spheres.

Reviewers' names are not kept — only their role and date. Themes and tone come from transparent keyword rules;
the review excerpts are returned too, so the model can summarise them in its own words.
"""

from __future__ import annotations

import re
from collections import Counter

from bs4 import BeautifulSoup

from jobs_helper.cv.naming import translit
from jobs_helper.http import FetchError, fetch_text

THEMES = {
    "compensation": r"зарплат|зп\b|компенсац|бонус|перегляд|salary|compensation|raise|індексац|недоплач",
    "management": r"менеджмент|керівництв|керівник|менеджер|management|manager|лід\b|тімлід|c-level|топ-менедж",
    "workload": r"овертайм|переробк|навантаж|дедлайн|вигоран|overtime|burnout|workload|понаднормов|вихідн",
    "culture": r"атмосфер|культур|колектив|команда|toxic|токсичн|culture|team spirit|дружн|мікроменедж",
    "processes": r"процес|бюрократ|хаос|планування|agile|scrum|process|organization|організац",
    "career": r"кар'єр|карʼєр|ріст|розвиток|навчанн|менторств|career|growth|promotion|підвищ",
    "layoffs": r"скорочен|звільн|layoff|бенч\b|bench|оптимізац",
    "hiring_process": r"співбесід|інтерв'ю|інтервʼю|рекрутер|тестов|hiring|interview|recruiter|фідбек|feedback|оффер|офер",
    "work_format": r"віддален|ремоут|remote|офіс|office|гібрид|hybrid|релокац",
    "product": r"продукт|проєкт|проект|технолог|стек|product|project|tech stack|legacy|легасі",
}
POSITIVE = r"класн|чудов|супер|подобаєт|рекоменд|комфорт|приємн|цікав|вдячн|топ\b|найкращ|адекватн|підтрим|прозор|гнучк|" \
           r"great|good|excellent|recommend|friendly|supportive|love|awesome|transparent|flexible"
NEGATIVE = r"жах|погано|токсичн|хаос|не рекоменд|розчар|проблем|бюрократ|ігнор|затримк|не вистач|мікроменедж|тиск|" \
           r"вигоран|звільн|скорочен|bad|terrible|toxic|chaos|disappoint|ignored|problem|pressure|burnout|layoff|unfair"
SPHERES = {
    "Fintech / Banking": r"fintech|банк|bank|payment|платіж|insurance|страхов|trading|трейдинг|crypto exchange",
    "E-commerce / Retail": r"e-commerce|ecommerce|marketplace|маркетплейс|retail|ритейл|інтернет-магазин",
    "GameDev": r"gamedev|game dev|\bgames?\b|ігор|ігри|ігров|unity|unreal",
    "Gambling / iGaming": r"igaming|gambling|casino|казино|betting|ставк|беттинг",
    "DefTech / MilTech": r"deftech|miltech|defen[cs]e|оборон|військ|дрон|drone|ЗСУ",
    "Healthcare / MedTech": r"health|medtech|medical|медич|clinic|клінік|wellbeing|fitness|фітнес",
    "EdTech": r"edtech|education|освіт|learning platform|навчальн",
    "AdTech / Marketing": r"adtech|advertis|реклам|marketing tech|martech",
    "AI / Data": r"\bai\b|штучн|machine learning|\bml\b|data platform|analytics platform|llm",
    "Security / Cybersecurity": r"cyber|кібер|security system|безпек|охоронн|antivirus",
    "Embedded / IoT / Hardware": r"embedded|iot|hardware|electronics|електронік|пристро|device|robot|робот|виробник",
    "Logistics": r"logistic|логіст|supply chain|delivery|доставк|fleet",
    "Travel / Hospitality": r"travel|подорож|hotel|готел|booking|туризм|horeca",
    "Media / Consumer apps": r"media|медіа|streaming|dating|знайомств|consumer app|social network|соцмереж|utilities|lifestyle",
    "Enterprise / SaaS": r"\bsaas\b|\berp\b|\bcrm\b|b2b|enterprise",
    "Blockchain / Web3": r"blockchain|блокчейн|web3|defi|crypto|крипт|\bnft\b",
    "Telecom / Cloud": r"telecom|телеком|cloud|хмарн|hosting|хостинг",
    "Automotive": r"automotive|автомоб|vehicle|\bcar\b",
    "Real Estate / PropTech": r"real estate|нерухом|proptech",
    "Energy / Industry / Agro": r"energy|енерг|manufacturing|виробництв|agri|агро|industrial",
    "GovTech / Public": r"govtech|держав|public services|e-gov|дія\b",
}


def _slug_candidates(name: str) -> list[str]:
    base = translit(name).lower()
    base = re.sub(r"\(.*?\)", " ", base)
    words = re.findall(r"[a-z0-9]+", base)
    stop = {"inc", "llc", "ltd", "group", "corp", "company", "co", "ukraine", "ua", "gmbh", "sp", "z", "oo", "tov", "tob"}
    core = [w for w in words if w not in stop]
    cands = ["-".join(words), "-".join(core), "".join(core), core[0] if core else ""]
    return [c for i, c in enumerate(cands) if c and c not in cands[:i]]


def find_dou_slug(company: str | None = None, job_id: str | None = None) -> str | None:
    if job_id and job_id.startswith("dou:"):
        return job_id[4:].split("/")[0]
    if not company:
        return None
    for slug in _slug_candidates(company):
        try:
            html = fetch_text(f"https://jobs.dou.ua/companies/{slug}/", ttl=7 * 86400)
        except FetchError:
            continue
        s = BeautifulSoup(html, "lxml")
        title = s.select_one(".company-info .g-h2, .company-info h1, h1")
        name_on_page = (title.get_text(" ", strip=True) if title else s.title.get_text() if s.title else "").lower()
        key = re.findall(r"[a-zа-яіїєґ0-9]+", company.lower())[:1]
        if not key or key[0] in name_on_page or key[0] in translit(name_on_page):
            return slug
    return None


def _num(text: str, pattern: str) -> float | None:
    m = re.search(pattern, text)
    return float(m.group(1).replace(",", ".")) if m else None


def profile(slug: str) -> dict:
    s = BeautifulSoup(fetch_text(f"https://jobs.dou.ua/companies/{slug}/", ttl=86400), "lxml")
    text = s.get_text(" ", strip=True)
    info = s.select_one(".company-info")
    about = s.select_one(".b-company-about .b-typo")
    sub = {k: _num(text, rf"{re.escape(lbl)}\s*(\d+)%") for k, lbl in (
        ("compensation", "Компенсація"), ("conditions", "Умови праці"), ("career", "Кар’єра (самореалізація)"),
        ("project", "Проєкт (продукт)"), ("culture", "Корпоративна культура"), ("social_responsibility", "Соціальна відповідальність"))}
    size = re.search(r"((?:понад|до|від)?\s*\d+(?:\s*(?:\.\.\.|…|–|-)\s*\d+)?)\s*спеціаліст", info.get_text(" ", strip=True) if info else text)
    business = re.search(r"Основний бізнес:\s*([^\n©]+?)(?:\s+Є резидентом|\s+©|$)", text)
    site = next((a.get("href") for a in (info.select("a[href^=http]") if info else []) if "dou.ua" not in a.get("href", "")), None)
    return {
        "dou_url": f"https://jobs.dou.ua/companies/{slug}/",
        "size": size.group(1).strip() if size else None,
        "offices": s.select_one(".offices").get_text(" ", strip=True) if s.select_one(".offices") else None,
        "website": site, "main_business": business.group(1).strip() if business else None,
        "diia_city": "резидентом Diia City" in text,
        "rating": {"score_100": _num(text, r"Оцінка компанії:\s*(\d+)/100"), "votes": _num(text, r"Проголосува\w*\s*(\d+)"),
                   **{k: v for k, v in sub.items() if v is not None}},
        "about": about.get_text(" ", strip=True)[:1200] if about else "",
    }


def reviews(slug: str, limit: int = 60) -> list[dict]:
    try:
        s = BeautifulSoup(fetch_text(f"https://jobs.dou.ua/companies/{slug}/reviews/", ttl=86400), "lxml")
    except FetchError:
        return []
    out = []
    for c in s.select(".b-comment")[:limit]:
        body = c.select_one(".l-text")
        head = c.select_one(".b-post-author")
        head_txt = head.get_text(" ", strip=True) if head else ""
        date = re.search(r"(\d{1,2}\s+[а-яіїєґ]+(?:\s+\d{4})?)\s+\d{1,2}:\d{2}", head_txt)
        if body and body.get_text(strip=True):
            out.append({"role": c.select_one(".prof").get_text(" ", strip=True) if c.select_one(".prof") else None,
                        "date": date.group(1) if date else None, "text": body.get_text(" ", strip=True)})
    return out


def analyze_reviews(items: list[dict]) -> dict:
    themes: dict[str, dict] = {}
    tone = Counter()
    for r in items:
        low = r["text"].lower()
        pos, neg = len(re.findall(POSITIVE, low)), len(re.findall(NEGATIVE, low))
        r_tone = "positive" if pos > neg else "negative" if neg > pos else "mixed"
        tone[r_tone] += 1
        for theme, pat in THEMES.items():
            for sent in re.split(r"(?<=[.!?])\s+", r["text"]):
                if re.search(pat, sent.lower()):
                    t = themes.setdefault(theme, {"mentions": 0, "positive": 0, "negative": 0, "quotes": []})
                    t["mentions"] += 1
                    sp, sn = len(re.findall(POSITIVE, sent.lower())), len(re.findall(NEGATIVE, sent.lower()))
                    t["positive"] += sp > sn
                    t["negative"] += sn > sp
                    if len(t["quotes"]) < 2 and 40 < len(sent) < 260:
                        t["quotes"].append(sent.strip())
    ranked = dict(sorted(themes.items(), key=lambda kv: -kv[1]["mentions"]))
    concerns = [k for k, v in ranked.items() if v["negative"] > v["positive"] and v["mentions"] >= 2]
    strengths = [k for k, v in ranked.items() if v["positive"] > v["negative"] and v["mentions"] >= 2]
    return {"count": len(items), "tone": dict(tone), "themes": ranked, "strengths": strengths, "concerns": concerns}


def spheres(*texts: str) -> list[str]:
    blob = " ".join(t for t in texts if t).lower()
    hits = {name: len(re.findall(pat, blob, re.I)) for name, pat in SPHERES.items()}
    return [name for name, n in sorted(hits.items(), key=lambda kv: -kv[1]) if n][:4]


def company_insights(company: str | None = None, job_id: str | None = None, vacancy_text: str = "") -> dict:
    slug = find_dou_slug(company, job_id)
    if not slug:
        return {"company": company, "found_on_dou": False, "spheres": spheres(vacancy_text),
                "hint": "The company was not found on DOU; judge it by the vacancy and its own website."}
    try:
        prof = profile(slug)
    except FetchError as e:
        return {"company": company, "found_on_dou": False, "error": str(e), "spheres": spheres(vacancy_text)}
    revs = reviews(slug)
    analysis = analyze_reviews(revs)
    rating = prof["rating"]
    verdict = []
    if rating.get("score_100") and (rating.get("votes") or 0) >= 10:
        verdict.append(f"employee rating {rating['score_100']:g}/100 from {rating['votes']:g} votes")
    if analysis["concerns"]:
        verdict.append("recurring concerns: " + ", ".join(analysis["concerns"]))
    if analysis["strengths"]:
        verdict.append("praised for: " + ", ".join(analysis["strengths"]))
    return {"company": company or slug, "found_on_dou": True, **prof,
            "spheres": ([prof["main_business"]] if prof.get("main_business") else []) + spheres(prof["about"], vacancy_text),
            "reviews": analysis, "summary": "; ".join(verdict) or "few signals on DOU",
            "review_excerpts": [{"role": r["role"], "date": r["date"], "text": r["text"][:500]} for r in revs[:12]],
            "note": "Reviews are self-selected and skew to extremes; read several before concluding."}
