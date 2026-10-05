"""Parser tests for bball.6h.py against trimmed real pages in tests/fixtures/bball/.

Run:  uv run --with pytest --with aiohttp --with beautifulsoup4 --with truststore pytest tests/
"""
import importlib.util
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "bball"

# Deterministic clock and zone: mid-season 2025-26, in St. Louis.
os.environ["BBALL_FAKE_TODAY"] = "2026-01-15"
os.environ["TZ"] = "America/Chicago"
time.tzset()

spec = importlib.util.spec_from_file_location("bball", ROOT / "bball.6h.py")
bb = importlib.util.module_from_spec(spec)
sys.modules["bball"] = bb          # dataclasses resolve string annotations via sys.modules
spec.loader.exec_module(bb)

LOCAL = ZoneInfo("America/Chicago")
CITIES = bb.DEFAULT_CONFIG["local_venue_cities"]


def read(name: str) -> str:
    return (FIX / name).read_text()


# --- helpers ---------------------------------------------------------------------------

def test_season_math():
    assert bb.season_start_year(date(2026, 10, 5)) == 2026
    assert bb.season_start_year(date(2027, 3, 1)) == 2026
    assert bb.season_slug() == "2025-26"
    assert bb.year_for_month(11) == 2025 and bb.year_for_month(2) == 2026


def test_parse_tipoff_time():
    assert bb.parse_tipoff_time("7:00PM").hour == 19
    assert bb.parse_tipoff_time("3:30 pm").hour == 15
    assert bb.parse_tipoff_time("11:30 AM").hour == 11
    assert bb.parse_tipoff_time("TBA") is None


# --- MaxPreps --------------------------------------------------------------------------

def test_maxpreps_schedule_from_contests_json():
    url = "https://www.maxpreps.com/il/belleville/belleville-east-lancers/basketball/schedule/"
    s = bb.parse_maxpreps_schedule(read("maxpreps_schedule_25-26.html"), url, CITIES)
    assert s.name == "Belleville East Lancers" and s.short == "Belleville East"
    assert s.record == "10-20" and s.streak == 1 and s.streak_type == "L"
    assert s.fetch_error is None
    assert s.rows_seen == 4 and s.rows_parsed == 3   # the placeholder contest is skipped
    home, neutral, away = s.schedule
    assert (home.date.date(), home.home_away, home.opponent) == (date(2025, 11, 24), "Home", "Ritenour")
    assert (home.result, home.score) == ("W", "68-56")
    assert home.tipoff_time == datetime(2025, 11, 24, 19, 30)
    assert home.game_url.startswith("https://www.maxpreps.com/") and "/game/" in home.game_url
    assert neutral.home_away == "Neutral" and neutral.opponent == "Jennings" and neutral.local is False
    assert away.home_away == "Away" and away.opponent == "Belleville West" and away.result == "L"


def test_maxpreps_table_fallback_when_json_shape_changes():
    url = "https://www.maxpreps.com/il/belleville/belleville-east-lancers/basketball/schedule/"
    html = read("maxpreps_schedule_25-26.html").replace('"contests": [[', '"contests": [["x",')
    s = bb.parse_maxpreps_schedule(html, url, CITIES)
    assert s.rows_parsed == 3                       # three table rows
    assert s.fetch_error is None and "shape changed" in s.warning
    g = s.schedule[0]
    assert (g.date.date(), g.opponent, g.result, g.score) == (date(2025, 11, 24), "Ritenour", "W", "68-56")
    assert g.tipoff_time == datetime(2025, 11, 24, 19, 30)
    assert g.game_url.startswith("https://www.maxpreps.com/")


def test_maxpreps_rankings():
    rank, tooltip = bb.parse_maxpreps_rankings(read("maxpreps_rankings_25-26.html"), "IL")
    assert rank == 208
    assert tooltip == "IL# 208 | IL Div# 79 | STL# 64"


# --- ESPN ------------------------------------------------------------------------------

def test_espn_schedule_tbd_neutral_and_completed():
    data = json.loads(read("espn_schedule_139.json"))
    games, seen = bb.parse_espn_schedule(data, "139", CITIES)
    assert seen == 3 and len(games) == 3
    tbd, neutral, done = games
    # TBD games are stored at midnight Eastern: the date must not slip to the previous day.
    assert tbd.date.date() == date(2026, 11, 2) and tbd.tipoff_time is None
    assert tbd.home_away == "Home" and tbd.opponent == "Lehigh"
    assert tbd.game_url.startswith("https://www.espn.com/")
    # Neutral site outside the area: kept in the schedule, flagged not local.
    assert neutral.home_away == "Neutral" and neutral.venue and neutral.local is False
    start = datetime.fromisoformat(data["events"][1]["date"].replace("Z", "+00:00"))
    assert neutral.tipoff_time == start.astimezone(LOCAL).replace(tzinfo=None)
    # Completed game: result and score from the API, our score first.
    assert done.result in ("W", "L") and done.score and "-" in done.score
    mine = next(c for c in data["events"][2]["competitions"][0]["competitors"] if c["team"]["id"] == "139")
    assert done.score.startswith(mine["score"]["displayValue"])


