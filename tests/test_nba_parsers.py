"""Parser tests for nba.1h.py against trimmed real ESPN payloads in tests/fixtures/nba/.

The fixtures are real responses captured 2026-10-25 with the bulk keys dropped, except
for two deliberate edits in scoreboard.json: the second event's status was forced to
in-progress and the third's to final, because every game on that day's real slate was
still pre-tip. Everything else — records, broadcasts, teams, stats — is untouched.

Run:  uv run --with pytest --with aiohttp --with truststore pytest tests/
"""
import importlib.util
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "nba"

# Deterministic clock and zone: the day the fixtures were captured, in St. Louis.
os.environ["NBA_FAKE_TODAY"] = "2026-10-25"
os.environ["TZ"] = "America/Chicago"
time.tzset()

spec = importlib.util.spec_from_file_location("nba", ROOT / "nba.1h.py")
nba = importlib.util.module_from_spec(spec)
sys.modules["nba"] = nba           # dataclasses resolve string annotations via sys.modules
spec.loader.exec_module(nba)

CENTRAL = ZoneInfo("America/Chicago")


def read(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())


# --- scoreboard ------------------------------------------------------------------------

def test_scoreboard_parses_every_game_state():
    games, seen = nba.parse_scoreboard(read("scoreboard"), CENTRAL)
    assert seen == 3 and len(games) == 3
    assert [g.matchup for g in games] == ["LAL @ UTAH", "IND @ BKN", "ORL @ NY"]
    assert [g.state for g in games] == ["pre", "in", "post"]


def test_scoreboard_converts_tipoff_to_the_configured_zone():
    games, _ = nba.parse_scoreboard(read("scoreboard"), CENTRAL)
    # 21:00Z on an October Sunday is 16:00 in Central Daylight Time.
    assert games[0].tipoff == datetime(2026, 10, 25, 16, 0, tzinfo=CENTRAL)
    assert games[0].detail == "4:00p"


def test_scoreboard_pre_game_carries_records_not_scores():
    pre = nba.parse_scoreboard(read("scoreboard"), CENTRAL)[0][0]
    assert (pre.away_record, pre.home_record) == ("2-0", "1-1")
    # ESPN sends "0" before tip; it is a real zero, not a missing value.
    assert (pre.away_score, pre.home_score) == (0, 0)


def test_scoreboard_live_game_shows_quarter_and_clock():
    live = nba.parse_scoreboard(read("scoreboard"), CENTRAL)[0][1]
    assert live.state == "in"
    assert live.detail == "Q3 4:21"
    assert (live.away_score, live.home_score) == (71, 78)


def test_scoreboard_final_game_shows_final():
    final = nba.parse_scoreboard(read("scoreboard"), CENTRAL)[0][2]
    assert final.state == "post" and final.detail == "Final"
    assert (final.away_score, final.home_score) == (114, 109)


def test_scoreboard_broadcast_skips_league_pass():
    games, _ = nba.parse_scoreboard(read("scoreboard"), CENTRAL)
    assert games[0].tv == "Spectrum Sports Net"
    # ORL @ NY carries only "NBA League Pass", which is not a network worth showing.
    assert games[2].tv is None


def test_scoreboard_game_knows_its_teams():
    games, _ = nba.parse_scoreboard(read("scoreboard"), CENTRAL)
    utah_home = games[0]
    assert utah_home.involves({utah_home.home_id})
    assert utah_home.involves({utah_home.away_id})
    assert not utah_home.involves({"9999"})


# --- team schedule ---------------------------------------------------------------------

def test_team_schedule_parses_and_sorts():
    games, seen = nba.parse_team_schedule(read("team_schedule_13"), CENTRAL)
    assert seen == 4 and len(games) == 4
    assert [g.tipoff for g in games] == sorted(g.tipoff for g in games)
    assert [g.state for g in games] == ["post", "post", "pre", "pre"]


