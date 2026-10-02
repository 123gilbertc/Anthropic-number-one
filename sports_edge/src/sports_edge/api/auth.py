"""Operator authentication for commands.

Read endpoints are open on the local machine; every state-changing command
needs an authenticated session. The operator token comes from
``SPORTS_EDGE_API_TOKEN`` or is generated at first start into
``runs/api_token`` (mode 0600). Login exchanges it for an HttpOnly,
SameSite=Strict session cookie; commands additionally require the
``X-SE-Request: 1`` header (a simple CSRF guard browsers won't send
cross-site without CORS approval). Bearer tokens work for scripts.
"""

from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request

COOKIE = "se_session"


class Auth:
    def __init__(self, runs_dir: Path) -> None:
        tok = os.environ.get("SPORTS_EDGE_API_TOKEN")
        if not tok:
            p = runs_dir / "api_token"
            runs_dir.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text(secrets.token_urlsafe(24))
                os.chmod(p, 0o600)
            tok = p.read_text().strip()
        self.token = tok
        self.sessions: set[str] = set()

    def check_token(self, candidate: str) -> bool:
        return hmac.compare_digest(candidate.encode(), self.token.encode())

    def login(self, candidate: str) -> str:
        if not self.check_token(candidate):
            raise HTTPException(401, {"code": "BAD_TOKEN", "detail": "invalid operator token"})
        sid = secrets.token_urlsafe(32)
        self.sessions.add(sid)
        return sid

    def is_authenticated(self, request: Request) -> bool:
        authz = request.headers.get("authorization", "")
        if authz.lower().startswith("bearer ") and self.check_token(authz[7:].strip()):
            return True
        sid = request.cookies.get(COOKIE)
        return bool(sid and sid in self.sessions)

    def require(self, request: Request) -> None:
        authz = request.headers.get("authorization", "")
        if authz.lower().startswith("bearer "):
            if self.check_token(authz[7:].strip()):
                return
            raise HTTPException(401, {"code": "BAD_TOKEN", "detail": "invalid bearer token"})
        sid = request.cookies.get(COOKIE)
        if not (sid and sid in self.sessions):
            raise HTTPException(401, {"code": "NOT_AUTHENTICATED", "detail": "log in first"})
        if request.headers.get("x-se-request") != "1":
            raise HTTPException(403, {"code": "CSRF", "detail": "missing X-SE-Request header"})
