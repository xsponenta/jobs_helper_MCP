"""Applications tracker: one CSV row per application attempt plus the documents that were sent."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from jobs_helper import config
from jobs_helper.models import now_iso

FIELDS = ["application_id", "created_at", "candidate", "job_id", "source", "target", "company", "title", "url",
          "status", "policy", "cv_path", "cover_path", "screenshot", "submitted_by", "answers"]
# statuses that mean "the employer has (or will get) this application"
ACTIVE = {"submitted", "awaiting_user"}


def workspace() -> Path:
    path = Path(os.environ.get("JOBS_WORKSPACE", config.HOME / "workspace"))
    path.mkdir(parents=True, exist_ok=True)
    return path


class Tracker:
    def __init__(self, root: Path | None = None):
        self.root = root or workspace()
        self.csv_path = self.root / "applications.csv"
        self.docs_dir = self.root / "applications"

    def rows(self) -> list[dict]:
        if not self.csv_path.exists():
            return []
        with self.csv_path.open(newline="") as f:
            return list(csv.DictReader(f))

    def find_active(self, candidate: str, job_id: str) -> dict | None:
        for r in self.rows():
            if r["job_id"] == job_id and r["candidate"].lower() == candidate.lower() and r["status"] in ACTIVE:
                return r
        return None

    def next_id(self) -> str:
        nums = [int(r["application_id"].split("-")[1]) for r in self.rows() if r["application_id"].startswith("A-")]
        return f"A-{(max(nums) if nums else 0) + 1:04d}"

    def save_documents(self, app_id: str, cv_markdown: str, cover_letter: str) -> tuple[str, str]:
        d = self.docs_dir / app_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "cv.md").write_text(cv_markdown.strip() + "\n")
        (d / "cover_letter.md").write_text(cover_letter.strip() + "\n")
        return str((d / "cv.md").relative_to(self.root)), str((d / "cover_letter.md").relative_to(self.root))

    def add(self, **row) -> dict:
        row = {k: row.get(k, "") for k in FIELDS} | {"created_at": now_iso()}
        if isinstance(row["answers"], dict):
            row["answers"] = json.dumps(row["answers"], ensure_ascii=False)
        new = not self.csv_path.exists()
        with self.csv_path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
        return row
