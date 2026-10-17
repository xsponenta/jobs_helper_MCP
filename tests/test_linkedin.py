"""LinkedIn service with a mocked LinkedIn API (no network)."""

import json
import time

import httpx
import pytest
import yaml

from jobs_helper.linkedin.service import LinkedInService


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("JOBS_SETTINGS", str(tmp_path / "settings.yaml"))
    monkeypatch.setenv("JOBS_SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.delenv("JOBS_DRY_RUN", raising=False)
    return tmp_path


def connect(env, expired=False):
    (env / "secrets").mkdir(exist_ok=True)
    (env / "secrets" / "linkedin_token.json").write_text(json.dumps(
        {"access_token": "T", "expires_in": 5184000, "obtained_at": int(time.time()) - (6_000_000 if expired else 0)}))


def policy(env, value):
    (env / "settings.yaml").write_text(yaml.safe_dump({"submit_policy": {"linkedin": value}}))


def api(calls):
    def handler(req: httpx.Request):
        calls.append(req)
        if req.url.path == "/v2/userinfo":
            return httpx.Response(200, json={"sub": "abc123", "name": "Maria Shevchenko", "email": "m@example.com"})
        if req.url.path == "/rest/posts":
            return httpx.Response(201, headers={"x-restli-id": "urn:li:share:42"})
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_not_connected_and_expired(env):
    assert LinkedInService(client=api([])).get_profile()["error"] == "not_connected"
    connect(env, expired=True)
    assert LinkedInService(client=api([])).get_profile()["error"] == "token_expired"


def test_profile(env):
    connect(env)
    assert LinkedInService(client=api([])).get_profile()["name"] == "Maria Shevchenko"


def test_auto_publish_builds_correct_request(env):
    connect(env)
    policy(env, "auto")
    calls = []
    r = LinkedInService(client=api(calls), role="reviewer").publish_post("Looking for a Frontend role. #react")
    assert r["status"] == "published" and r["post_id"] == "urn:li:share:42"
    post = calls[-1]
    body = json.loads(post.content)
    assert body["author"] == "urn:li:person:abc123" and body["lifecycleState"] == "PUBLISHED"
    assert post.headers["X-Restli-Protocol-Version"] == "2.0.0" and post.headers["LinkedIn-Version"]


def test_confirm_declined_manual_and_dry_run(env, monkeypatch):
    connect(env)
    policy(env, "confirm")
    assert LinkedInService(client=api([]), role="reviewer", confirmer=lambda t, v: False).publish_post("hi")["status"] == "declined"
    opened = []
    policy(env, "manual")
    r = LinkedInService(client=api([]), role="reviewer", opener=opened.append).publish_post("hello world")
    assert r["status"] == "awaiting_user" and "shareActive=true" in opened[0]
    monkeypatch.setenv("JOBS_DRY_RUN", "1")
    assert LinkedInService(client=api([]), role="reviewer").publish_post("draft")["status"] == "prepared"
    rows = (env / "ws" / "linkedin_posts.csv").read_text().splitlines()
    assert len(rows) == 4  # header + declined + awaiting_user + prepared


def test_validation_and_roles(env):
    s = LinkedInService(client=api([]), role="reviewer")
    assert s.publish_post("x" * 3001)["error"] == "post_too_long"
    assert s.publish_post("  ")["error"] == "invalid_argument"
    assert LinkedInService(client=api([]), role="scout").publish_post("hi")["error"] == "forbidden"


def test_profile_update_file(env):
    s = LinkedInService(role="reviewer")
    r = s.save_profile_update("Frontend Developer · React, TypeScript, Next.js", "I build product web apps.", ["React", "TypeScript"])
    assert r["status"] == "draft_saved"          # nothing opens before the candidate approves
    text = (env / "ws" / "profile_update.md").read_text()
    assert "Frontend Developer" in text and "React, TypeScript" in text
    assert s.save_profile_update("x" * 221, "a", [])["error"] == "headline_too_long"


def test_without_token_falls_back_to_share_dialog(env):
    policy(env, "auto")
    opened = []
    r = LinkedInService(client=api([]), role="reviewer", opener=opened.append).publish_post("Open to work")
    assert r["status"] == "awaiting_user" and "not connected" in r["message"] and opened
