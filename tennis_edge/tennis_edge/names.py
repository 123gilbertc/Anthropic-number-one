"""Player-name normalisation so different sources line up.

Sources disagree on formats: Sackmann uses "Jannik Sinner", tennis-data.co.uk
uses "Sinner J.", Polymarket uses whatever the market creator typed. We key
every player by "<surname> <first initial>" in lowercase ASCII.
"""
import re
import unicodedata


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def player_key(name: str) -> str:
    s = _ascii(name).strip().lower()
    s = re.sub(r"[^a-z.\- ]", "", s)
    if not s:
        return ""
    parts = s.replace("-", " ").split()
    # tennis-data.co.uk style: "Sinner J." / "Auger-Aliassime F." / "De Minaur A."
    if len(parts) >= 2 and re.fullmatch(r"([a-z]\.)+", parts[-1]):
        return f"{' '.join(parts[:-1]).replace('.', '')} {parts[-1][0]}"
    parts = [p.replace(".", "") for p in parts if p.replace(".", "")]
    if len(parts) == 1:
        return parts[0]
    # "Jannik Sinner" / "Alex de Minaur": surname = everything after first name
    return f"{' '.join(parts[1:])} {parts[0][0]}"