def test_espn_local_neutral_is_listed():
    data = json.loads(read("espn_schedule_139.json"))
    ev = data["events"][1]
    ev["competitions"][0]["venue"] = {"fullName": "Enterprise Center", "address": {"city": "St. Louis", "state": "MO"}}
    games, _ = bb.parse_espn_schedule(data, "139", CITIES)
    assert games[1].local is True
    s = bb.School(key="espn:139", url="", schedule=games)
    assert [g.opponent for g in bb.listed_games(s)] == ["Lehigh", games[1].opponent, done_opp(data)]


def done_opp(data):
    comp = data["events"][2]["competitions"][0]
    me = next(c for c in comp["competitors"] if c["team"]["id"] == "139")
    if me["homeAway"] != "home":
        return None  # not listed; the assertion above would have failed first
    return next(c for c in comp["competitors"] if c["team"]["id"] != "139")["team"]["shortDisplayName"]


def test_espn_team():
    name, short, record, rank, url = bb.parse_espn_team(json.loads(read("espn_team_139.json")))
    assert (name, short, record, rank) == ("Saint Louis Billikens", "Saint Louis", "12-3", 24)
    assert url.endswith("/saint-louis-billikens")


# --- NET / NJCAA / Region 24 -----------------------------------------------------------

def test_net_rankings_and_through_date():
    ranks, through = bb.parse_net_rankings(read("ncaa_net.html"))
    assert ranks["Illinois"] == 12 and ranks["SIUE"] == 201
    assert through == date(2026, 4, 6)
    assert bb.net_rank_for("Saint Louis", ranks) == 88
    assert bb.net_rank_for("saint louis", ranks) == 88
    assert bb.net_rank_for("Lindenwood", ranks) is None


def test_njcaa_polls_latest_d1():
    ranks, title = bb.parse_njcaa_polls(json.loads(read("njcaa_polls.json")))
    assert "Week 14" in title
    assert ranks == {"Snow College": 1, "Vincennes University": 9}   # unranked entries dropped


def test_region24_standings():
    recs = bb.parse_region24_standings(read("region24_standings.html"))
    assert recs["Southwestern Illinois College"] == "27-8"
    assert recs["Vincennes University"] == "12-21"


# --- SWIC / Vincennes ------------------------------------------------------------------

def test_swic_schedule():
    s = bb.parse_swic_schedule(read("swic_schedule.html"))
    assert s.rows_seen == 5 and s.rows_parsed == 5
    by_opp = {g.opponent: g for g in s.schedule}
    scrim = by_opp["Link Prep"]
    assert scrim.home_away == "Home" and scrim.note == "Scrimmage"
    assert scrim.date.date() == date(2025, 10, 20) and scrim.tipoff_time == datetime(2025, 10, 20, 17, 0)
    assert by_opp["Mineral Area College"].home_away == "Home"
    jam = by_opp["GRAC Jamboree"]
    assert jam.home_away == "Away" and jam.date.date() == date(2025, 10, 17)   # "Oct 17-18" -> first day
    played = by_opp["Brescia University JV"]
    assert (played.result, played.score) == ("W", "113-87")


def test_vincennes_schedule_dedupes_next_event_card_and_converts_eastern():
    url = "https://govutrailblazers.com/sports/mbkb/2025-26/schedule"
    s = bb.parse_vincennes_schedule(read("vincennes_schedule.html"), url)
    opps = [g.opponent for g in s.schedule]
    assert opps.count("Lake Land College") == 1          # next-event card + row -> one game
    assert "VU Jamboree" not in opps
    home = [g for g in s.schedule if g.home_away == "Home"]
    assert home and all(g.note == "Exhibition" for g in home)
    daytona = next(g for g in s.schedule if g.opponent.startswith("Daytona"))
    assert daytona.date.date() == date(2025, 10, 24)
    assert daytona.tipoff_time == datetime(2025, 10, 24, 14, 45)   # 3:45 PM Eastern -> Central


# --- Sidearm (D2 / D3 / NAIA) -----------------------------------------------------------

SIDEARM_URL = "https://mckbearcats.com/sports/mens-basketball/schedule/2025-26"


