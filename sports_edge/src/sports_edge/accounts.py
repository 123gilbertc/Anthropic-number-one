"""Customer accounts: users, sessions, preferences, watchlists, entitlements, audit.

Security choices:
* passwords hashed with scrypt (n=2**14, r=8, p=1, 16-byte salt), compared in
  constant time; never logged or returned;
* sessions are random 256-bit tokens in an HttpOnly, SameSite=Strict cookie, held
  server-side with an expiry; every state change also needs the ``X-SE-Request``
  header (CSRF guard);
* login attempts are rate-limited per email and per client address;
* every account action is written to an audit log; deletion purges the user's
  documents and keeps only a tombstone with the audit trail.

Customer data is isolated by ``user_id`` in every query here, in the paper ledger and
in alert inboxes. Operators (the existing token login) are a separate role and are
the only ones who may configure provider credentials or promote models.

Plans and entitlements are features, not prices: no price is defined anywhere in
code. Lower tiers get exactly the same safety checks and the same forecasts.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}$")
MIN_PASSWORD = 10

PLANS: dict[str, dict] = {
    "free": {"label": "Free", "price": None,
             "entitlements": ["live_board", "game_workspace", "paper_portfolio",
                              "history_recent", "what_if", "watchlist"]},
    "pro": {"label": "Pro", "price": None,
            "entitlements": ["live_board", "game_workspace", "paper_portfolio",
                             "history_recent", "what_if", "watchlist", "alerts",
                             "history_full", "exports"]},
}
PRICE_NOTE = ("Pricing is not approved. Plans differ only in convenience features; every "
              "plan gets the same forecasts and the same safety checks.")

DEFAULT_PREFS: dict[str, Any] = {
    "sports": ["NHL", "NFL", "TENNIS", "MLB"],
    "favorites": [],  # participant ids
    "timezone": "UTC",
    "odds_format": "probability",  # probability / american / decimal / cents
    "notifications": {"inbox": True, "email": False, "push": False},
    "quiet_hours": None,  # {"start": "23:00", "end": "07:00"} in the user's timezone
    "alerts_paused": False,
}


class AccountError(Exception):
    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.status, self.code, self.detail = status, code, detail


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r),
                             p=int(p), dklen=len(bytes.fromhex(dk)))
        return hmac.compare_digest(got, bytes.fromhex(dk))
    except (ValueError, TypeError):
        return False


@dataclass
class User:
    user_id: str
    email: str
    password_hash: str
    role: str  # customer / operator
    created_at: datetime
    deleted_at: datetime | None = None

    def public(self) -> dict:
        return {"user_id": self.user_id, "email": self.email, "role": self.role,
                "created_at": self.created_at.isoformat()}


class AccountStore(Protocol):
    persisted: bool

    def create_user(self, u: User) -> None: ...
    def user_by_email(self, email: str) -> User | None: ...
    def user(self, user_id: str) -> User | None: ...
    def mark_deleted(self, user_id: str, when: datetime) -> None: ...
    def put_doc(self, user_id: str, kind: str, doc: dict) -> None: ...
    def get_doc(self, user_id: str, kind: str) -> dict | None: ...
    def docs(self, user_id: str) -> dict[str, dict]: ...
    def purge_docs(self, user_id: str) -> None: ...
    def billing_event_seen(self, event_id: str) -> bool: ...
    def record_billing_event(self, event_id: str, kind: str, payload_sha: str) -> bool: ...
    def audit(self, user_id: str | None, action: str, detail: dict) -> None: ...
    def audit_for(self, user_id: str) -> list[dict]: ...
    def user_for_customer(self, customer_id: str) -> str | None: ...


@dataclass
class MemoryAccountStore:
    persisted: bool = False
    users: dict[str, User] = field(default_factory=dict)
    docs_: dict[tuple[str, str], dict] = field(default_factory=dict)
    billing: dict[str, tuple[str, str]] = field(default_factory=dict)
    audits: list[dict] = field(default_factory=list)
    lock: Any = field(default_factory=threading.RLock)

    def create_user(self, u: User) -> None:
        with self.lock:
            if self.user_by_email(u.email):
                raise AccountError(409, "EMAIL_TAKEN", "an account with this email exists")
            self.users[u.user_id] = u

    def user_by_email(self, email: str) -> User | None:
        e = email.strip().lower()
        return next((u for u in self.users.values()
                     if u.email == e and u.deleted_at is None), None)

    def user(self, user_id: str) -> User | None:
        u = self.users.get(user_id)
        return u if u and u.deleted_at is None else None

    def mark_deleted(self, user_id: str, when: datetime) -> None:
        u = self.users[user_id]
        u.deleted_at = when
        u.email = f"deleted:{user_id}"
        u.password_hash = ""

    def put_doc(self, user_id: str, kind: str, doc: dict) -> None:
        with self.lock:
            self.docs_[(user_id, kind)] = doc

    def get_doc(self, user_id: str, kind: str) -> dict | None:
        return self.docs_.get((user_id, kind))

    def docs(self, user_id: str) -> dict[str, dict]:
        return {k: v for (u, k), v in self.docs_.items() if u == user_id}

    def purge_docs(self, user_id: str) -> None:
        with self.lock:
            for k in [k for k in self.docs_ if k[0] == user_id]:
                del self.docs_[k]

    def billing_event_seen(self, event_id: str) -> bool:
        return event_id in self.billing

    def record_billing_event(self, event_id: str, kind: str, payload_sha: str) -> bool:
        with self.lock:
            if event_id in self.billing:
                return False
            self.billing[event_id] = (kind, payload_sha)
            return True

    def audit(self, user_id: str | None, action: str, detail: dict) -> None:
        self.audits.append({"t": datetime.now(UTC).isoformat(), "user_id": user_id,
                            "action": action, "detail": detail})

    def audit_for(self, user_id: str) -> list[dict]:
        return [a for a in self.audits if a["user_id"] == user_id]

    def user_for_customer(self, customer_id: str) -> str | None:
        for (u, k), d in self.docs_.items():
            if k == "subscription" and d.get("customer_id") == customer_id:
                return u
        return None


@dataclass
class Sessions:
    ttl: timedelta = timedelta(days=7)
    items: dict[str, tuple[str, datetime]] = field(default_factory=dict)

    def create(self, user_id: str) -> str:
        sid = secrets.token_urlsafe(32)
        self.items[sid] = (user_id, datetime.now(UTC) + self.ttl)
        return sid

    def user_id(self, sid: str | None) -> str | None:
        if not sid or sid not in self.items:
            return None
        uid, exp = self.items[sid]
        if exp < datetime.now(UTC):
            del self.items[sid]
            return None
        return uid

    def end(self, sid: str | None) -> None:
        self.items.pop(sid or "", None)

    def end_all(self, user_id: str) -> None:
        for k in [k for k, (u, _) in self.items.items() if u == user_id]:
            del self.items[k]


@dataclass
class RateLimiter:
    limit: int = 8  # failed attempts per window, per email and per client address
    window_s: float = 600
    hits: dict[str, list[float]] = field(default_factory=dict)

    def check(self, key: str) -> None:
        """Refuse when too many *failed* attempts happened recently."""
        now = time.monotonic()
        h = [t for t in self.hits.get(key, []) if now - t < self.window_s]
        self.hits[key] = h
        if len(h) >= self.limit:
            raise AccountError(429, "RATE_LIMITED", "too many attempts; try again later")

    def failed(self, key: str) -> None:
        self.hits.setdefault(key, []).append(time.monotonic())


@dataclass
class Accounts:
    store: Any
    sessions: Sessions = field(default_factory=Sessions)
    limiter: RateLimiter = field(default_factory=RateLimiter)

    # ---------------------------------------------------------------- identity

    def signup(self, email: str, password: str) -> User:
        e = email.strip().lower()
        if not EMAIL.match(e):
            raise AccountError(422, "BAD_EMAIL", "enter a valid email address")
        if len(password) < MIN_PASSWORD:
            raise AccountError(422, "WEAK_PASSWORD",
                               f"use at least {MIN_PASSWORD} characters")
        u = User(f"u_{uuid.uuid4().hex[:16]}", e, hash_password(password), "customer",
                 datetime.now(UTC))
        self.store.create_user(u)
        self.store.put_doc(u.user_id, "preferences", dict(DEFAULT_PREFS))
        self.store.put_doc(u.user_id, "subscription", {"plan": "free", "status": "none"})
        self.store.audit(u.user_id, "SIGNUP", {})
        return u

    def login(self, email: str, password: str, client: str) -> tuple[User, str]:
        e = email.strip().lower()
        self.limiter.check(f"email:{e}")
        self.limiter.check(f"ip:{client}")
        u = self.store.user_by_email(e)
        ok = verify_password(password, u.password_hash) if u else \
            verify_password(password, hash_password("x" * 12))  # equalize timing
        if not (u and ok):
            self.limiter.failed(f"email:{e}")
            self.limiter.failed(f"ip:{client}")
            self.store.audit(u.user_id if u else None, "LOGIN_FAILED", {"client": client})
            raise AccountError(401, "BAD_CREDENTIALS", "email or password is incorrect")
        self.store.audit(u.user_id, "LOGIN", {"client": client})
        return u, self.sessions.create(u.user_id)

    def delete(self, user_id: str, password: str) -> None:
        u = self.store.user(user_id)
        if u is None or not verify_password(password, u.password_hash):
            raise AccountError(401, "BAD_CREDENTIALS", "password confirmation failed")
        self.store.audit(user_id, "DELETE_REQUESTED", {})
        self.store.purge_docs(user_id)
        self.store.mark_deleted(user_id, datetime.now(UTC))
        self.sessions.end_all(user_id)
        self.store.audit(user_id, "DELETED", {"note": "documents purged; tombstone kept"})

    # ---------------------------------------------------------------- documents

    def preferences(self, user_id: str) -> dict:
        return {**DEFAULT_PREFS, **(self.store.get_doc(user_id, "preferences") or {})}

    def set_preferences(self, user_id: str, patch: dict) -> dict:
        allowed = set(DEFAULT_PREFS)
        bad = set(patch) - allowed
        if bad:
            raise AccountError(422, "UNKNOWN_PREFERENCE", f"unknown fields {sorted(bad)}")
        prefs = self.preferences(user_id) | patch
        if prefs["odds_format"] not in ("probability", "american", "decimal", "cents"):
            raise AccountError(422, "BAD_ODDS_FORMAT", "probability/american/decimal/cents")
        if not set(prefs["sports"]) <= {"NHL", "NFL", "TENNIS", "MLB"}:
            raise AccountError(422, "BAD_SPORT", "unknown sport")
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(prefs["timezone"])
        except Exception as e:
            raise AccountError(422, "BAD_TIMEZONE", "unknown timezone") from e
        self.store.put_doc(user_id, "preferences", prefs)
        self.store.audit(user_id, "PREFERENCES_UPDATED", {"fields": sorted(patch)})
        return prefs

    def watchlist(self, user_id: str) -> list[dict]:
        return (self.store.get_doc(user_id, "watchlist") or {}).get("items", [])

    def set_watchlist(self, user_id: str, items: list[dict]) -> list[dict]:
        clean = []
        for it in items[:200]:
            if it.get("kind") not in ("game", "participant") or not isinstance(it.get("id"), str):
                raise AccountError(422, "BAD_WATCH_ITEM", "kind game|participant and id required")
            clean.append({"kind": it["kind"], "id": it["id"][:120]})
        self.store.put_doc(user_id, "watchlist", {"items": clean})
        return clean

    # ---------------------------------------------------------------- entitlements

    def subscription(self, user_id: str) -> dict:
        return self.store.get_doc(user_id, "subscription") or {"plan": "free", "status": "none"}

    def entitlements(self, user_id: str, role: str) -> list[str]:
        if role == "operator":
            return sorted(set(PLANS["pro"]["entitlements"]) | {"operator"})
        sub = self.subscription(user_id)
        plan = sub.get("plan", "free") if sub.get("status") in ("active", "trialing") \
            else "free"
        return list(PLANS[plan]["entitlements"])

    def require(self, user_id: str, role: str, entitlement: str) -> None:
        if entitlement not in self.entitlements(user_id, role):
            raise AccountError(403, "NOT_ENTITLED",
                               f"your plan does not include '{entitlement}'")

    def export(self, user_id: str, ledger: list[dict], alerts: list[dict]) -> dict:
        u = self.store.user(user_id)
        return {"exported_at": datetime.now(UTC).isoformat(),
                "user": u.public() if u else None, "documents": self.store.docs(user_id),
                "paper_ledger": ledger, "alerts": alerts,
                "audit": self.store.audit_for(user_id)}


class SqlAccountStore:
    """Postgres-backed store (tables from migration b6c4f7a508fc)."""

    persisted = True

    def __init__(self, engine) -> None:
        self.engine = engine

    def _t(self):
        from sports_edge.storage import tables
        return tables

    def create_user(self, u: User) -> None:
        from sqlalchemy.exc import IntegrityError
        t = self._t()
        try:
            with self.engine.begin() as c:
                c.execute(t.users.insert().values(
                    user_id=u.user_id, email=u.email, password_hash=u.password_hash,
                    role=u.role, created_at=u.created_at))
        except IntegrityError as e:
            raise AccountError(409, "EMAIL_TAKEN", "an account with this email exists") from e

    def _user(self, where) -> User | None:
        from sqlalchemy import select
        t = self._t()
        with self.engine.connect() as c:
            r = c.execute(select(t.users).where(where)).mappings().first()
        if r is None or r["deleted_at"] is not None:
            return None
        return User(r["user_id"], r["email"], r["password_hash"], r["role"], r["created_at"],
                    r["deleted_at"])

    def user_by_email(self, email: str) -> User | None:
        return self._user(self._t().users.c.email == email.strip().lower())

    def user(self, user_id: str) -> User | None:
        return self._user(self._t().users.c.user_id == user_id)

    def mark_deleted(self, user_id: str, when: datetime) -> None:
        t = self._t()
        with self.engine.begin() as c:
            c.execute(t.users.update().where(t.users.c.user_id == user_id).values(
                deleted_at=when, email=f"deleted:{user_id}", password_hash=""))

    def put_doc(self, user_id: str, kind: str, doc: dict) -> None:
        from sqlalchemy.dialects.postgresql import insert
        t = self._t()
        stmt = insert(t.user_docs).values(user_id=user_id, kind=kind, doc=doc,
                                          updated_at=datetime.now(UTC))
        stmt = stmt.on_conflict_do_update(index_elements=["user_id", "kind"],
                                          set_={"doc": doc, "updated_at": datetime.now(UTC)})
        with self.engine.begin() as c:
            c.execute(stmt)

    def get_doc(self, user_id: str, kind: str) -> dict | None:
        from sqlalchemy import select
        t = self._t()
        with self.engine.connect() as c:
            return c.execute(select(t.user_docs.c.doc).where(
                t.user_docs.c.user_id == user_id, t.user_docs.c.kind == kind)).scalar()

    def docs(self, user_id: str) -> dict[str, dict]:
        from sqlalchemy import select
        t = self._t()
        with self.engine.connect() as c:
            rows = c.execute(select(t.user_docs.c.kind, t.user_docs.c.doc).where(
                t.user_docs.c.user_id == user_id)).all()
        return {k: d for k, d in rows}

    def purge_docs(self, user_id: str) -> None:
        t = self._t()
        with self.engine.begin() as c:
            c.execute(t.user_docs.delete().where(t.user_docs.c.user_id == user_id))

    def billing_event_seen(self, event_id: str) -> bool:
        from sqlalchemy import select
        t = self._t()
        with self.engine.connect() as c:
            return c.execute(select(t.billing_events.c.event_id).where(
                t.billing_events.c.event_id == event_id)).first() is not None

    def record_billing_event(self, event_id: str, kind: str, payload_sha: str) -> bool:
        from sqlalchemy.dialects.postgresql import insert
        t = self._t()
        stmt = insert(t.billing_events).values(event_id=event_id, kind=kind,
                                               payload_sha256=payload_sha, processed=True)
        with self.engine.begin() as c:
            res = c.execute(stmt.on_conflict_do_nothing(index_elements=["event_id"]))
        return res.rowcount == 1  # atomic: concurrent duplicates cannot both win

    def audit(self, user_id: str | None, action: str, detail: dict) -> None:
        t = self._t()
        with self.engine.begin() as c:
            c.execute(t.audit_log.insert().values(user_id=user_id, action=action,
                                                  detail=detail))

    def audit_for(self, user_id: str) -> list[dict]:
        from sqlalchemy import select
        t = self._t()
        with self.engine.connect() as c:
            rows = c.execute(select(t.audit_log).where(t.audit_log.c.user_id == user_id)
                             .order_by(t.audit_log.c.pk)).mappings().all()
        return [{"t": r["t"].isoformat(), "user_id": r["user_id"], "action": r["action"],
                 "detail": r["detail"]} for r in rows]

    def user_for_customer(self, customer_id: str) -> str | None:
        from sqlalchemy import select
        t = self._t()
        with self.engine.connect() as c:
            rows = c.execute(select(t.user_docs.c.user_id, t.user_docs.c.doc).where(
                t.user_docs.c.kind == "subscription")).all()
        return next((u for u, d in rows if (d or {}).get("customer_id") == customer_id), None)
