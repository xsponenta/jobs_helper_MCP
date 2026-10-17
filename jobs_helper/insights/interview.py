"""Interview preparation material for one vacancy. The server collects facts and question banks; the model turns them
into answers / STAR stories using ONLY the candidate's real facts, and runs the mock interview in chat."""

from __future__ import annotations

import re

from jobs_helper.cv.review import _bullets, terms, timeline
from jobs_helper.insights.fit import candidate_context, fit
from jobs_helper.models import Job

TOPICS = {
    "python": ["GIL and when threads / processes / asyncio fit", "generators and iterators", "mutable default arguments",
               "decorators and context managers", "how dict and set work inside"],
    "django": ["ORM N+1 and select_related / prefetch_related", "migrations on a large table", "middleware and the request cycle",
               "transactions and select_for_update"],
    "fastapi": ["dependency injection", "async endpoints vs blocking code", "pydantic validation", "background tasks"],
    "flask": ["app / request context", "blueprints", "scaling a Flask app"],
    "javascript": ["event loop, microtasks vs macrotasks", "closures and this", "promises vs async/await", "prototype chain"],
    "typescript": ["generics and utility types", "type narrowing", "unknown vs any", "structural typing"],
    "react": ["reconciliation and keys", "useEffect pitfalls and dependency arrays", "memoization (memo, useMemo, useCallback)",
              "state management choices", "rendering performance"],
    "next.js": ["SSR vs SSG vs ISR", "server components", "data fetching and caching"],
    "vue": ["reactivity system", "composition vs options API", "computed vs watch"],
    "angular": ["change detection", "RxJS operators", "dependency injection"],
    "node.js": ["event loop and blocking", "streams and backpressure", "error handling in async code", "clustering"],
    "java": ["JVM memory and GC", "equals / hashCode contract", "concurrency (synchronized, volatile, executors)", "collections internals"],
    "spring": ["bean lifecycle and scopes", "transactions (@Transactional pitfalls)", "Spring Boot auto-configuration"],
    "c#": ["value vs reference types", "async/await and deadlocks", "LINQ deferred execution", "IDisposable"],
    ".net": ["DI lifetimes", "middleware pipeline", "EF Core tracking and performance"],
    "go": ["goroutines and channels", "context cancellation", "interfaces and embedding", "race conditions"],
    "php": ["request lifecycle", "composer autoloading", "OPcache", "PHP 8 features"],
    "laravel": ["service container", "Eloquent N+1", "queues"],
    "sql": ["JOIN types", "indexes and when they are not used", "window functions", "transaction isolation levels"],
    "postgresql": ["EXPLAIN ANALYZE", "MVCC and VACUUM", "partial / composite indexes", "locks"],
    "mysql": ["InnoDB indexes", "replication", "isolation levels"],
    "mongodb": ["schema design: embed vs reference", "indexes", "aggregation pipeline"],
    "redis": ["data structures and use cases", "persistence", "cache invalidation", "distributed locks"],
    "kafka": ["partitions and consumer groups", "delivery semantics", "ordering guarantees"],
    "rabbitmq": ["exchanges and routing", "acks and redelivery", "dead-letter queues"],
    "docker": ["image layers and caching", "multi-stage builds", "container vs VM"],
    "kubernetes": ["pods, deployments, services", "liveness vs readiness probes", "resource requests / limits", "rolling updates"],
    "aws": ["IAM roles and policies", "choosing EC2 / Lambda / ECS", "S3 consistency and lifecycle", "VPC basics"],
    "terraform": ["state and locking", "modules", "plan / apply drift"],
    "ci/cd": ["pipeline design", "deployment strategies (blue-green, canary)", "secrets in CI"],
    "rest": ["idempotency", "status codes", "versioning", "pagination"],
    "graphql": ["N+1 and dataloaders", "schema design", "caching"],
    "microservices": ["service boundaries", "distributed transactions / sagas", "observability"],
    "pandas": ["groupby / merge", "vectorization vs apply", "memory with large frames"],
    "machine learning": ["bias / variance", "train / validation / test and leakage", "metric choice for imbalanced data"],
    "pytorch": ["autograd", "training loop", "GPU memory"],
    "llm": ["RAG pipeline design", "evaluation of LLM outputs", "prompt injection", "fine-tuning vs prompting"],
    "nlp": ["tokenization", "embeddings", "transformer attention"],
    "spark": ["lazy evaluation", "shuffles and partitioning", "joins and skew"],
    "airflow": ["DAG design and idempotency", "backfills", "sensors and retries"],
    "selenium": ["waits", "page object pattern", "flaky tests"],
    "playwright": ["auto-waiting", "fixtures", "parallel runs"],
    "qa automation": ["test pyramid", "what to automate", "flaky test triage", "test data management"],
    "agile": ["sprint ceremonies and their purpose", "estimation", "handling scope change"],
    "scrum": ["role of the Scrum Master / PO", "velocity misuse", "Definition of Done"],
    "jira": ["workflow design", "reporting"],
    "stakeholder management": ["conflicting priorities", "saying no", "status reporting"],
    "risk management": ["risk register", "mitigation vs contingency"],
    "figma": ["design systems", "handoff to developers"],
    "user research": ["interview techniques", "synthesis", "usability testing"],
}
BEHAVIORAL = [
    "Tell me about yourself (2 minutes: current role → key results → why this role).",
    "Describe a project you are most proud of. What was your exact contribution?",
    "Tell me about a time you disagreed with a teammate or manager. How was it resolved?",
    "Describe a failure or mistake. What did you change afterwards?",
    "Tell me about a tight deadline. How did you prioritise?",
    "When did you have to learn something new quickly?",
    "Give an example of feedback you received and what you did with it.",
    "Why are you looking for a new job, and why this company?",
]
LEAD = [
    "How do you handle an under-performing team member?",
    "Tell me about a decision you made with incomplete information.",
    "How do you balance delivery pressure against technical debt / quality?",
]
ASK = [
    "What does success look like in the first 3 months?",
    "What does a typical week look like for this role?",
    "How is the team organised and who will I work with most?",
    "What are the biggest challenges the team faces right now?",
    "What are the next steps and the timeline of the hiring process?",
]


