"""Load historical matches from Jeff Sackmann CSVs or tennis-data.co.uk files.

Both are free:
  * Sackmann format: ATP via https://github.com/Tennismylife/TML-Database
  * tennis-data.co.uk: http://www.tennis-data.co.uk/alldata.php  (includes
    closing bookmaker odds - needed for an honest backtest)

Every loader yields `Match` records sorted by date.
"""
from __future__ import annotations

import csv
import glob
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable, Optional

from .names import player_key

SURFACES = ("hard", "clay", "grass")


@dataclass
class Match:
    date: date
    winner: str
    loser: str
    surface: str  # hard / clay / grass
    best_of: int = 3
    tournament: str = ""
    round: str = ""
    # decimal odds for winner / loser if the source has them (Pinnacle preferred)
    odds_w: Optional[float] = None
    odds_l: Optional[float] = None
    completed: bool = True  # False for retirements / walkovers
    extra: dict = field(default_factory=dict)

    @property
    def wkey(self) -> str:
        return player_key(self.winner)

    @property
    def lkey(self) -> str:
        return player_key(self.loser)


def _surface(s: str) -> str:
    s = (s or "").strip().lower()
    if s.startswith("clay"):
        return "clay"
    if s.startswith("grass"):
        return "grass"
    return "hard"  # hard, carpet, indoor hard


def _float(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f > 1.0 else None
    except (TypeError, ValueError):
        return None


def _parse_date(s: str) -> date:
    s = s.strip()
    for fmt in ("%Y%m%d", "%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unrecognised date: {s!r}")


def _from_sackmann(row: dict) -> Match:
    score = row.get("score", "") or ""
    return Match(
        date=_parse_date(row["tourney_date"]),
        winner=row["winner_name"],
        loser=row["loser_name"],
        surface=_surface(row.get("surface", "")),
        best_of=int(row.get("best_of") or 3),
        tournament=row.get("tourney_name", ""),
        round=row.get("round", ""),
        completed=not any(t in score for t in ("RET", "W/O", "DEF", "ABN")),
    )


def _from_tennis_data(row: dict) -> Match:
    # prefer Pinnacle closing odds (sharpest), then market average, then B365
    ow = ol = None
    for w, l in (("PSW", "PSL"), ("AvgW", "AvgL"), ("B365W", "B365L")):
        ow, ol = _float(row.get(w)), _float(row.get(l))
        if ow and ol:
            break
    return Match(
        date=_parse_date(row["Date"]),
        winner=row["Winner"],
        loser=row["Loser"],
        surface=_surface(row.get("Surface", "")),
        best_of=int(float(row.get("Best of") or 3)),
        tournament=row.get("Tournament", ""),
        round=row.get("Round", ""),
        odds_w=ow if ol else None,
        odds_l=ol if ow else None,
        completed=(row.get("Comment", "Completed") or "Completed").strip() == "Completed",
    )


def _rows(path: str):
    """Yield (columns, row-dict iterator) for .csv or .xlsx/.xls files."""
    if path.lower().endswith((".xlsx", ".xls")):
        try:
            import openpyxl
        except ImportError as e:
            raise SystemExit("pip install openpyxl to read tennis-data.co.uk .xlsx files") from e
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True).active
        it = ws.iter_rows(values_only=True)
        header = [str(h) if h is not None else "" for h in next(it)]

        def gen():
            for vals in it:
                row = {}
                for h, v in zip(header, vals):
                    if hasattr(v, "strftime"):
                        v = v.strftime("%Y-%m-%d")
                    row[h] = "" if v is None else str(v)
                yield row
        return set(header), gen()
    fh = open(path, newline="", encoding="utf-8-sig", errors="replace")
    reader = csv.DictReader(fh)
    return set(reader.fieldnames or []), reader


def read_csv(path: str) -> list[Match]:
    """Read one Sackmann or tennis-data.co.uk file (.csv or .xlsx)."""
    cols, reader = _rows(path)
    if {"winner_name", "loser_name", "tourney_date"} <= cols:
        parse = _from_sackmann
    elif {"Winner", "Loser", "Date"} <= cols:
        parse = _from_tennis_data
    else:
        raise ValueError(f"{path}: unknown layout, columns={sorted(cols)[:12]}")
    out = []
    for row in reader:
        try:
            if row.get("winner_name") or row.get("Winner"):
                out.append(parse(row))
        except (ValueError, KeyError):
            continue  # skip malformed rows
    return out


def load_matches(paths: Iterable[str]) -> list[Match]:
    """Load every CSV matching the given paths/globs, sorted by date."""
    files: list[str] = []
    for p in paths:
        if os.path.isdir(p):
            for ext in ("*.csv", "*.xlsx", "*.xls"):
                files += glob.glob(os.path.join(p, ext))
        else:
            files += glob.glob(p)
    matches: list[Match] = []
    for f in sorted(set(files)):
        matches += read_csv(f)
    # stable sort keeps within-tournament round order from the source file
    matches.sort(key=lambda m: m.date)
    return matches


TML_URL = "https://raw.githubusercontent.com/Tennismylife/TML-Database/master/{name}.csv"


def download_tml(years: Iterable[int], out_dir: str) -> list[str]:
    """Download ATP results from TML-Database (a Sackmann-format mirror; the original
    JeffSackmann/tennis_atp repo is no longer public)."""
    import requests

    os.makedirs(out_dir, exist_ok=True)
    saved = []
    for name in [str(y) for y in years] + ["ongoing_tourneys"]:
        r = requests.get(TML_URL.format(name=name), timeout=60)
        if r.status_code != 200:
            print(f"  skip {name} ({r.status_code})")
            continue
        path = os.path.join(out_dir, f"atp_{name}.csv")
        with open(path, "wb") as fh:
            fh.write(r.content)
        saved.append(path)
        print(f"  saved {path}")
    return saved
