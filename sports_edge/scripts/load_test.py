"""Local load test: event-processing time and API latency under concurrent clients.

Measures, on *this* machine only:
  1. processing time per replayed event through the real Monitor (all four sports);
  2. HTTP latency of /api/board and /api/events/{id}/workspace with N concurrent clients
     while the replay advances (server started separately on --base).

This is local processing speed. It is NOT stadium-to-screen latency: provider
delay, network and browser rendering are not included. No external API is called.

    uv run python scripts/load_test.py --base http://127.0.0.1:8811 --clients 25 --seconds 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import statistics
import time
from pathlib import Path

import httpx


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


def processing() -> dict:
    from sports_edge.replay.runner import ReplayStream
    from sports_edge.sports.models import synthetic_forecasters

    st = ReplayStream(Path(__file__).resolve().parents[1] / "fixtures" / "slate_synthetic.jsonl")
    fc = synthetic_forecasters(st.strength, nhl_games=150, nfl_games=150)
    clock, mon = st.build(forecaster=None, forecasters=fc, mechanics_demo=True)
    mon.auto_paper = False
    by_kind: dict[str, list[float]] = {}
    while not st.done:
        kind = st.body[st.position]["stream"]
        t = time.perf_counter()
        st.step(clock, mon)
        by_kind.setdefault(kind, []).append((time.perf_counter() - t) * 1000)
    return {k: {"n": len(v), "p50_ms": round(pct(v, .5), 3), "p95_ms": round(pct(v, .95), 3),
                "p99_ms": round(pct(v, .99), 3), "max_ms": round(max(v), 2)}
            for k, v in by_kind.items()}


async def http_load(base: str, clients: int, seconds: float, token: str) -> dict:
    lat: dict[str, list[float]] = {"board": [], "workspace": []}
    errors = 0
    stop = time.monotonic() + seconds
    async with httpx.AsyncClient(base_url=base, timeout=30) as op:
        await op.post("/api/auth/login", json={"token": token})

        async def stepper():
            while time.monotonic() < stop:
                await op.post("/api/session/step", json={"n": 20}, headers={"X-SE-Request": "1"})
                await asyncio.sleep(0.25)

        async def client(i: int):
            nonlocal errors
            async with httpx.AsyncClient(base_url=base, timeout=30) as c:
                games = ["SYN-NHL-1", "SYN-NFL-1", "SYN-TEN-2", "SYN-MLB-1"]
                k = 0
                while time.monotonic() < stop:
                    for name, path in (("board", "/api/board?mode=demo"),
                                       ("workspace", f"/api/events/{games[(i + k) % 4]}/workspace")):
                        t = time.perf_counter()
                        r = await c.get(path)
                        lat[name].append((time.perf_counter() - t) * 1000)
                        errors += r.status_code != 200
                    k += 1
        await asyncio.gather(stepper(), *[client(i) for i in range(clients)])
    return {"clients": clients, "seconds": seconds, "errors": errors,
            **{k: {"requests": len(v), "p50_ms": round(pct(v, .5), 1),
                   "p95_ms": round(pct(v, .95), 1), "p99_ms": round(pct(v, .99), 1),
                   "mean_ms": round(statistics.mean(v), 1) if v else None}
               for k, v in lat.items()}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8811")
    ap.add_argument("--clients", type=int, default=25)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--token", default=os.environ.get("SPORTS_EDGE_API_TOKEN", "dev-token"))
    a = ap.parse_args()
    hw = {"cpus": os.cpu_count(), "machine": platform.machine(), "python": platform.python_version(),
          "processor": platform.processor() or "unknown"}
    try:
        mem = [x for x in Path("/proc/meminfo").read_text().splitlines() if x.startswith("MemTotal")]
        hw["memory"] = mem[0].split(":")[1].strip()
    except OSError:
        pass
    out = {"hardware": hw, "processing_per_event": processing(),
           "http": asyncio.run(http_load(a.base, a.clients, a.seconds, a.token)),
           "note": "Local processing and serving only; not stadium-to-screen latency."}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