def requirements(description: str) -> list[str]:
    lines = [ln.strip(" -•*·\t") for ln in description.splitlines()]
    req = [ln for ln in lines if 15 < len(ln) < 220 and (re.search(r"\d\+?\s*(?:years?|рок|років)|experience|досвід|knowledge|знання|"
                                                                      r"understanding|розумін|proficien|familiar|вміння|skills?", ln, re.I))]
    return req[:14]


def risk_questions(cv_text: str, job: Job, ctx: dict, f: dict) -> list[dict]:
    tl = timeline(cv_text)
    out = []
    for g in tl["gaps"][:2]:
        out.append({"question": f"What did you do between {g['between']}?",
                    "why": f"A {g['months']}-month gap in the CV.", "prepare": "A short, honest answer: what you did and what you learned."})
    for p in tl["short_tenures"][:2]:
        out.append({"question": f"Why did you stay only {p['months']} months at: {p['line'][:60]}?",
                    "why": "Short tenure.", "prepare": "Factual reason (project ended, relocation, …), no blame."})
    for k in f.get("knockouts", []):
        out.append({"question": "The role asks for more than your CV shows — why should we consider you?" if "years" in k or "level" in k
                    else "How comfortable are you working in English daily?" if "English" in k else k,
                    "why": k, "prepare": "Concrete evidence of equivalent work; never overstate."})
    for skill in f.get("missing", [])[:4]:
        out.append({"question": f"Have you worked with {skill}?", "why": f"{skill} is in the vacancy, not in the CV.",
                    "prepare": "Say honestly what you know, the closest thing you used, and how you would ramp up."})
    return out


def star_material(cv_text: str, job_text: str) -> list[dict]:
    wanted = terms(job_text)
    scored = []
    for b in _bullets(cv_text):
        rel = len(terms(b) & wanted)
        has_num = bool(re.search(r"\d", b))
        scored.append((rel * 2 + has_num, b))
    scored.sort(key=lambda x: -x[0])
    return [{"fact": b, "matches_vacancy": sorted(terms(b) & wanted)} for s, b in scored[:8] if s > 0]


def prepare(job: Job, cv_text: str, profile: dict | None = None, company: dict | None = None) -> dict:
    ctx = candidate_context(cv_text, profile)
    f = fit(job, ctx) if ctx["has_cv"] else {}
    job_text = f"{job.title}\n{job.description}"
    wanted = terms(job_text)
    tech = [{"topic": t, "likely_questions": TOPICS[t]} for t in sorted(wanted, key=lambda t: t not in terms(job.title)) if t in TOPICS][:10]
    other = sorted(t for t in wanted if t not in TOPICS)
    lead = job.seniority in ("senior", "lead") or re.search(r"lead|head|manager|менеджер|керівник", job.title, re.I)
    ask = list(ASK)
    out = {
        "vacancy": {"job_id": job.id, "title": job.title, "company": job.company, "seniority": job.seniority},
        "key_requirements": requirements(job.description),
        "technical_topics": tech,
        "other_technologies": other[:12],
        "behavioral_questions": BEHAVIORAL + (LEAD if lead else []),
        "risk_questions": risk_questions(cv_text, job, ctx, f) if ctx["has_cv"] else [],
        "star_material": star_material(cv_text, job_text) if ctx["has_cv"] else [],
        "fit": {k: f.get(k) for k in ("score", "label", "knockouts", "covered", "missing")} if f else None,
    }
    if company and company.get("found_on_dou"):
        hiring = (company.get("reviews") or {}).get("themes", {}).get("hiring_process", {})
        out["company_brief"] = {k: company.get(k) for k in ("company", "size", "main_business", "spheres", "rating", "summary", "website")}
        if hiring.get("quotes"):
            out["company_brief"]["what_candidates_say_about_interviews"] = hiring["quotes"]
        concerns = (company.get("reviews") or {}).get("concerns", [])
        if "workload" in concerns:
            ask.append("How often does the team work outside normal hours, and how is it compensated?")
        if "management" in concerns or "processes" in concerns:
            ask.append("How are priorities set and communicated to the team?")
        if "compensation" in concerns:
            ask.append("How and how often are salaries reviewed?")
    out["questions_to_ask"] = ask
    out["instructions"] = ("Build STAR answers (Situation, Task, Action, Result) ONLY from star_material and the candidate's "
                           "remembered facts; where a result number is unknown, ask the candidate instead of inventing it. "
                           "For risk questions draft honest answers. Offer a mock interview (prompt mock_interview): one "
                           "question at a time, wait for the answer, then give short feedback on structure, specifics and length.")
    return out
