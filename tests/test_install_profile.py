"""Profile from the Claude Desktop extension form (env), CV reading and the get_candidate tool."""

import json

from docx import Document

from jobs_helper import policy as pol


def test_env_overlays_yaml_and_ignores_placeholders(tmp_path, monkeypatch):
    cand = tmp_path / "candidate.yaml"
    cand.write_text("personal: {first_name: Old, city: Kyiv}\n")
    monkeypatch.setenv("JOBS_CANDIDATE", str(cand))
    monkeypatch.setenv("JOBS_CANDIDATE_FIRST_NAME", "Olena")
    monkeypatch.setenv("JOBS_CANDIDATE_PHONE", "${user_config.phone}")   # unset field of the extension form
    monkeypatch.setenv("JOBS_CANDIDATE_SALARY_USD", "3500.0")
    monkeypatch.setenv("JOBS_CANDIDATE_CONSENT", "false")
    monkeypatch.setenv("JOBS_POLICY_DOU", "confirm")
    monkeypatch.setenv("JOBS_POLICY_ATS", "")                            # empty -> default
    prof = pol.load_candidate()
    assert prof["personal"] == {"first_name": "Olena", "city": "Kyiv"}
    assert prof["job_preferences"]["desired_salary_usd"] == 3500
    assert prof["answers"]["consent_to_processing"] is False
    assert pol.load_policies()["dou"] == "confirm" and pol.load_policies()["ats"] == "confirm"


def test_cv_formats(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_CANDIDATE", str(tmp_path / "none.yaml"))
    doc = Document()
    doc.add_paragraph("Olena Kovalenko — Python Backend Developer")
    doc.save(tmp_path / "cv.docx")
    monkeypatch.setenv("JOBS_CANDIDATE_CV", str(tmp_path / "cv.docx"))
    path = pol.cv_file()
    assert path.name == "cv.docx" and "Python Backend" in pol.read_cv_text(path)


def test_get_candidate_tool(tmp_path, monkeypatch):
    (tmp_path / "cv.md").write_text("# Olena\nPython, Django")
    monkeypatch.setenv("JOBS_CANDIDATE", str(tmp_path / "none.yaml"))
    monkeypatch.setenv("JOBS_CANDIDATE_FIRST_NAME", "Olena")
    monkeypatch.setenv("JOBS_CANDIDATE_CV", str(tmp_path / "cv.md"))
    from jobs_helper.mcp_all import get_candidate
    out = get_candidate()
    assert out["profile"]["personal"]["first_name"] == "Olena" and "Django" in out["cv_text"]
    assert "sensitive" not in json.dumps(out["profile"])
