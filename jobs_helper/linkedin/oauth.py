"""LinkedIn OAuth 2.0 (3-legged) for the candidate's own account.

Self-serve products only: "Sign In with LinkedIn using OpenID Connect" (openid profile email) and
"Share on LinkedIn" (w_member_social). Client id/secret live in .secrets/linkedin.env, the token in
.secrets/linkedin_token.json (both git-ignored). Tokens live ~60 days; there is no refresh token.
"""

from __future__ import annotations

import json
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from jobs_helper import config

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
REDIRECT_URI = "http://localhost:8765/callback"
SCOPES = "openid profile email w_member_social"


def secrets_dir() -> Path:
    return Path(os.environ.get("JOBS_SECRETS_DIR", config.HOME / ".secrets"))


def token_path() -> Path:
    return secrets_dir() / "linkedin_token.json"


def load_client() -> dict:
    path = secrets_dir() / "linkedin.env"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found: put LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET there")
    pairs = (line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))
    return {k.strip(): v.strip() for k, v in pairs}


def load_token() -> dict | None:
    try:
        tok = json.loads(token_path().read_text())
    except (OSError, json.JSONDecodeError):
        return None
    tok["expired"] = time.time() > tok.get("obtained_at", 0) + tok.get("expires_in", 0) - 300
    return tok


def oauth_login() -> str:
    client = load_client()
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urlencode({"response_type": "code", "client_id": client["LINKEDIN_CLIENT_ID"],
                                      "redirect_uri": REDIRECT_URI, "state": state, "scope": SCOPES})
    got: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            got.update({k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Готово, вкладку можна закрити.".encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("localhost", 8765), Handler)
    print("Відкриваю LinkedIn для входу…")
    webbrowser.open(url)
    server.handle_request()
    server.server_close()
    if got.get("state") != state or "code" not in got:
        raise RuntimeError(f"OAuth failed: {got.get('error_description') or got}")
    r = httpx.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": got["code"], "redirect_uri": REDIRECT_URI,
                                    "client_id": client["LINKEDIN_CLIENT_ID"], "client_secret": client["LINKEDIN_CLIENT_SECRET"]},
                   timeout=30)
    r.raise_for_status()
    tok = r.json() | {"obtained_at": int(time.time())}
    secrets_dir().mkdir(parents=True, exist_ok=True)
    token_path().write_text(json.dumps(tok, indent=2))
    return f"LinkedIn connected; token valid for {tok.get('expires_in', 0) // 86400} days (scope: {tok.get('scope')})"
