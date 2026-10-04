"""Subscription billing (payment provider TEST mode only) and entitlement reconciliation.

Nothing here charges anyone: no live keys are read, no prices are defined, and checkout
creation stays NOT_CONFIGURED until an operator supplies *test-mode* credentials and an
approved price id. The webhook handler is what makes entitlements trustworthy:

* signature verification: Stripe-style ``Stripe-Signature: t=<unix>,v1=<hex>`` where
  v1 = HMAC-SHA256(secret, f"{t}.{raw_body}"); timestamps outside the tolerance are
  rejected (replay protection). Scheme from general knowledge: UNVERIFIED in this
  session until checked against the provider's current docs;
* idempotency: every event id is processed at most once (duplicates return 200);
* ordering: an older event never overwrites newer subscription state;
* renewal failure: ``past_due`` immediately limits entitlements to the free plan
  (configurable grace period, default none);
* reconciliation: entitlements are always *derived* from the stored subscription
  state; ``reconcile`` re-derives them (and would re-fetch from the provider when a
  test key is configured).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from sports_edge.accounts import PLANS, AccountError

TOLERANCE_S = 300
ACTIVE = ("active", "trialing")


def sign(secret: str, payload: bytes, t: int | None = None) -> str:
    """Build a signature header (used by tests and local webhook replays)."""
    t = t or int(time.time())
    mac = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(secret: str, payload: bytes, header: str | None, now: float | None = None) -> None:
    if not secret:
        raise AccountError(409, "BILLING_NOT_CONFIGURED", "no webhook signing secret set")
    if not header:
        raise AccountError(400, "NO_SIGNATURE", "missing signature header")
    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        k, _, v = item.strip().partition("=")
        parts.setdefault(k, []).append(v)
    try:
        t = int(parts["t"][0])
    except (KeyError, ValueError) as e:
        raise AccountError(400, "BAD_SIGNATURE", "no timestamp in signature") from e
    if abs((now or time.time()) - t) > TOLERANCE_S:
        raise AccountError(400, "STALE_SIGNATURE", "signature timestamp outside tolerance")
    expected = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, v) for v in parts.get("v1", [])):
        raise AccountError(400, "BAD_SIGNATURE", "signature does not match")


@dataclass
class Billing:
    accounts: object  # Accounts
    webhook_secret: str | None
    test_secret_key: str | None = None
    grace_hours: int = 0

    def status(self) -> dict:
        key_ok = bool(self.test_secret_key and self.test_secret_key.startswith("sk_test_"))
        return {"mode": "TEST" if key_ok else "NOT_CONFIGURED",
                "checkout": "available (test mode)" if key_ok else
                "NOT_CONFIGURED: needs a test-mode secret key and an approved price id",
                "webhook": "configured" if self.webhook_secret else "NOT_CONFIGURED",
                "live_charges": "DISABLED: no live keys are ever read",
                "plans": {k: {"label": v["label"], "price": v["price"],
                              "entitlements": v["entitlements"]} for k, v in PLANS.items()}}

    def handle(self, payload: bytes, signature: str | None, now: float | None = None) -> dict:
        verify(self.webhook_secret or "", payload, signature, now)
        try:
            ev = json.loads(payload)
            eid, kind, obj = ev["id"], ev["type"], ev["data"]["object"]
            created = int(ev.get("created", 0))
        except (ValueError, KeyError, TypeError) as e:
            raise AccountError(400, "BAD_EVENT", "unparseable event") from e
        store = self.accounts.store  # type: ignore[attr-defined]
        sha = hashlib.sha256(payload).hexdigest()
        if not store.record_billing_event(eid, kind, sha):
            return {"received": True, "duplicate": True}
        user_id = self._user_for(obj)
        if user_id is None:
            store.audit(None, "BILLING_EVENT_UNMATCHED", {"event": eid, "type": kind})
            return {"received": True, "matched": False}
        sub = dict(self.accounts.subscription(user_id))  # type: ignore[attr-defined]
        if created and created < int(sub.get("last_event_created", 0)):
            store.audit(user_id, "BILLING_EVENT_OUT_OF_ORDER", {"event": eid, "type": kind})
            return {"received": True, "ignored": "older than current state"}
        if kind == "checkout.session.completed":
            sub.update(customer_id=obj.get("customer"), subscription_id=obj.get("subscription"),
                       plan=(obj.get("metadata") or {}).get("plan", sub.get("plan", "free")),
                       status="active")
        elif kind in ("customer.subscription.created", "customer.subscription.updated"):
            plan = (obj.get("metadata") or {}).get("plan") or sub.get("plan", "free")
            sub.update(customer_id=obj.get("customer", sub.get("customer_id")),
                       subscription_id=obj.get("id"), status=obj.get("status", "unknown"),
                       plan=plan if plan in PLANS else "free",
                       current_period_end=obj.get("current_period_end"),
                       cancel_at_period_end=bool(obj.get("cancel_at_period_end")))
        elif kind == "customer.subscription.deleted":
            sub.update(status="canceled", plan="free")
        elif kind == "invoice.payment_failed":
            sub.update(status="past_due")
        elif kind == "invoice.paid":
            if sub.get("status") == "past_due":
                sub.update(status="active")
        else:
            store.audit(user_id, "BILLING_EVENT_IGNORED", {"event": eid, "type": kind})
            return {"received": True, "ignored": kind}
        sub["last_event_created"] = created
        sub["updated_at"] = datetime.now(UTC).isoformat()
        store.put_doc(user_id, "subscription", sub)
        store.audit(user_id, "BILLING_EVENT", {"event": eid, "type": kind,
                                                "status": sub.get("status")})
        return {"received": True, "user_id": user_id, "status": sub.get("status"),
                "plan": sub.get("plan")}

    def _user_for(self, obj: dict) -> str | None:
        ref = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("user_id")
        store = self.accounts.store  # type: ignore[attr-defined]
        if ref and store.user(ref):
            return ref
        cust = obj.get("customer")
        return store.user_for_customer(cust) if cust else None

    def checkout(self, user_id: str, plan: str) -> dict:
        if plan not in PLANS or PLANS[plan]["price"] is None:
            raise AccountError(409, "PRICE_NOT_APPROVED",
                               "no approved price exists for this plan; checkout disabled")
        if not (self.test_secret_key and self.test_secret_key.startswith("sk_test_")):
            raise AccountError(409, "BILLING_NOT_CONFIGURED", "test-mode billing not configured")
        raise AccountError(501, "NOT_IMPLEMENTED", "checkout session creation awaits an "
                                                   "approved price id")
