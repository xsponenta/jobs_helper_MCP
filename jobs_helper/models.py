"""Normalized data model shared by all sources, the snapshot store and the MCP server."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

SourceName = Literal["dou", "djinni", "happymonday"]
WorkMode = Literal["remote", "hybrid", "onsite", "unknown"]
Seniority = Literal["intern", "junior", "middle", "senior", "lead", "unknown"]
ApplyType = Literal["on_site", "external", "login_required", "unknown"]

ALL_SOURCES: tuple[SourceName, ...] = ("dou", "djinni", "happymonday")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Job(BaseModel):
    id: str = Field(description="Stable id: '<source>:<key>', e.g. 'dou:acme/376945', 'djinni:853661-python-dev', 'hm:2049225'")
    source: SourceName
    url: str
    title: str
    company: str | None = None
    description: str = ""
    location: str | None = Field(None, description="Raw location text from the source")
    cities: list[str] = []
    work_mode: WorkMode = "unknown"
    salary_min: int | None = Field(None, description="Monthly, USD")
    salary_max: int | None = None
    seniority: Seniority = "unknown"
    experience_years: float | None = Field(None, description="Required experience, years")
    english: str | None = None
    tags: list[str] = []
    published_at: str | None = None
    valid_through: str | None = None
    apply_type: ApplyType = "unknown"
    apply_url: str | None = None
    details_loaded: bool = Field(False, description="True when the full vacancy page was parsed")
    fetched_at: str = Field(default_factory=now_iso)

    def short(self) -> dict:
        """Compact view for search results (keeps tool outputs small)."""
        salary = None
        if self.salary_min or self.salary_max:
            salary = f"{self.salary_min or ''}-{self.salary_max or ''} USD".strip("-")
        return {
            "job_id": self.id, "title": self.title, "company": self.company, "source": self.source,
            "work_mode": self.work_mode, "location": self.location, "salary": salary,
            "seniority": self.seniority, "experience_years": self.experience_years,
            "url": self.url,
        }


class SearchQuery(BaseModel):
    keywords: str = Field(description="Role and skills, e.g. 'python django' or 'react typescript'")
    sources: list[SourceName] = list(ALL_SOURCES)
    remote: bool | None = Field(None, description="True: remote only")
    city: str | None = None
    min_salary: int | None = Field(None, description="Drop jobs whose known max salary is below this (USD/month)")
    max_experience_years: float | None = Field(None, description="Drop jobs that require more experience than this")
    seniority: Seniority | None = None
    limit: int = Field(20, ge=1, le=100)
