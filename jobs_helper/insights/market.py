"""What the market asks for a role, compared with the candidate's CV: technology demand across current vacancies,
the candidate's gaps ranked by demand, level / English / remote mix, and a learning plan."""

from __future__ import annotations

import statistics
from collections import Counter

from jobs_helper.cv.review import terms
from jobs_helper.extract import guess_english
from jobs_helper.insights.fit import expand, norm_english
from jobs_helper.models import Job

# a related skill the candidate may already have — makes a gap cheaper to close
NEIGHBOURS = {
    "postgresql": ["mysql", "sql", "oracle"], "mysql": ["postgresql", "sql"], "mongodb": ["dynamodb", "cassandra"],
    "aws": ["gcp", "azure"], "gcp": ["aws", "azure"], "azure": ["aws", "gcp"], "kubernetes": ["docker", "helm", "k8s"],
    "terraform": ["ansible", "aws"], "react": ["vue", "angular", "next.js"], "vue": ["react", "angular"],
    "angular": ["react", "vue", "typescript"], "next.js": ["react"], "typescript": ["javascript"], "node.js": ["javascript", "express"],
    "nestjs": ["node.js", "express", "angular"], "fastapi": ["flask", "django"], "django": ["flask", "fastapi"],
    "kafka": ["rabbitmq", "celery"], "rabbitmq": ["kafka", "celery"], "pytorch": ["tensorflow", "keras"],
    "tensorflow": ["pytorch", "keras"], "spark": ["pandas", "airflow"], "airflow": ["dbt", "spark"], "go": ["python", "java"],
    "kotlin": ["java"], "spring": ["java"], "playwright": ["selenium", "cypress"], "cypress": ["playwright", "selenium"],
    "github actions": ["gitlab ci", "jenkins", "ci/cd"], "gitlab ci": ["github actions", "jenkins", "ci/cd"],
    "graphql": ["rest"], "grpc": ["rest"], "redis": ["memcached"], "elasticsearch": ["postgresql"], "llm": ["nlp", "transformers"],
}


def analyze(jobs: list[Job], cv_text: str, salary: dict | None = None) -> dict:
    n = len(jobs)
    if not n:
        return {"vacancies_analyzed": 0, "hint": "No vacancies found for this role: try a broader role name."}
    demand = Counter()
    for j in jobs:
        demand.update(terms(f"{j.title}\n{' '.join(j.tags)}\n{j.description}"))
    have = expand(terms(cv_text)) if cv_text else set()
    top = [(t, c) for t, c in demand.most_common(40) if c / n >= 0.08]
    in_demand = [{"skill": t, "share": round(c / n, 2), "in_cv": t in have} for t, c in top]
    gaps = []
    for t, c in top:
        if t in have:
            continue
        share = c / n
        near = [x for x in NEIGHBOURS.get(t, []) if x in have]
        gaps.append({"skill": t, "share": round(share, 2), "priority": "high" if share >= 0.4 else "medium" if share >= 0.2 else "low",
                     "you_have_related": near or None,
                     "how_to_show": "a small public project or a work task that used it, then a CV bullet — only once it is real"})
    levels = Counter(j.seniority for j in jobs)
    years = [j.experience_years for j in jobs if j.experience_years]
    english = Counter(norm_english(j.english) or guess_english(j.description) or "not stated" for j in jobs)
    remote = sum(j.work_mode == "remote" for j in jobs)
    posted = [((j.salary_min or j.salary_max) + (j.salary_max or j.salary_min)) / 2 for j in jobs if j.salary_min or j.salary_max]
    covered = [g for g in in_demand if g["in_cv"]]
    weight_total = sum(g["share"] for g in in_demand) or 1
    out = {
        "vacancies_analyzed": n,
        "market_coverage": round(sum(g["share"] for g in covered) / weight_total, 2) if cv_text else None,
        "skills_in_demand": in_demand[:25],
        "your_strengths": [g["skill"] for g in covered][:10],
        "gaps": gaps[:12],
        "seniority_mix": {k: round(v / n, 2) for k, v in levels.most_common()},
        "required_years": {"median": statistics.median(years), "max": max(years)} if years else None,
        "english_mix": {k: round(v / n, 2) for k, v in english.most_common()},
        "remote_share": round(remote / n, 2),
        "posted_salaries": {"n": len(posted), "median": round(statistics.median(posted)), "min": round(min(posted)),
                            "max": round(max(posted))} if posted else None,
    }
    if salary:
        out["salary_survey"] = salary
    plan = []
    for g in gaps[:6]:
        step = (f"Build on {', '.join(g['you_have_related'])}: the jump to {g['skill']} is short." if g["you_have_related"]
                else f"Learn {g['skill']} from the basics.")
        plan.append({"skill": g["skill"], "priority": g["priority"], "asked_in": f"{int(g['share'] * 100)}% of vacancies", "step": step})
    out["learning_plan"] = plan
    out["note"] = ("Based on current postings only (one snapshot of the market). Never add a skill to the CV before the "
                   "candidate actually has it.")
    return out