def test_team_schedule_reads_the_nested_score_shape():
    """Scoreboard scores are strings; schedule scores are {value, displayValue}."""
    games, _ = nba.parse_team_schedule(read("team_schedule_13"), CENTRAL)
    assert (games[0].away, games[0].home) == ("GS", "LAL")
    assert (games[0].away_score, games[0].home_score) == (105, 127)


def test_team_schedule_broadcast_prefers_national():
    games, _ = nba.parse_team_schedule(read("team_schedule_13"), CENTRAL)
    assert games[0].tv == "ESPN"          # market type National
    assert games[3].tv == "Peacock"       # market type National, streaming


# --- athlete ---------------------------------------------------------------------------

def test_athlete_identity():
    info = nba.parse_athlete(read("athlete_1966"))
    assert info["name"] == "LeBron James"
    assert info["team_abbr"] == "PHI" and info["team_id"] == "20"
    assert info["jersey"] == "23" and info["position"] == "F"
    assert info["injury"] is None


def test_athlete_injury_is_status_plus_detail():
    info = nba.parse_athlete(read("athlete_1966_injured"))
    assert info["injury"] == "Questionable — Left Ankle Sprain"


def test_athlete_handles_an_empty_payload():
    info = nba.parse_athlete({})
    assert info["name"] == "" and info["injury"] is None


# --- averages, career totals, game log -------------------------------------------------

def test_overview_averages_use_the_current_season_split():
    avg = nba.parse_overview_averages(read("overview_1966"))
    assert avg["PTS"] == "15.5" and avg["REB"] == "9.5" and avg["AST"] == "6.0"
    assert avg["MIN"] == "33.5" and avg["GP"] == "2"
    # The career split must not leak into the season line.
    assert avg["PTS"] != "26.8"


def test_career_points_come_from_the_totals_row():
    assert nba.parse_career_points(read("stats_1966")) == 43471


def test_career_points_missing_is_none_not_a_crash():
    assert nba.parse_career_points({}) is None
    assert nba.parse_career_points({"categories": [{"name": "averages"}]}) is None


def test_gamelog_keeps_regular_season_only_newest_first():
    log, seen = nba.parse_gamelog(read("gamelog_1966"), CENTRAL)
    # The fixture also holds two preseason games, which must not be counted.
    assert seen == 2 and len(log) == 2
    assert [g.played_on for g in log] == [date(2026, 10, 22), date(2026, 10, 20)]


def test_gamelog_rows_carry_labelled_stats():
    log, _ = nba.parse_gamelog(read("gamelog_1966"), CENTRAL)
    first = log[0]
    assert first.opponent == "CLE" and first.at_vs == "vs" and first.result == "W"
    assert first.stat("PTS") == "17" and first.stat("REB") == "10"
    assert first.stat("FG") == "5-14" and first.stat("3PT") == "1-4"
    assert first.number("PTS") == 17.0
    assert first.stat("NOPE") == "—" and first.number("NOPE") is None


def test_gamelog_marks_road_games():
    log, _ = nba.parse_gamelog(read("gamelog_1966"), CENTRAL)
    assert log[1].at_vs == "@" and log[1].opponent == "NY"


# --- leaders ---------------------------------------------------------------------------

def test_leaders_pick_per_game_not_season_total():
    """Both categories are abbreviated PTS. Selecting by abbreviation would return the
    season-total leader (Maxey, 86.0) instead of the scoring leader (Jerome, 36.5)."""
    table, seen = nba.parse_leaders(read("leaders"), ["PTS", "AST", "REB"])
    assert seen == 4
    assert table["PTS"][0].name == "Ty Jerome"
    assert table["PTS"][0].value == "36.5"
    assert table["PTS"][0].short == "T. Jerome"
    assert table["PTS"][0].team == "MEM"
    assert table["PTS"][0].rank == 1


