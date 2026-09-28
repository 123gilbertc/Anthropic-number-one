"""Team identity: nflverse abbreviations, franchise continuity, and name normalization.

Polymarket titles use full names ("Kansas City Chiefs"), nicknames ("Chiefs") or city
names ("Kansas City"). nflverse uses abbreviations and changes them on relocation
(STL->LA, SD->LAC, OAK->LV). ``canonical`` maps any historical code to the current
franchise so ratings carry across relocations.
"""
from __future__ import annotations

import re

CURRENT_TEAMS: tuple[str, ...] = (
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB",
    "HOU", "IND", "JAX", "KC", "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG", "NYJ",
    "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS",
)

# Historical nflverse codes -> current franchise code
FRANCHISE_MAP: dict[str, str] = {"STL": "LA", "SD": "LAC", "OAK": "LV"}

DIVISIONS: dict[str, tuple[str, str]] = {
    "BUF": ("AFC", "East"), "MIA": ("AFC", "East"), "NE": ("AFC", "East"), "NYJ": ("AFC", "East"),
    "BAL": ("AFC", "North"), "CIN": ("AFC", "North"), "CLE": ("AFC", "North"), "PIT": ("AFC", "North"),
    "HOU": ("AFC", "South"), "IND": ("AFC", "South"), "JAX": ("AFC", "South"), "TEN": ("AFC", "South"),
    "DEN": ("AFC", "West"), "KC": ("AFC", "West"), "LV": ("AFC", "West"), "LAC": ("AFC", "West"),
    "DAL": ("NFC", "East"), "NYG": ("NFC", "East"), "PHI": ("NFC", "East"), "WAS": ("NFC", "East"),
    "CHI": ("NFC", "North"), "DET": ("NFC", "North"), "GB": ("NFC", "North"), "MIN": ("NFC", "North"),
    "ATL": ("NFC", "South"), "CAR": ("NFC", "South"), "NO": ("NFC", "South"), "TB": ("NFC", "South"),
    "ARI": ("NFC", "West"), "LA": ("NFC", "West"), "SF": ("NFC", "West"), "SEA": ("NFC", "West"),
}

FULL_NAMES: dict[str, str] = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LA": "Los Angeles Rams", "LAC": "Los Angeles Chargers",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}

# Every alias we expect to meet in market titles, feeds, or other data vendors.
_ALIASES: dict[str, str] = {
    # nicknames
    "cardinals": "ARI", "falcons": "ATL", "ravens": "BAL", "bills": "BUF", "panthers": "CAR",
    "bears": "CHI", "bengals": "CIN", "browns": "CLE", "cowboys": "DAL", "broncos": "DEN",
    "lions": "DET", "packers": "GB", "texans": "HOU", "colts": "IND", "jaguars": "JAX",
    "jags": "JAX", "chiefs": "KC", "rams": "LA", "chargers": "LAC", "raiders": "LV",
    "dolphins": "MIA", "vikings": "MIN", "patriots": "NE", "pats": "NE", "saints": "NO",
    "giants": "NYG", "jets": "NYJ", "eagles": "PHI", "steelers": "PIT", "seahawks": "SEA",
    "49ers": "SF", "niners": "SF", "buccaneers": "TB", "bucs": "TB", "titans": "TEN",
    "commanders": "WAS", "football team": "WAS", "redskins": "WAS",
    # cities / regions (unambiguous ones only)
    "arizona": "ARI", "atlanta": "ATL", "baltimore": "BAL", "buffalo": "BUF", "carolina": "CAR",
    "chicago": "CHI", "cincinnati": "CIN", "cleveland": "CLE", "dallas": "DAL", "denver": "DEN",
    "detroit": "DET", "green bay": "GB", "houston": "HOU", "indianapolis": "IND",
    "jacksonville": "JAX", "kansas city": "KC", "las vegas": "LV", "miami": "MIA",
    "minnesota": "MIN", "new england": "NE", "new orleans": "NO", "philadelphia": "PHI",
    "pittsburgh": "PIT", "seattle": "SEA", "san francisco": "SF", "tampa bay": "TB", "tampa": "TB",
    "tennessee": "TEN", "washington": "WAS", "oakland": "LV", "st. louis": "LA", "st louis": "LA",
    "san diego": "LAC",
    # other vendors' codes
    "jac": "JAX", "lar": "LA", "lvr": "LV", "nor": "NO", "gnb": "GB", "kan": "KC", "nwe": "NE",
    "sfo": "SF", "tam": "TB", "wsh": "WAS", "wdc": "WAS", "hst": "HOU", "bal": "BAL", "clt": "IND",
    "crd": "ARI", "rav": "BAL", "oti": "TEN", "htx": "HOU", "sdg": "LAC", "ram": "LA", "rai": "LV",
    "stl": "LA", "sd": "LAC", "oak": "LV", "la": "LA", "lv": "LV", "kc": "KC", "gb": "GB",
    "ne": "NE", "no": "NO", "sf": "SF", "tb": "TB",
}
for _code, _full in FULL_NAMES.items():
    _ALIASES[_full.lower()] = _code
    _ALIASES[_code.lower()] = _code