def test_parse_sidearm_time():
    t, tz = bb.parse_sidearm_time("7:00 PM")
    assert (t.hour, t.minute) == (19, 0) and tz == bb.CENTRAL
    assert bb.parse_sidearm_time("6 p.m.")[0].hour == 18
    assert bb.parse_sidearm_time("11:00 AM")[0].hour == 11
    assert bb.parse_sidearm_time("Noon (CT)")[0].hour == 12
    t, tz = bb.parse_sidearm_time("12 p.m. ET")
    assert t.hour == 12 and tz == bb.EASTERN
    assert bb.parse_sidearm_time("2 p.m. MDT")[1] == ZoneInfo("America/Denver")
    assert bb.parse_sidearm_time("TBA")[0] is None and bb.parse_sidearm_time("")[0] is None


def test_sidearm_completed_season():
    s = bb.parse_sidearm_schedule(read("sidearm_mckendree_25-26.html"), SIDEARM_URL, CITIES)
    assert s.record == "22-8"
    assert s.rows_seen == 7 and s.rows_parsed == 6          # the postponed game is dropped
    by_opp = {g.opponent: g for g in s.schedule}
    assert "Maryville University" not in by_opp
    exh = by_opp["Southern Illinois University"]             # "[Exhibition]" moved to the note
    assert (exh.home_away, exh.note, exh.result, exh.score) == ("Away", "Exhibition", "L", "42-83")
    home = by_opp["Hannibal-LaGrange University"]
    assert (home.home_away, home.result, home.score) == ("Home", "W", "80-39")
    assert home.date.date() == date(2025, 11, 21) and home.tipoff_time == datetime(2025, 11, 21, 19, 0)
    assert home.game_url.startswith("https://mckbearcats.com/") and home.game_url.endswith("/boxscore/19238")
    assert by_opp["Rockhurst University"].score == "95-85"   # "W, 95-85 (2OT)"
    far = by_opp["Malone University"]
    assert (far.home_away, far.venue, far.local) == ("Neutral", "Gillmor Center", False)
    glvc = by_opp["(3) University of Illinois Springfield"]  # conference tournament at UMSL
    assert (glvc.home_away, glvc.venue, glvc.local) == ("Neutral", "Mark Twain Building", True)
    assert glvc.date.date() == date(2026, 3, 6)
    assert "(2) RV William Jewell College" in by_opp           # site text has a "|" after "(2)"
    assert not any("|" in g.opponent or "|" in (g.venue or "") for g in s.schedule)
    assert {g.opponent for g in bb.listed_games(s)} == {
        "Hannibal-LaGrange University", "Rockhurst University", "(3) University of Illinois Springfield",
        "(2) RV William Jewell College"}


def test_sidearm_doubled_template_and_aria_label_times(monkeypatch):
    """UMSL repeats each row's inner blocks and only carries the time in the aria-label."""
    monkeypatch.setenv("BBALL_FAKE_TODAY", "2026-10-05")
    s = bb.parse_sidearm_schedule(read("sidearm_umsl_26-27.html"), SIDEARM_URL, CITIES)
    assert s.record == "0-0" and s.rows_seen == 5 and s.rows_parsed == 5
    assert [g.opponent for g in s.schedule] == [
        "Kentucky Wesleyan", "Hannibal-LaGrange", "Upper Iowa", "Parkside", "GLVC Tournament"]
    away, lebanon, home, tbd, tourney = s.schedule
    assert away.home_away == "Away" and away.tipoff_time == datetime(2026, 11, 9, 18, 0)   # "6 p.m."
    assert (lebanon.home_away, lebanon.venue, lebanon.local) == ("Neutral", "Melvin Price Convocation Center", True)
    assert lebanon.tipoff_time == datetime(2026, 11, 20, 17, 30)
    assert home.home_away == "Home" and home.date.date() == date(2026, 12, 5) and home.tipoff_time is not None
    assert home.result is None and home.score is None and home.game_url == SIDEARM_URL
    assert tbd.tipoff_time is None
    assert tourney.date.date() == date(2027, 3, 4) and tourney.tipoff_time is None and not tourney.local
    assert [g.opponent for g in bb.listed_games(s)] == ["Hannibal-LaGrange", "Upper Iowa"]