def test_leaders_rank_sequentially_and_cover_each_category():
    table, _ = nba.parse_leaders(read("leaders"), ["PTS", "AST", "REB"])
    assert set(table) == {"PTS", "AST", "REB"}
    assert [l.rank for l in table["AST"]] == [1, 2, 3, 4, 5]
    assert table["REB"][0].name == "Domantas Sabonis"


def test_leaders_skip_categories_espn_does_not_return():
    table, _ = nba.parse_leaders(read("leaders"), ["PTS", "BLK", "nonsense"])
    assert set(table) == {"PTS"}


# --- standings ---------------------------------------------------------------------------

def test_standings_sort_by_seed_despite_unsorted_input():
    """ESPN returns entries in an arbitrary order — the fixture starts at the 14 seed."""
    raw = read("standings")
    assert [e["team"]["abbreviation"] for e in raw["children"][0]["standings"]["entries"]] \
        == ["MIL", "MIA", "NY", "BKN"]
    table, seen = nba.parse_standings(raw)
    assert seen == 8
    assert [t.abbr for t in table["East"]] == ["NY", "BKN", "MIL", "MIA"]
    assert [t.seed for t in table["East"]] == [11, 12, 14, 15]


def test_standings_read_record_streak_and_last_ten():
    table, _ = nba.parse_standings(read("standings"))
    ny = table["East"][0]
    assert ny.record == "0-2" and ny.games_behind == "2.5"
    assert ny.streak == "L2" and ny.last_ten == "0-2"


def test_standings_cover_both_conferences():
    table, _ = nba.parse_standings(read("standings"))
    assert set(table) == {"East", "West"}
    assert nba.standing_for(table, table["West"][0].team_id)[0] == "West"
    assert nba.standing_for(table, "9999") is None


def test_standings_survive_an_empty_payload():
    table, seen = nba.parse_standings({})
    assert table == {} and seen == 0


# --- derived views -----------------------------------------------------------------------

def game(event_id, days_ahead, away="AAA", home="BBB", state="pre", away_id="1", home_id="2"):
    tip = datetime(2026, 10, 25, 19, 0, tzinfo=CENTRAL) + timedelta(days=days_ahead)
    return nba.Game(event_id=event_id, tipoff=tip, away=away, home=home,
                    away_id=away_id, home_id=home_id, state=state)


def test_upcoming_games_dedupe_a_shared_matchup():
    """A game between two tracked teams sits on both schedules and must appear once."""
    shared = game("shared", 2, away_id="1", home_id="2")
    schedules = {"1": [game("a", 1), shared], "2": [shared, game("b", 3)]}
    picked = nba.upcoming_games(schedules, 5)
    assert [g.event_id for g in picked] == ["a", "shared", "b"]


def test_upcoming_games_drop_finished_games_and_honour_the_count():
    schedules = {"1": [game("old", -3, state="post"), game("x", 1), game("y", 2)]}
    assert [g.event_id for g in nba.upcoming_games(schedules, 1)] == ["x"]


def test_head_to_head_finds_the_next_meeting_only():
    meeting = game("meet", 5, away_id="1", home_id="2")
    schedules = {"1": [game("other", 1, away_id="1", home_id="9"), meeting]}
    assert nba.head_to_head(schedules, {"1", "2"}).event_id == "meet"
    assert nba.head_to_head(schedules, {"1"}) is None
    assert nba.head_to_head(schedules, {"1", "7"}) is None


def test_season_highs_from_the_log():
    log, _ = nba.parse_gamelog(read("gamelog_1966"), CENTRAL)
    assert nba.season_highs(log) == {"PTS": 17.0, "REB": 10.0, "AST": 7.0}
    assert nba.season_highs([]) == {}


def test_milestone_counts_to_the_next_thousand():
    assert nba.milestone(43471, 1000) == (43471, 44000, 529)
    # A player sitting exactly on a milestone is chasing the next one, not standing still.
    assert nba.milestone(30000, 1000) == (30000, 31000, 1000)
    assert nba.milestone(None, 1000) is None
    assert nba.milestone(100, 0) is None