_ALIASES.update({
    "los angeles rams": "LA", "la rams": "LA", "l.a. rams": "LA",
    "los angeles chargers": "LAC", "la chargers": "LAC", "l.a. chargers": "LAC",
    "new york giants": "NYG", "ny giants": "NYG", "n.y. giants": "NYG",
    "new york jets": "NYJ", "ny jets": "NYJ", "n.y. jets": "NYJ",
    "washington commanders": "WAS", "washington football team": "WAS", "washington redskins": "WAS",
    "oakland raiders": "LV", "las vegas raiders": "LV", "st. louis rams": "LA", "san diego chargers": "LAC",
    "kansas city chiefs": "KC", "kc chiefs": "KC", "tampa bay bucs": "TB", "gb packers": "GB",
})


def canonical(code: str) -> str:
    """Map any nflverse code (historical or current) to the current franchise code."""
    c = str(code).upper().strip()
    return FRANCHISE_MAP.get(c, c)


def normalize_team_name(text: str) -> str | None:
    """Resolve a free-text team reference to a current nflverse code, or None if unknown.

    Handles full names, nicknames, cities, common vendor codes, and relocations.
    Longest alias wins so "los angeles rams" beats "rams"/"los angeles".
    """
    if text is None:
        return None
    s = re.sub(r"\s+", " ", str(text).strip().lower())
    s = s.replace("’", "'")
    if not s:
        return None
    if s in _ALIASES:
        return _ALIASES[s]
    up = s.upper()
    if up in CURRENT_TEAMS:
        return up
    if up in FRANCHISE_MAP:
        return FRANCHISE_MAP[up]
    # substring search, longest alias first, bounded on word edges
    best: tuple[int, str] | None = None
    for alias, code in _ALIASES.items():
        if len(alias) < 3:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", s):
            if best is None or len(alias) > best[0]:
                best = (len(alias), code)
    return best[1] if best else None


def teams_in_text(text: str) -> list[str]:
    """All distinct team codes mentioned in a title such as 'Chiefs vs. Bills' (order of appearance)."""
    s = re.sub(r"\s+", " ", str(text).lower())
    found: list[tuple[int, int, str]] = []
    for alias, code in _ALIASES.items():
        if len(alias) < 3:
            continue
        for m in re.finditer(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", s):
            found.append((m.start(), -len(alias), code))
    found.sort()
    out: list[str] = []
    covered: list[tuple[int, int]] = []
    for start, neg_len, code in found:
        end = start - neg_len
        if any(a <= start < b for a, b in covered):
            continue
        covered.append((start, end))
        if code not in out:
            out.append(code)
    return out


def conference(code: str) -> str:
    return DIVISIONS[canonical(code)][0]


def division(code: str) -> str:
    conf, div = DIVISIONS[canonical(code)]
    return f"{conf} {div}"


def division_members(code: str) -> list[str]:
    key = DIVISIONS[canonical(code)]
    return [t for t, d in DIVISIONS.items() if d == key]