def test_sidearm_skips_third_party_games_and_converts_tagged_zones(monkeypatch):
    monkeypatch.setenv("BBALL_FAKE_TODAY", "2026-10-05")
    s = bb.parse_sidearm_schedule(read("sidearm_washu_26-27.html"), SIDEARM_URL, CITIES)
    assert s.rows_seen == 3 and s.rows_parsed == 2
    assert [g.opponent for g in s.schedule] == ["Bethany Lutheran College", "Hanover College"]   # not "Gustavus Adolphus vs. Transylvania"
    home, away_site = s.schedule
    assert home.home_away == "Home" and home.tipoff_time == datetime(2026, 11, 6, 19, 0)
    assert away_site.home_away == "Neutral" and not away_site.local
    assert away_site.tipoff_time == datetime(2026, 11, 21, 17, 0)        # "6 p.m. ET" -> Central


def test_sidearm_row_without_link_and_alumni_game(monkeypatch):
    monkeypatch.setenv("BBALL_FAKE_TODAY", "2026-10-05")
    s = bb.parse_sidearm_schedule(read("sidearm_harris_stowe_26-27.html"), SIDEARM_URL, CITIES)
    alumni, opener = s.schedule
    assert (alumni.opponent, alumni.home_away, alumni.note, alumni.tipoff_time) == ("Alumni", "Home", "Exhibition", None)
    assert opener.opponent == "Brescia University (Ky.)" and opener.tipoff_time == datetime(2026, 10, 24, 18, 0)   # "6 PM"
    assert bb.season_state([s]) == ("pre", date(2026, 10, 24))           # the alumni game does not open the season


def test_sidearm_record_and_empty_page():
    assert bb.parse_sidearm_record(read("sidearm_mckendree_25-26.html")) == "22-8"
    assert bb.parse_sidearm_record("<html></html>") is None
    assert bb.parse_sidearm_schedule("<html></html>", SIDEARM_URL, CITIES).problem == "no schedule rows found"


def test_small_college_key_url_and_menu_tag():
    entry = {"name": "McKendree", "level": "D2", "base": "https://mckbearcats.com/"}
    assert bb.small_college_key(entry) == "sidearm:mckbearcats.com"
    assert bb.sidearm_schedule_url(entry["base"]) == SIDEARM_URL
    mk = lambda name, level: bb.School(key=name, url="", name=name, level=level, record="3-1")
    order = [s.name for s in bb.sort_schools([mk("WashU", "D3"), mk("Missouri Baptist", "NAIA"), mk("UMSL", "D2"), mk("McKendree", "D2")])]
    assert order == ["McKendree", "UMSL", "WashU", "Missouri Baptist"]
    assert bb.school_line(mk("WashU", "D3"), "").startswith("[D3]    WashU")
    assert bb.school_line(bb.School(key="k", url="", name="Illinois"), "").startswith("[  -  ] Illinois")
    assert {e["level"] for e in bb.DEFAULT_CONFIG["small_colleges"]} == {"D2", "D3", "NAIA"}


# --- season state / display helpers -----------------------------------------------------

def test_season_state_ignores_exhibitions():
    g = lambda d, note=None: bb.Game(date=datetime(d.year, d.month, d.day), home_away="Home", opponent="X", note=note)
    s = bb.School(key="k", url="", schedule=[g(date(2026, 1, 20)), g(date(2026, 3, 1))])
    assert bb.season_state([s]) == ("in", None)
    s = bb.School(key="k", url="", schedule=[g(date(2026, 1, 18), "Scrimmage"), g(date(2026, 2, 10))])
    assert bb.season_state([s]) == ("pre", date(2026, 2, 10))
    s = bb.School(key="k", url="", schedule=[g(date(2025, 12, 1))])
    assert bb.season_state([s]) == ("over", None)


def test_health_problem_messages():
    assert bb.School(key="k", url="").problem == "no schedule rows found"
    assert bb.School(key="k", url="", rows_seen=5).problem == "0 of 5 rows parsed (layout changed?)"
    assert bb.School(key="k", url="", rows_seen=5, rows_parsed=5).problem is None
    assert bb.School(key="k", url="", fetch_error="HTTP 403").problem == "HTTP 403"


def test_cache_roundtrip():
    g = bb.Game(date=datetime(2026, 1, 20), home_away="Home", opponent="X", tipoff_time=datetime(2026, 1, 20, 19, 0))
    s = bb.School(key="k", url="u", name="N", schedule=[g], rows_seen=1, rows_parsed=1,
                  last_successful_update=datetime(2026, 1, 15, 8, 0))
    back = bb.school_from_dict(json.loads(json.dumps(bb.school_to_dict(s))))
    assert back == s
    failed = bb.School(key="k", url="u", fetch_error="Timeout")
    replaced = bb.apply_cache(failed, {"k": back})
    assert replaced.schedule == [g] and replaced.stale_since == datetime(2026, 1, 15, 8, 0)
    assert replaced.fetch_error == "Timeout"