def test_season_state_is_in_when_games_are_on_today():
    games, _ = nba.parse_scoreboard(read("scoreboard"), CENTRAL)
    state, first = nba.season_state(nba.Bundle(games=games))
    assert state == "in" and first == date(2026, 10, 25)


def test_season_state_goes_pre_only_when_the_next_tip_is_far_off():
    near = nba.Bundle(schedules={"1": [game("soon", 3)]})
    assert nba.season_state(near)[0] == "in"
    far = nba.Bundle(schedules={"1": [game("later", 60)]})
    state, first = nba.season_state(far)
    assert state == "pre" and first == date(2026, 12, 24)
    assert nba.season_state(nba.Bundle()) == ("pre", None)


# --- rendering -------------------------------------------------------------------------

def test_scrub_keeps_leading_spaces_and_kills_pipes():
    """SwiftBar splits on the last '|', and the leading spaces are what align the tables."""
    assert nba.scrub("  padded  ") == "  padded"
    assert "|" not in nba.scrub("a | b")
    assert nba.scrub(None) == ""


@pytest.mark.parametrize("away,home", [("LAL", "UTAH"), ("NY", "SA"), ("PHI", "GS")])
def test_slate_columns_line_up_whatever_the_abbreviation(away, home):
    head = nba.slate_row(" ", "AWAY", "REC", " ", "HOME", "REC", "STATUS", "TV")
    row = nba.slate_row("●", away, "2-0", "@", home, "1-1", "7:00p", "ESPN")
    assert row.index("@") == head.index("HOME") - 2
    assert row.index("2-0") == head.index("REC")
    assert row.index("7:00p") == head.index("STATUS")


def test_standings_and_log_columns_line_up():
    head = nba.standings_row(" ", "#", "TEAM", "W-L", "GB", "STRK", "L10")
    row = nba.standings_row("●", "1", "PHI", "3-0", "-", "W3", "3-0")
    assert row.index("PHI") == head.index("TEAM")
    assert row.index("W3") == head.index("STRK")
    log_head = nba.log_row("DATE", "OPP", "W/L", "MIN", "PTS", "REB", "AST", "FG", "3PT")
    log = nba.log_row("Oct 22", "vs CLE", "W", "33", "17", "10", "5", "5-14", "1-4")
    assert log.index("vs CLE") == log_head.index("OPP")
    assert log.index("5-14") == log_head.index("FG")


def test_game_row_switches_records_for_scores_once_the_ball_is_up():
    games, _ = nba.parse_scoreboard(read("scoreboard"), CENTRAL)
    assert "2-0" in nba.game_row(games[0], set())
    live = nba.game_row(games[1], set())
    assert "71" in live and "78" in live and "Q3 4:21" in live


def test_game_row_marks_a_tracked_team():
    games, _ = nba.parse_scoreboard(read("scoreboard"), CENTRAL)
    assert nba.game_row(games[0], {games[0].home_id}).startswith(nba.TRACKED_MARK)
    assert nba.game_row(games[0], set()).startswith(" ")


def test_calendar_line_is_an_option_alternate_for_fantastical():
    games, _ = nba.parse_scoreboard(read("scoreboard"), CENTRAL)
    out = nba.calendar_line(games[0], 0, "LAL @ UTAH")
    assert out.startswith("Add to Calendar · LAL @ UTAH |")
    assert "x-fantastical3://parse?add=1&sentence=" in out
    assert "alternate=true" in out and "terminal=false" in out
    assert "2026/10/25%20at%201600" in out      # local tipoff, not the UTC hour


def test_relative_day_labels():
    assert nba.relative_day(date(2026, 10, 25)) == "Today"
    assert nba.relative_day(date(2026, 10, 26)) == "Tomorrow"
    assert nba.relative_day(date(2026, 10, 24)) == "Yesterday"
    assert nba.relative_day(date(2026, 10, 28)) == "Wed"
    assert nba.relative_day(date(2026, 12, 1)) == "Dec 01"


