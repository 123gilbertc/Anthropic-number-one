"""Customer account, preferences, watchlist, alerts, export/deletion and billing routes.

Every route resolves the caller (customer or operator) on the server and scopes every
read and write to that caller. Entitlements are enforced here, never in the browser.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sports_edge.accounts import PLANS, PRICE_NOTE, AccountError
from sports_edge.api.auth import USER_COOKIE
from sports_edge.product.alerting import validate_rule


class Credentials(BaseModel):
    email: str
    password: str


class DeleteRequest(BaseModel):
    password: str


class PrefsPatch(BaseModel):
    patch: dict


class Watchlist(BaseModel):
    items: list[dict]


class Rule(BaseModel):
    rule: dict


class CheckoutRequest(BaseModel):
    plan: str


def _err(e: AccountError) -> JSONResponse:
    return JSONResponse({"code": e.code, "detail": e.detail}, status_code=e.status)


def register(app: FastAPI, srv) -> None:
    acc = srv.accounts

    def actor(request: Request) -> tuple[str, str]:
        return srv.auth.require_actor(request)

    def client(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    # ---------------------------------------------------------------- identity

    @app.post("/api/account/signup")
    def signup(req: Credentials, request: Request, response: Response):
        try:
            u = acc.signup(req.email, req.password)
            _, sid = acc.login(req.email, req.password, client(request))
        except AccountError as e:
            return _err(e)
        response.set_cookie(USER_COOKIE, sid, httponly=True, samesite="strict", secure=False)
        srv.alerts.subscribers.add(u.user_id)
        return {"user": u.public()}

    @app.post("/api/account/login")
    def login(req: Credentials, request: Request, response: Response):
        try:
            u, sid = acc.login(req.email, req.password, client(request))
        except AccountError as e:
            return _err(e)
        response.set_cookie(USER_COOKIE, sid, httponly=True, samesite="strict", secure=False)
        srv.alerts.subscribers.add(u.user_id)
        return {"user": u.public()}

    @app.post("/api/account/logout")
    def logout(request: Request, response: Response):
        acc.sessions.end(request.cookies.get(USER_COOKIE))
        response.delete_cookie(USER_COOKIE)
        return {"signed_in": False}

    @app.get("/api/account/me")
    def me(request: Request):
        a = srv.auth.actor(request)
        if a is None:
            return {"signed_in": False, "plans": _plans()}
        uid, role = a
        user = acc.store.user(uid) if role == "customer" else None
        return {"signed_in": True, "role": role,
                "user": user.public() if user else {"user_id": "operator", "role": "operator"},
                "subscription": acc.subscription(uid) if role == "customer" else None,
                "entitlements": acc.entitlements(uid, role),
                "preferences": acc.preferences(uid),
                "watchlist": acc.watchlist(uid),
                "accounts_persisted": acc.store.persisted, "plans": _plans()}

    def _plans() -> dict:
        return {"plans": PLANS, "note": PRICE_NOTE}

    @app.delete("/api/account")
    def delete_account(req: DeleteRequest, request: Request, response: Response,
                       who: tuple[str, str] = Depends(actor)):
        if who[1] != "customer":
            raise HTTPException(409, {"code": "OPERATOR", "detail": "operators are not "
                                                                    "deleted here"})
        try:
            acc.delete(who[0], req.password)
        except AccountError as e:
            return _err(e)
        response.delete_cookie(USER_COOKIE)
        srv.alerts.subscribers.discard(who[0])
        srv.alerts.inbox.pop(who[0], None)
        return {"deleted": True}

    @app.get("/api/account/export")
    def export(who: tuple[str, str] = Depends(actor)):
        """Every user may export their own data (not a paid feature)."""
        s = srv.session
        led = [e.model_dump(mode="json") for e in s.ledger if e.user_id == who[0]]
        return acc.export(who[0], led, srv.alerts.inbox.get(who[0], []))

    # ---------------------------------------------------------------- preferences

    @app.put("/api/account/preferences")
    def prefs(req: PrefsPatch, who: tuple[str, str] = Depends(actor)):
        try:
            return acc.set_preferences(who[0], req.patch)
        except AccountError as e:
            return _err(e)

    @app.put("/api/account/watchlist")
    def watchlist(req: Watchlist, who: tuple[str, str] = Depends(actor)):
        try:
            return {"items": acc.set_watchlist(who[0], req.items)}
        except AccountError as e:
            return _err(e)

    # ---------------------------------------------------------------- alerts

    @app.get("/api/alerts")
    def alerts(who: tuple[str, str] = Depends(actor)):
        srv.alerts.subscribers.add(who[0])
        doc = acc.store.get_doc(who[0], "alert_rules") or {"rules": []}
        return {"rules": doc["rules"],
                "inbox": srv.alerts.for_user(who[0], srv.session.clock.now()),
                "entitled": "alerts" in acc.entitlements(who[0], who[1]),
                "channels": {"inbox": "available", "email": "NOT_CONFIGURED",
                             "push": "NOT_CONFIGURED"}}

    @app.post("/api/alerts/rules")
    def add_rule(req: Rule, who: tuple[str, str] = Depends(actor)):
        try:
            acc.require(who[0], who[1], "alerts")
            rule = validate_rule(req.rule)
        except AccountError as e:
            return _err(e)
        except ValueError as e:
            return JSONResponse({"code": "BAD_RULE", "detail": str(e)}, status_code=422)
        doc = acc.store.get_doc(who[0], "alert_rules") or {"rules": []}
        if len(doc["rules"]) >= 50:
            return JSONResponse({"code": "TOO_MANY_RULES", "detail": "limit 50"},
                                status_code=422)
        doc["rules"].append(rule)
        acc.store.put_doc(who[0], "alert_rules", doc)
        acc.store.audit(who[0], "ALERT_RULE_ADDED", {"kind": rule["kind"]})
        srv.alerts.subscribers.add(who[0])
        return rule

    @app.delete("/api/alerts/rules/{rule_id}")
    def delete_rule(rule_id: str, who: tuple[str, str] = Depends(actor)):
        doc = acc.store.get_doc(who[0], "alert_rules") or {"rules": []}
        doc["rules"] = [r for r in doc["rules"] if r["rule_id"] != rule_id]
        acc.store.put_doc(who[0], "alert_rules", doc)
        return {"rules": doc["rules"]}

    # ---------------------------------------------------------------- billing (TEST mode)

    @app.get("/api/billing")
    def billing_status(request: Request):
        return srv.billing.status() | {"note": PRICE_NOTE}

    @app.post("/api/billing/checkout")
    def checkout(req: CheckoutRequest, who: tuple[str, str] = Depends(actor)):
        try:
            return srv.billing.checkout(who[0], req.plan)
        except AccountError as e:
            return _err(e)

    @app.post("/api/billing/webhook")
    async def webhook(request: Request):
        body = await request.body()
        try:
            return srv.billing.handle(body, request.headers.get("stripe-signature"))
        except AccountError as e:
            return _err(e)

    # ---------------------------------------------------------------- exports (Pro)

    @app.get("/api/export/paper-ledger.csv")
    def export_csv(who: tuple[str, str] = Depends(actor)):
        try:
            acc.require(who[0], who[1], "exports")
        except AccountError as e:
            return _err(e)
        rows = ["time,kind,order_id,contract_id,mode,data_label,detail"]
        for e in srv.session.ledger:
            if e.user_id != who[0]:
                continue
            detail = str(e.detail).replace('"', "'")
            rows.append(f'{e.time.isoformat()},{e.kind},{e.order_id or ""},{e.contract_id},'
                        f'{e.mode},{e.data_label},"{detail}"')
        return Response("\n".join(rows) + "\n", media_type="text/csv",
                        headers={"content-disposition": "attachment; filename=paper-ledger.csv",
                                 "x-generated-at": datetime.now(UTC).isoformat()})
