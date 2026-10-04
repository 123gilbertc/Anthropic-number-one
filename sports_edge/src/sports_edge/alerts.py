"""Optional Discord alert delivery. Disabled unless explicitly enabled AND configured.

Alerts include as-of time, expiry, state ID, reasons and the maximum eligible
paper addition, and are labelled with the data mode so a replay/demo alert can
never be mistaken for a live one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx

from sports_edge.domain.records import Decision

Post = Callable[[str, dict], Awaitable[int]]


async def _post(url: str, payload: dict) -> int:
    async with httpx.AsyncClient(timeout=5) as c:
        r = await c.post(url, json=payload)
        return r.status_code


def format_alert(d: Decision, mode: str, data_label: str) -> str:
    ev = f"cautious EV ${d.ev.ev_conservative}" if d.ev else "no EV estimate"
    return (f"[{mode} · {data_label} · PAPER ONLY] {d.action.value} {d.selection_team} "
            f"({d.contract_id}) | {ev} | max add ${d.max_eligible_addition} | state "
            f"{d.snapshot_id} | as of {d.decision_time.isoformat()} | expires "
            f"{d.expires_at.isoformat()} | {', '.join(r.value for r in d.reasons)}")


async def send_discord(d: Decision, mode: str, data_label: str, *, enabled: bool,
                       webhook_url: str | None, post: Post = _post) -> str:
    if not enabled:
        return "DISABLED"
    if not webhook_url:
        return "NOT_CONFIGURED"
    try:
        code = await post(webhook_url, {"content": format_alert(d, mode, data_label)[:1900]})
    except Exception as e:
        return f"FAILED: {type(e).__name__}"
    return "SENT" if 200 <= code < 300 else f"FAILED: HTTP {code}"