def test_clock_label_is_compact():
    assert nba.clock_label(datetime(2026, 10, 25, 19, 30)) == "7:30p"
    assert nba.clock_label(datetime(2026, 10, 25, 0, 5)) == "12:05a"
    assert nba.clock_label(datetime(2026, 10, 25, 12, 0)) == "12:00p"


# --- cache ------------------------------------------------------------------------------

def built_bundle():
    games, _ = nba.parse_scoreboard(read("scoreboard"), CENTRAL)
    standings, _ = nba.parse_standings(read("standings"))
    leaders, _ = nba.parse_leaders(read("leaders"), ["PTS"])
    player = nba.Player(pid="1966", **nba.parse_athlete(read("athlete_1966")))
    player.averages = nba.parse_overview_averages(read("overview_1966"))
    player.log, _ = nba.parse_gamelog(read("gamelog_1966"), CENTRAL)
    player.career_points = nba.parse_career_points(read("stats_1966"))
    return nba.Bundle(games=games, standings=standings, leaders=leaders, players=[player],
                      schedules={"13": nba.parse_team_schedule(read("team_schedule_13"), CENTRAL)[0]})


def test_bundle_survives_a_cache_round_trip():
    original = built_bundle()
    restored = nba.bundle_from_dict(json.loads(json.dumps(nba.bundle_to_dict(original))), CENTRAL)
    assert [g.matchup for g in restored.games] == [g.matchup for g in original.games]
    assert restored.games[0].tipoff == original.games[0].tipoff
    assert restored.standings["East"][0].abbr == original.standings["East"][0].abbr
    assert restored.leaders["PTS"][0].value == "36.5"
    assert restored.players[0].log[0].played_on == date(2026, 10, 22)
    assert restored.players[0].career_points == 43471
    assert len(restored.schedules["13"]) == 4


def test_apply_cache_fills_a_failed_section_and_marks_it_stale():
    cached = built_bundle()
    fresh = nba.Bundle(errors={"standings": "HTTP 503"})
    merged = nba.apply_cache(fresh, cached)
    assert merged.standings["East"][0].abbr == "NY"
    assert "standings" in merged.stale


def test_apply_cache_leaves_a_healthy_section_alone():
    cached = built_bundle()
    fresh = built_bundle()
    fresh.standings = {"East": [nba.TeamStanding(team_id="1", abbr="ZZZ", name="Test")]}
    merged = nba.apply_cache(fresh, cached)
    assert merged.standings["East"][0].abbr == "ZZZ"
    assert merged.stale == []


def test_apply_cache_substitutes_a_failed_player():
    cached = built_bundle()
    fresh = nba.Bundle(players=[nba.Player(pid="1966", fetch_error="HTTP 500")])
    merged = nba.apply_cache(fresh, cached)
    assert merged.players[0].name == "LeBron James"
    assert any(s.startswith("player ") for s in merged.stale)


def test_apply_cache_without_a_cache_is_a_no_op():
    fresh = nba.Bundle(errors={"games": "boom"})
    assert nba.apply_cache(fresh, None) is fresh


# --- config -----------------------------------------------------------------------------

def test_tracked_teams_come_from_the_players_plus_extras():
    players = [nba.Player(pid="1966", name="LeBron James", team_id="20"),
               nba.Player(pid="3992", name="James Harden", team_id="5")]
    assert nba.tracked_team_ids(players, {}) == {"20", "5"}
    assert nba.tracked_team_ids(players, {"extra_teams": [13]}) == {"20", "5", "13"}
    # A player whose fetch failed has no team and must not add an empty id.
    assert nba.tracked_team_ids([nba.Player(pid="x")], {}) == set()


def test_every_configured_leader_category_has_a_known_espn_name():
    cfg = nba.DEFAULT_CONFIG
    for abbr in cfg["leader_categories"] + cfg["more_leader_categories"]:
        assert abbr in nba.LEADER_CATEGORY_NAMES, abbr
