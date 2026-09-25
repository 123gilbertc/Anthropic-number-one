import pytest

from nfl_edge import teams


@pytest.mark.parametrize(
    "text,code",
    [
        ("Kansas City Chiefs", "KC"), ("Chiefs", "KC"), ("KC", "KC"), ("kansas city", "KC"),
        ("Los Angeles Rams", "LA"), ("LA Rams", "LA"), ("Rams", "LA"), ("LAR", "LA"), ("St. Louis Rams", "LA"),
        ("Los Angeles Chargers", "LAC"), ("Chargers", "LAC"), ("San Diego Chargers", "LAC"),
        ("Las Vegas Raiders", "LV"), ("Oakland Raiders", "LV"), ("Raiders", "LV"),
        ("Washington Commanders", "WAS"), ("Washington Football Team", "WAS"), ("WSH", "WAS"),
        ("New York Giants", "NYG"), ("NY Jets", "NYJ"), ("Jets", "NYJ"), ("Giants", "NYG"),
        ("San Francisco 49ers", "SF"), ("49ers", "SF"), ("Niners", "SF"),
        ("Tampa Bay Buccaneers", "TB"), ("Bucs", "TB"), ("Jacksonville Jaguars", "JAX"), ("JAC", "JAX"),
        ("Green Bay Packers", "GB"), ("GNB", "GB"), ("New England Patriots", "NE"), ("NWE", "NE"),
        ("Will the Buffalo Bills win?", "BUF"), ("", None), ("Some Random Text", None),
    ],
)
def test_normalize(text, code):
    assert teams.normalize_team_name(text) == code


def test_canonical():
    assert teams.canonical("STL") == "LA"
    assert teams.canonical("sd") == "LAC"
    assert teams.canonical("OAK") == "LV"
    assert teams.canonical("KC") == "KC"


def test_teams_in_text_order_and_longest_match():
    assert teams.teams_in_text("Chiefs vs. Bills") == ["KC", "BUF"]
    assert teams.teams_in_text("Los Angeles Rams vs Los Angeles Chargers") == ["LA", "LAC"]
    assert teams.teams_in_text("NFL: New York Giants at New York Jets") == ["NYG", "NYJ"]
    assert teams.teams_in_text("Will the Detroit Lions win Super Bowl LXI?") == ["DET"]


def test_divisions():
    assert teams.division("KC") == "AFC West"
    assert teams.conference("STL") == "NFC"
    assert sorted(teams.division_members("DET")) == ["CHI", "DET", "GB", "MIN"]
    assert len(teams.DIVISIONS) == 32 and set(teams.DIVISIONS) == set(teams.CURRENT_TEAMS)
