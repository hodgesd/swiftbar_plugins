#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "aiohttp>=3.8.0",
#     "truststore>=0.9.0",
# ]
# ///

# <swiftbar.title>NBA</swiftbar.title>
# <swiftbar.version>v1.0</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Tonight's NBA scoreboard, league leaders and standings, with season lines and game logs for tracked players. Configurable via ~/.config/swiftbar-plugins/nba.json</swiftbar.desc>
# <swiftbar.dependencies>uv</swiftbar.dependencies>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

"""
NBA — SwiftBar plugin

A league tracker first: tonight's whole slate, the scoring/assist/rebound leaders and both
conference standings. Tracked players (LeBron James and James Harden by default) get their
own section, and their games are flagged in the slate — they are a lens on the league, not
the subject of the plugin.

Sources (all ESPN, free and undocumented; shapes probed 2026-10-25)
  Scoreboard        site.api.espn.com/.../nba/scoreboard — today's games, live score and
                    clock, team records, broadcast.
  Standings         site.web.api.espn.com/apis/v2/.../nba/standings — children[] are the
                    conferences; entries carry playoffSeed, gamesBehind, streak, L10.
                    Entries arrive unsorted, so they are re-sorted by seed.
  Leaders           site.web.api.espn.com/apis/site/v3/.../nba/leaders — categories are
                    keyed by `name` ("pointsPerGame"), never by `abbreviation`: PTS is used
                    by both the per-game and the season-total category.
  Athlete           .../v3/sports/basketball/nba/athletes/{id} — team, jersey, position,
                    injury status. This is where a player's team comes from each run.
  Overview          .../athletes/{id}/overview — season averages, splits[0] is the current
                    regular season, splits[-1] is career.
  Game log          .../athletes/{id}/gamelog — seasonTypes[].categories[] are months of
                    eventId+stats; events{} maps eventId to date, opponent and result.
  Career totals     .../athletes/{id}/stats — categories[name=totals].totals is the career
                    line; its last column is points, which drives the milestone watch.
  Team schedule     site.api.espn.com/.../nba/teams/{id}/schedule — the full 82, used for
                    NEXT UP. Note its broadcast shape differs from the scoreboard's.

Config (optional)   ~/.config/swiftbar-plugins/nba.json — every key has a default; see
                    DEFAULT_CONFIG. `players` are ESPN athlete IDs and the tracked teams
                    are derived from them on every run, so a trade needs no edit here.
Cache               ~/.cache/swiftbar-plugins/nba_cache.json — last good copy of each
                    section, shown (marked stale) when that source fails.

CLI
  ./nba.1h.py            SwiftBar output
  ./nba.1h.py --check    one line per source with rows seen / parsed; exit 1 on any
                         failure. Run it when the menu looks wrong.
Env
  NBA_FAKE_TODAY=YYYY-MM-DD   pretend it is another day (layout testing)
  NBA_NO_CACHE=1              ignore the cache
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
from zoneinfo import ZoneInfo

import aiohttp
import truststore

# --- CONFIGURATION -------------------------------------------------------------------

CONFIG_PATH = Path(os.path.expanduser("~/.config/swiftbar-plugins/nba.json"))
_XDG_CACHE = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
CACHE_FILE = Path(_XDG_CACHE) / "swiftbar-plugins" / "nba_cache.json"
CACHE_MAX_AGE_DAYS = 7

DEFAULT_CONFIG: dict[str, Any] = {
    # ESPN athlete IDs. Their teams are looked up every run.
    "players": [1966, 3992],          # LeBron James, James Harden
    "extra_teams": [],                # ESPN team IDs followed without a player
    "max_past_games": 5,
    "next_up_count": 3,
    "leader_categories": ["PTS", "AST", "REB"],        # on the LEAGUE LEADERS line
    "more_leader_categories": ["STL", "BLK", "3PM"],   # submenu only
    "timezone": "America/Chicago",
    # A figure dribbling, so the menu bar reads differently from bball.6h.py's ball.
    # Any SF Symbol name works; SwiftBar draws nothing if the name is unknown.
    "sf_symbol": "figure.basketball",
    "milestone_step": 1000,
}

FETCH_TIMEOUT_SECONDS = 20
FETCH_RETRY_COUNT = 1
FETCH_LIMIT_PER_HOST = 4
OFF_SEASON_WINDOW_DAYS = 10
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

SITE_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
WEB_API = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba"
LEADERS_URL = "https://site.web.api.espn.com/apis/site/v3/sports/basketball/nba/leaders?limit=10"
STANDINGS_URL = "https://site.web.api.espn.com/apis/v2/sports/basketball/nba/standings"
SCOREBOARD_URL = f"{SITE_API}/scoreboard"
NBA_HOME = "https://www.espn.com/nba/"

# ESPN's leaders feed repeats an abbreviation across a per-game and a season-total
# category, so every category this plugin shows is pinned by its stable `name`.
LEADER_CATEGORY_NAMES = {
    "PTS": "pointsPerGame",
    "AST": "assistsPerGame",
    "REB": "reboundsPerGame",
    "STL": "stealsPerGame",
    "BLK": "blocksPerGame",
    "3PM": "3PointsMadePerGame",
    "FG%": "fieldGoalPercentage",
    "3P%": "3PointPct",
    "FT%": "FreeThrowPct",
    "MPG": "minutesPerGame",
    "PER": "PER",
}

# --- PRESENTATION --------------------------------------------------------------------

# "SFMono-Regular" silently falls back to the proportional system font; Menlo does not.
TABLE = "font=Menlo size=11"
TABLE_BOLD = "font=Menlo-Bold size=11"
HEADER_FONT = "font=SF Pro Display size=13"
# SwiftBar takes color=<light mode>,<dark mode>. The light halves are Apple's darker
# accessible variants, readable on the pale menu background. Secondary text is left
# uncolored so it follows the system label color, which tracks the menu's appearance.
DIMMED = ""
ACCENT_GREEN = "color=#1F7A35,#34C759"
ACCENT_RED = "color=#C4000F,#FF3B30"
ACCENT_ORANGE = "color=#B33000,#FF9500"
ACCENT_BLUE = "color=#0040DD,#0A84FF"
ACCENT_YELLOW = "color=#8A6100,#FFD60A"
LABEL_COLOR = "color=#3A3A3C,#AEAEB2"

TRACKED_MARK = "●"


# --- MODELS --------------------------------------------------------------------------

@dataclass
class Game:
    """One NBA game, from either the scoreboard or a team schedule."""
    event_id: str
    tipoff: datetime                   # tz-aware, in the configured zone
    away: str                          # team abbreviation
    home: str
    away_id: str = ""
    home_id: str = ""
    away_name: str = ""
    home_name: str = ""
    away_record: Optional[str] = None
    home_record: Optional[str] = None
    away_score: Optional[int] = None
    home_score: Optional[int] = None
    state: str = "pre"                 # "pre" | "in" | "post"
    detail: str = ""                   # "Final", "Q3 4:21", "7:00p"
    tv: Optional[str] = None
    url: Optional[str] = None
    venue: Optional[str] = None

    @property
    def matchup(self) -> str:
        return f"{self.away} @ {self.home}"

    def involves(self, team_ids: set[str]) -> bool:
        return self.away_id in team_ids or self.home_id in team_ids


@dataclass
class PlayerGame:
    """One row of a player's game log."""
    event_id: str
    played_on: date
    opponent: str                      # abbreviation
    at_vs: str                         # "vs" | "@"
    result: Optional[str]              # "W" | "L"
    score: Optional[str]               # "114-106", the player's team first
    stats: dict[str, str] = field(default_factory=dict)   # keyed by ESPN's labels
    url: Optional[str] = None

    def stat(self, label: str, default: str = "—") -> str:
        return self.stats.get(label) or default

    def number(self, label: str) -> Optional[float]:
        try:
            return float(self.stats.get(label, ""))
        except (TypeError, ValueError):
            return None


@dataclass
class Player:
    pid: str
    name: str = ""
    short_name: str = ""
    team_id: str = ""
    team_abbr: str = ""
    team_name: str = ""
    jersey: Optional[str] = None
    position: Optional[str] = None
    injury: Optional[str] = None            # "Questionable — ankle", None when active
    averages: dict[str, str] = field(default_factory=dict)   # ESPN labels: PTS, REB, ...
    log: list[PlayerGame] = field(default_factory=list)
    career_points: Optional[int] = None
    fetch_error: Optional[str] = None

    @property
    def healthy(self) -> bool:
        return not self.fetch_error and bool(self.name)

    @property
    def label(self) -> str:
        bits = [self.name]
        if self.team_abbr:
            bits.append(f"· {self.team_abbr}")
        if self.jersey:
            bits.append(f"#{self.jersey}")
        return " ".join(bits)


@dataclass
class Leader:
    rank: int
    athlete_id: str
    name: str
    team: str
    value: str
    short: str = ""                    # ESPN's "T. Jerome" — survives suffixes like Jr.


@dataclass
class TeamStanding:
    team_id: str
    abbr: str
    name: str
    wins: int = 0
    losses: int = 0
    seed: Optional[int] = None
    games_behind: str = "—"
    streak: str = ""
    last_ten: str = ""

    @property
    def record(self) -> str:
        return f"{self.wins}-{self.losses}"


@dataclass
class Bundle:
    """Everything one run fetched, plus a per-section error so the cache can fill gaps."""
    games: list[Game] = field(default_factory=list)
    standings: dict[str, list[TeamStanding]] = field(default_factory=dict)
    leaders: dict[str, list[Leader]] = field(default_factory=dict)
    players: list[Player] = field(default_factory=list)
    schedules: dict[str, list[Game]] = field(default_factory=dict)   # team_id -> games
    errors: dict[str, str] = field(default_factory=dict)
    stale: list[str] = field(default_factory=list)


# --- TIME HELPERS --------------------------------------------------------------------

def local_zone(cfg: dict) -> ZoneInfo:
    try:
        return ZoneInfo(cfg.get("timezone") or "America/Chicago")
    except Exception:
        return ZoneInfo("America/Chicago")


def today() -> date:
    """Today, or NBA_FAKE_TODAY when set, so layouts can be tested on any day."""
    fake = os.environ.get("NBA_FAKE_TODAY")
    if fake:
        try:
            return date.fromisoformat(fake)
        except ValueError:
            pass
    return date.today()


def parse_iso(value: str, tz: ZoneInfo) -> Optional[datetime]:
    """ESPN hands out '2026-10-25T21:00Z' and '...T23:00:00.000+00:00'."""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def clock_label(dt: datetime) -> str:
    """'7:00p' — tighter than %I:%M %p in a Menlo column."""
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d}{'p' if dt.hour >= 12 else 'a'}"


def relative_day(d: date) -> str:
    delta = (d - today()).days
    if delta == 0:
        return "Today"
    if delta == 1:
        return "Tomorrow"
    if delta == -1:
        return "Yesterday"
    if 0 < delta < 7:
        return d.strftime("%a")
    return d.strftime("%b %d")


# --- CONFIG & CACHE ------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text())
            if isinstance(user, dict):
                cfg.update(user)
        except Exception:
            pass
    return cfg


def _game_to_dict(g: Game) -> dict:
    d = asdict(g)
    d["tipoff"] = g.tipoff.isoformat()
    return d


def _game_from_dict(d: dict, tz: ZoneInfo) -> Game:
    d = dict(d)
    dt = datetime.fromisoformat(d["tipoff"])
    d["tipoff"] = dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)
    return Game(**d)


def _player_to_dict(p: Player) -> dict:
    d = asdict(p)
    d["log"] = [{**asdict(g), "played_on": g.played_on.isoformat()} for g in p.log]
    return d


def _player_from_dict(d: dict) -> Player:
    d = dict(d)
    d["log"] = [PlayerGame(**{**g, "played_on": date.fromisoformat(g["played_on"])})
                for g in d.get("log", [])]
    return Player(**d)


def bundle_to_dict(b: Bundle) -> dict:
    return {
        "games": [_game_to_dict(g) for g in b.games],
        "standings": {k: [asdict(t) for t in v] for k, v in b.standings.items()},
        "leaders": {k: [asdict(l) for l in v] for k, v in b.leaders.items()},
        "players": [_player_to_dict(p) for p in b.players],
        "schedules": {k: [_game_to_dict(g) for g in v] for k, v in b.schedules.items()},
    }


def bundle_from_dict(d: dict, tz: ZoneInfo) -> Bundle:
    return Bundle(
        games=[_game_from_dict(g, tz) for g in d.get("games", [])],
        standings={k: [TeamStanding(**t) for t in v] for k, v in (d.get("standings") or {}).items()},
        leaders={k: [Leader(**l) for l in v] for k, v in (d.get("leaders") or {}).items()},
        players=[_player_from_dict(p) for p in d.get("players", [])],
        schedules={k: [_game_from_dict(g, tz) for g in v] for k, v in (d.get("schedules") or {}).items()},
    )


def load_cache(tz: ZoneInfo) -> tuple[Optional[Bundle], Optional[datetime]]:
    if os.environ.get("NBA_NO_CACHE"):
        return None, None
    try:
        payload = json.loads(CACHE_FILE.read_text())
        stamp = datetime.fromisoformat(payload["timestamp"])
        if datetime.now() - stamp > timedelta(days=CACHE_MAX_AGE_DAYS):
            return None, None
        return bundle_from_dict(payload["data"], tz), stamp
    except Exception:
        return None, None


def save_cache(fresh: Bundle, previous: Optional[Bundle]) -> None:
    """Only sections that came back clean overwrite the cache; the rest keep their copy."""
    try:
        merged = bundle_to_dict(previous) if previous else bundle_to_dict(Bundle())
        new = bundle_to_dict(fresh)
        for section in ("games", "standings", "leaders", "schedules"):
            if section not in fresh.errors and new[section]:
                merged[section] = new[section]
        if "players" not in fresh.errors:
            by_id = {p["pid"]: p for p in merged.get("players", [])}
            for p in fresh.players:
                if p.healthy:
                    by_id[p.pid] = _player_to_dict(p)
            merged["players"] = list(by_id.values())
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"timestamp": datetime.now().isoformat(), "data": merged}))
    except Exception:
        pass


def apply_cache(fresh: Bundle, cached: Optional[Bundle]) -> Bundle:
    """Substitute the last good copy of any section whose fetch failed, marked stale."""
    if not cached:
        return fresh
    for section in ("games", "standings", "leaders", "schedules"):
        if section in fresh.errors and getattr(cached, section):
            setattr(fresh, section, getattr(cached, section))
            fresh.stale.append(section)
    by_id = {p.pid: p for p in cached.players}
    for i, p in enumerate(fresh.players):
        if not p.healthy and p.pid in by_id:
            fresh.players[i] = by_id[p.pid]
            fresh.stale.append(f"player {by_id[p.pid].name or p.pid}")
    return fresh


# --- FETCH ---------------------------------------------------------------------------

class FetchError(Exception):
    pass


async def fetch_json(session: aiohttp.ClientSession, url: str) -> Any:
    last: Optional[Exception] = None
    for attempt in range(FETCH_RETRY_COUNT + 1):
        try:
            async with session.get(url, headers={"User-Agent": USER_AGENT}) as resp:
                if resp.status != 200:
                    raise FetchError(f"HTTP {resp.status}")
                # ESPN labels some of these endpoints text/plain.
                return json.loads(await resp.text())
        except Exception as exc:                          # noqa: BLE001 — reported, not raised
            last = exc
            if attempt < FETCH_RETRY_COUNT:
                await asyncio.sleep(0.5)
    raise FetchError(str(last) or "fetch failed")


# --- PARSERS -------------------------------------------------------------------------
# Every parse_* below is pure: JSON in, models out, no network and no clock beyond the
# zone it is handed. tests/test_nba_parsers.py drives them from trimmed fixtures.

def _status_detail(status: dict, tipoff: Optional[datetime]) -> tuple[str, str]:
    """(state, human detail) for a competition's status block."""
    stype = (status or {}).get("type") or {}
    state = stype.get("state") or "pre"
    if state == "in":
        period = status.get("period") or 0
        shown = (status.get("displayClock") or "").strip()
        if period > 4:
            label = "OT" if period == 5 else f"{period - 4}OT"
        else:
            label = f"Q{period}" if period else "—"
        if stype.get("name") == "STATUS_HALFTIME":
            return state, "Half"
        return state, f"{label} {shown}".strip()
    if state == "post":
        return state, stype.get("shortDetail") or stype.get("description") or "Final"
    return state, clock_label(tipoff) if tipoff else "TBD"


def _score(competitor: dict) -> Optional[int]:
    """Scoreboard gives score as a string, team schedules as {value, displayValue}."""
    raw = competitor.get("score")
    if isinstance(raw, dict):
        raw = raw.get("displayValue") or raw.get("value")
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


def _scoreboard_tv(broadcasts: list) -> Optional[str]:
    """Scoreboard shape: [{market: 'away'|'home'|'national', names: [...]}]."""
    national, other = [], []
    for b in broadcasts or []:
        names = [n for n in (b.get("names") or []) if n and n != "NBA League Pass"]
        if not names:
            continue
        (national if b.get("market") == "national" else other).extend(names)
    picked = national or other
    return picked[0] if picked else None


def _schedule_tv(broadcasts: list) -> Optional[str]:
    """Team-schedule shape: [{market: {type: 'National'}, media: {shortName: 'ESPN'}}]."""
    national, other = [], []
    for b in broadcasts or []:
        name = ((b.get("media") or {}).get("shortName")
                or (b.get("names") or [None])[0])
        if not name or name == "NBA League Pass":
            continue
        if ((b.get("market") or {}).get("type") or "").lower() == "national":
            national.append(name)
        else:
            other.append(name)
    picked = national or other
    return picked[0] if picked else None


def _event_url(event: dict) -> Optional[str]:
    for link in event.get("links") or []:
        href = link.get("href") or ""
        if href.startswith("http"):
            return href
    return None


def _competition_game(event: dict, comp: dict, tz: ZoneInfo, tv_from: str) -> Optional[Game]:
    tipoff = parse_iso(event.get("date") or comp.get("date") or "", tz)
    if not tipoff:
        return None
    sides: dict[str, dict] = {}
    for c in comp.get("competitors") or []:
        side = c.get("homeAway")
        if side in ("home", "away"):
            sides[side] = c
    if "home" not in sides or "away" not in sides:
        return None

    def team_of(side: str) -> dict:
        return sides[side].get("team") or {}

    def record_of(side: str) -> Optional[str]:
        for r in sides[side].get("records") or []:
            # The first record is overall; 'total'/'overall' name it when present.
            if r.get("type") in (None, "total", "overall") or r.get("name") in ("overall", "All Splits"):
                return r.get("summary")
        recs = sides[side].get("records") or []
        return recs[0].get("summary") if recs else None

    status = comp.get("status") or event.get("status") or {}
    state, detail = _status_detail(status, tipoff)
    tv = _scoreboard_tv(comp.get("broadcasts") or []) if tv_from == "scoreboard" \
        else _schedule_tv(comp.get("broadcasts") or [])
    venue = (comp.get("venue") or {}).get("fullName")
    return Game(
        event_id=str(event.get("id") or comp.get("id") or ""),
        tipoff=tipoff,
        away=team_of("away").get("abbreviation") or "?",
        home=team_of("home").get("abbreviation") or "?",
        away_id=str(team_of("away").get("id") or ""),
        home_id=str(team_of("home").get("id") or ""),
        away_name=team_of("away").get("displayName") or "",
        home_name=team_of("home").get("displayName") or "",
        away_record=record_of("away"),
        home_record=record_of("home"),
        away_score=_score(sides["away"]),
        home_score=_score(sides["home"]),
        state=state,
        detail=detail,
        tv=tv,
        url=_event_url(event),
        venue=venue,
    )


def parse_scoreboard(data: dict, tz: ZoneInfo) -> tuple[list[Game], int]:
    """Today's slate. Returns (games, events_seen)."""
    events = (data or {}).get("events") or []
    games = []
    for e in events:
        for comp in e.get("competitions") or []:
            g = _competition_game(e, comp, tz, "scoreboard")
            if g:
                games.append(g)
            break
    games.sort(key=lambda g: (g.tipoff, g.away))
    return games, len(events)


def parse_team_schedule(data: dict, tz: ZoneInfo) -> tuple[list[Game], int]:
    """A team's full season. Returns (games, events_seen)."""
    events = (data or {}).get("events") or []
    games = []
    for e in events:
        for comp in e.get("competitions") or []:
            g = _competition_game(e, comp, tz, "schedule")
            if g:
                games.append(g)
            break
    games.sort(key=lambda g: g.tipoff)
    return games, len(events)


def parse_athlete(data: dict) -> dict[str, Any]:
    """Identity, team and injury for one athlete."""
    a = (data or {}).get("athlete") or {}
    team = a.get("team") or {}
    injury = None
    injuries = a.get("injuries") or []
    if injuries:
        first = injuries[0] or {}
        status = first.get("status") or (first.get("type") or {}).get("description")
        detail = ((first.get("details") or {}).get("type")
                  or (first.get("details") or {}).get("detail")
                  or first.get("shortComment"))
        injury = " — ".join(x for x in (status, detail) if x) or None
    else:
        status = (a.get("status") or {})
        # ESPN keeps type 'active' for healthy players; anything else is worth showing.
        if status.get("type") and status.get("type") != "active":
            injury = status.get("name") or status.get("abbreviation")
    return {
        "name": a.get("displayName") or "",
        "short_name": a.get("shortName") or "",
        "team_id": str(team.get("id") or ""),
        "team_abbr": team.get("abbreviation") or "",
        "team_name": team.get("displayName") or "",
        "jersey": a.get("jersey"),
        "position": (a.get("position") or {}).get("abbreviation"),
        "injury": injury,
    }


def parse_overview_averages(data: dict) -> dict[str, str]:
    """Current-season per-game averages, keyed by ESPN's own labels (PTS, REB, ...)."""
    stats = (data or {}).get("statistics") or {}
    labels = stats.get("labels") or []
    splits = stats.get("splits") or []
    if not labels or not splits:
        return {}
    # splits[0] is the current regular season; a later split named Career is the total.
    current = splits[0].get("stats") or []
    return {label: value for label, value in zip(labels, current)}


def parse_career_points(data: dict) -> Optional[int]:
    """Career regular-season points from the totals category's `totals` row."""
    for cat in (data or {}).get("categories") or []:
        if cat.get("name") != "totals":
            continue
        labels = cat.get("labels") or []
        totals = cat.get("totals") or []
        if "PTS" not in labels or len(totals) != len(labels):
            continue
        try:
            return int(float(totals[labels.index("PTS")]))
        except (TypeError, ValueError):
            return None
    return None


def parse_gamelog(data: dict, tz: ZoneInfo) -> tuple[list[PlayerGame], int]:
    """Flattens the month categories of the regular season. Newest first."""
    data = data or {}
    labels = data.get("labels") or []
    events = data.get("events") or {}
    season_types = data.get("seasonTypes") or []
    chosen = [s for s in season_types if "regular season" in (s.get("displayName") or "").lower()]
    if not chosen:
        chosen = season_types
    rows, seen = [], 0
    for st in chosen:
        for cat in st.get("categories") or []:
            for entry in cat.get("events") or []:
                seen += 1
                eid = str(entry.get("eventId") or "")
                meta = events.get(eid) or {}
                played = parse_iso(meta.get("gameDate") or "", tz)
                if not played:
                    continue
                stats = entry.get("stats") or []
                rows.append(PlayerGame(
                    event_id=eid,
                    played_on=played.date(),
                    opponent=(meta.get("opponent") or {}).get("abbreviation") or "?",
                    at_vs="@" if (meta.get("atVs") or "vs") == "@" else "vs",
                    result=meta.get("gameResult"),
                    score=meta.get("score"),
                    stats={label: value for label, value in zip(labels, stats)},
                    url=_event_url(meta),
                ))
    rows.sort(key=lambda r: r.played_on, reverse=True)
    return rows, seen


def parse_leaders(data: dict, abbreviations: list[str]) -> tuple[dict[str, list[Leader]], int]:
    """Top-N per requested category. Categories are matched by ESPN's stable `name`,
    because PTS is shared by the per-game and the season-total categories."""
    cats = ((data or {}).get("leaders") or {}).get("categories") or []
    by_name = {c.get("name"): c for c in cats}
    out: dict[str, list[Leader]] = {}
    for abbr in abbreviations:
        name = LEADER_CATEGORY_NAMES.get(abbr)
        cat = by_name.get(name) if name else None
        if cat is None:
            continue
        entries = []
        for i, l in enumerate(cat.get("leaders") or [], start=1):
            athlete = l.get("athlete") or {}
            team = l.get("team") or athlete.get("team") or {}
            entries.append(Leader(
                rank=i,
                athlete_id=str(athlete.get("id") or ""),
                name=athlete.get("displayName") or athlete.get("shortName") or "?",
                team=team.get("abbreviation") or "",
                value=str(l.get("displayValue") or l.get("value") or ""),
                short=athlete.get("shortName") or "",
            ))
        if entries:
            out[abbr] = entries
    return out, len(cats)


def _stat_value(stats: list[dict], *names: str) -> Optional[str]:
    for n in names:
        for s in stats or []:
            if s.get("name") == n or s.get("type") == n:
                return s.get("displayValue")
    return None


def parse_standings(data: dict) -> tuple[dict[str, list[TeamStanding]], int]:
    """{'East': [...], 'West': [...]} sorted by playoff seed. ESPN's entries arrive in
    an arbitrary order, so the seed is what puts them in sequence."""
    out: dict[str, list[TeamStanding]] = {}
    seen = 0
    for child in (data or {}).get("children") or []:
        conf = child.get("abbreviation") or child.get("shortName") or child.get("name") or "?"
        entries = ((child.get("standings") or {}).get("entries")) or []
        rows = []
        for e in entries:
            seen += 1
            team = e.get("team") or {}
            stats = e.get("stats") or []

            def num(*names: str) -> int:
                raw = _stat_value(stats, *names)
                try:
                    return int(float(raw))
                except (TypeError, ValueError):
                    return 0

            seed_raw = _stat_value(stats, "playoffSeed")
            try:
                seed = int(float(seed_raw))
            except (TypeError, ValueError):
                seed = None
            rows.append(TeamStanding(
                team_id=str(team.get("id") or ""),
                abbr=team.get("abbreviation") or "?",
                name=team.get("displayName") or team.get("name") or "",
                wins=num("wins"),
                losses=num("losses"),
                seed=seed,
                games_behind=_stat_value(stats, "gamesBehind") or "—",
                streak=_stat_value(stats, "streak") or "",
                last_ten=_stat_value(stats, "Last Ten Games", "lasttengames") or "",
            ))
        rows.sort(key=lambda t: (t.seed is None, t.seed or 0, -t.wins))
        if rows:
            out[conf] = rows
    return out, seen


# --- PER-SOURCE PROCESSING -----------------------------------------------------------

async def process_player(session: aiohttp.ClientSession, pid: Any, tz: ZoneInfo) -> Player:
    """Four calls per player: identity, averages, game log, career totals."""
    player = Player(pid=str(pid))
    try:
        athlete = await fetch_json(session, f"{WEB_API}/athletes/{pid}")
    except FetchError as exc:
        player.fetch_error = f"athlete: {exc}"
        return player
    try:
        for k, v in parse_athlete(athlete).items():
            setattr(player, k, v)
    except Exception as exc:                              # noqa: BLE001
        player.fetch_error = f"athlete parse: {type(exc).__name__}"
        return player
    if not player.name:
        player.fetch_error = "athlete has no name"
        return player

    overview, gamelog, stats = await asyncio.gather(
        fetch_json(session, f"{WEB_API}/athletes/{pid}/overview"),
        fetch_json(session, f"{WEB_API}/athletes/{pid}/gamelog"),
        fetch_json(session, f"{WEB_API}/athletes/{pid}/stats"),
        return_exceptions=True,
    )
    if not isinstance(overview, Exception):
        try:
            player.averages = parse_overview_averages(overview)
        except Exception:                                 # noqa: BLE001
            pass
    if not isinstance(gamelog, Exception):
        try:
            player.log, _ = parse_gamelog(gamelog, tz)
        except Exception:                                 # noqa: BLE001
            pass
    if not isinstance(stats, Exception):
        try:
            player.career_points = parse_career_points(stats)
        except Exception:                                 # noqa: BLE001
            pass
    return player


async def process_team_schedule(session: aiohttp.ClientSession, team_id: str,
                                tz: ZoneInfo) -> tuple[str, list[Game]]:
    data = await fetch_json(session, f"{SITE_API}/teams/{team_id}/schedule")
    games, _ = parse_team_schedule(data, tz)
    return team_id, games


def make_session() -> aiohttp.ClientSession:
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    connector = aiohttp.TCPConnector(limit_per_host=FETCH_LIMIT_PER_HOST, ssl=ctx)
    return aiohttp.ClientSession(connector=connector,
                                 timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS))


async def gather_all(cfg: dict, tz: ZoneInfo) -> Bundle:
    """One pass over every source. A failure is recorded per section, never raised."""
    bundle = Bundle()
    leader_cats = list(dict.fromkeys(
        (cfg.get("leader_categories") or []) + (cfg.get("more_leader_categories") or [])))
    async with make_session() as session:
        scoreboard, standings, leaders, players = await asyncio.gather(
            fetch_json(session, SCOREBOARD_URL),
            fetch_json(session, STANDINGS_URL),
            fetch_json(session, LEADERS_URL),
            asyncio.gather(*(process_player(session, p, tz) for p in cfg.get("players") or []),
                           return_exceptions=True),
            return_exceptions=True,
        )

        if isinstance(scoreboard, Exception):
            bundle.errors["games"] = str(scoreboard) or type(scoreboard).__name__
        else:
            try:
                bundle.games, _ = parse_scoreboard(scoreboard, tz)
            except Exception as exc:                      # noqa: BLE001
                bundle.errors["games"] = f"parse: {type(exc).__name__}"

        if isinstance(standings, Exception):
            bundle.errors["standings"] = str(standings) or type(standings).__name__
        else:
            try:
                bundle.standings, _ = parse_standings(standings)
                if not bundle.standings:
                    bundle.errors["standings"] = "no conferences parsed"
            except Exception as exc:                      # noqa: BLE001
                bundle.errors["standings"] = f"parse: {type(exc).__name__}"

        if isinstance(leaders, Exception):
            bundle.errors["leaders"] = str(leaders) or type(leaders).__name__
        else:
            try:
                bundle.leaders, _ = parse_leaders(leaders, leader_cats)
                if not bundle.leaders:
                    bundle.errors["leaders"] = "no categories parsed"
            except Exception as exc:                      # noqa: BLE001
                bundle.errors["leaders"] = f"parse: {type(exc).__name__}"

        ids = [str(p) for p in cfg.get("players") or []]
        for pid, result in zip(ids, players if not isinstance(players, Exception) else []):
            bundle.players.append(result if isinstance(result, Player)
                                  else Player(pid=pid, fetch_error=f"crash: {type(result).__name__}"))
        if isinstance(players, Exception):
            bundle.errors["players"] = str(players) or type(players).__name__
            bundle.players = [Player(pid=p, fetch_error="fetch failed") for p in ids]

        team_ids = tracked_team_ids(bundle.players, cfg)
        if team_ids:
            results = await asyncio.gather(
                *(process_team_schedule(session, t, tz) for t in sorted(team_ids)),
                return_exceptions=True)
            failed = []
            for t, r in zip(sorted(team_ids), results):
                if isinstance(r, Exception):
                    failed.append(t)
                else:
                    bundle.schedules[r[0]] = r[1]
            if failed and not bundle.schedules:
                bundle.errors["schedules"] = f"{len(failed)} team schedule(s) failed"
    return bundle


def tracked_team_ids(players: list[Player], cfg: dict) -> set[str]:
    ids = {p.team_id for p in players if p.team_id}
    ids |= {str(t) for t in (cfg.get("extra_teams") or []) if str(t).strip()}
    return ids


# --- DERIVED VIEWS -------------------------------------------------------------------

def upcoming_games(schedules: dict[str, list[Game]], count: int) -> list[Game]:
    """Next games across every tracked team, de-duplicated and nearest first.
    A game between two tracked teams appears on both schedules."""
    t = today()
    seen: dict[str, Game] = {}
    for games in schedules.values():
        for g in games:
            if g.state == "post" or g.tipoff.date() < t:
                continue
            seen.setdefault(g.event_id, g)
    return sorted(seen.values(), key=lambda g: g.tipoff)[:count]


def head_to_head(schedules: dict[str, list[Game]], team_ids: set[str]) -> Optional[Game]:
    """The next meeting of two tracked teams, when there is one."""
    if len(team_ids) < 2:
        return None
    t = today()
    best: Optional[Game] = None
    for games in schedules.values():
        for g in games:
            if g.tipoff.date() < t or g.state == "post":
                continue
            if g.home_id in team_ids and g.away_id in team_ids:
                if best is None or g.tipoff < best.tipoff:
                    best = g
    return best


def season_highs(log: list[PlayerGame]) -> dict[str, float]:
    highs: dict[str, float] = {}
    for label in ("PTS", "REB", "AST"):
        values = [g.number(label) for g in log]
        values = [v for v in values if v is not None]
        if values:
            highs[label] = max(values)
    return highs


def milestone(points: Optional[int], step: int) -> Optional[tuple[int, int, int]]:
    """(career, next milestone, points to go). None once step is unusable."""
    if not points or step <= 0:
        return None
    nxt = ((points // step) + 1) * step
    return points, nxt, nxt - points


def standing_for(standings: dict[str, list[TeamStanding]], team_id: str
                 ) -> Optional[tuple[str, TeamStanding]]:
    for conf, rows in standings.items():
        for t in rows:
            if t.team_id == team_id:
                return conf, t
    return None


def season_state(bundle: Bundle) -> tuple[str, Optional[date]]:
    """('in'|'pre', first upcoming tip). 'pre' only when nothing is close."""
    if bundle.games:
        return "in", today()
    nxt = upcoming_games(bundle.schedules, 1)
    if not nxt:
        return "pre", None
    first = nxt[0].tipoff.date()
    if (first - today()).days > OFF_SEASON_WINDOW_DAYS:
        return "pre", first
    return "in", first


# --- RENDERING -----------------------------------------------------------------------

def scrub(text: Any) -> str:
    """SwiftBar splits a line on the last '|', so no field may contain one. Leading
    spaces are kept: they are what lines the Menlo columns up under their headers."""
    return str(text if text is not None else "").replace("|", "/").replace("\n", " ").rstrip()


# Each table below is laid out by one function used for both its header and its rows, so
# the two cannot drift apart.

def slate_row(mark: str, away: str, a_rec: str, sep: str, home: str, h_rec: str,
              status: str, tv: str) -> str:
    return (f"{mark} {away:<4} {a_rec:>5} {sep} {home:<4} {h_rec:>5}  "
            f"{status:<9}  {tv}").rstrip()


def log_row(when: str, opp: str, result: str, minutes: str, pts: str, reb: str,
            ast: str, fg: str, three: str) -> str:
    return (f"{when:<6} {opp:<6} {result:<3} {minutes:>3} {pts:>4} {reb:>4} {ast:>4}  "
            f"{fg:<7} {three}").rstrip()


def standings_row(mark: str, seed: str, team: str, record: str, gb: str, streak: str,
                  last_ten: str) -> str:
    return (f"{mark}{seed:>3} {team:<5} {record:>6} {gb:>5}  "
            f"{streak:<4} {last_ten}").rstrip()


def leader_row(mark: str, rank: str, name: str, team: str, value: str) -> str:
    who = f"{name} {team}".strip()
    return f"{mark} {rank:>3} {who:<28} {value:>6}".rstrip()


def indent(depth: int) -> str:
    """SwiftBar nests one submenu level per '--', so a level is two dashes, not one."""
    return "--" * depth


def line(text: str, depth: int = 0, *params: str) -> None:
    opts = " ".join(p for p in params if p)
    print(f"{indent(depth)}{scrub(text)}" + (f" | {opts}" if opts else ""))


def header(text: str, depth: int = 0) -> None:
    line(text, depth, HEADER_FONT, LABEL_COLOR)


def separator(depth: int = 0) -> None:
    print(f"{indent(depth)}---")


def calendar_line(g: Game, depth: int, label: str, style: str = "") -> str:
    """The ⌥ alternate of a game row: hands Fantastical a natural-language sentence.
    SwiftBar swaps it in for the line printed just before it while Option is held, so it
    must directly follow its game row at the same depth."""
    title = f"{g.away_name or g.away} at {g.home_name or g.home}"
    where = g.venue or (g.home_name or g.home)
    appt = f'{g.tipoff:%Y/%m/%d} at {g.tipoff:%H%M} "{title}" at {where}'
    return (f"{indent(depth)}Add to Calendar · {scrub(label)} | "
            f"href=x-fantastical3://parse?add=1&sentence={quote(appt)} "
            f"sfimage=calendar.badge.plus alternate=true terminal=false{style}")


def game_row(g: Game, tracked: set[str]) -> str:
    """One Menlo row of the slate. Before tip the two numeric columns are records;
    once the ball is up they become the score."""
    mark = TRACKED_MARK if g.involves(tracked) else " "
    if g.state == "pre":
        left, right = g.away_record or "", g.home_record or ""
    else:
        left = "—" if g.away_score is None else str(g.away_score)
        right = "—" if g.home_score is None else str(g.home_score)
    return slate_row(mark, g.away, left, "@", g.home, right, g.detail, g.tv or "")


def print_slate(games: list[Game], tracked: set[str], stale: bool) -> None:
    live = [g for g in games if g.state == "in"]
    label = "LIVE" if live else "TODAY"
    count = f"{len(games)} game{'s' if len(games) != 1 else ''}"
    header(f"{label} · {today():%a %b %d} · {count}{' · cached' if stale else ''}")
    if not games:
        line("No games scheduled", 0, DIMMED, LABEL_COLOR)
        return
    scored = any(g.state != "pre" for g in games)
    line(slate_row(" ", "AWAY", "SCORE" if scored else "REC", " ", "HOME",
                   "SCORE" if scored else "REC", "STATUS", "TV"),
         0, TABLE_BOLD, LABEL_COLOR)
    for g in games:
        style = ACCENT_ORANGE if g.state == "in" else (DIMMED if g.state == "post" else "")
        if g.involves(tracked) and g.state != "in":
            style = ACCENT_BLUE
        text = game_row(g, tracked)
        line(text, 0, TABLE, style, f"href={g.url or NBA_HOME}")
        if g.state == "pre":
            print(calendar_line(g, 0, f"{g.matchup} {g.detail}", f" {TABLE}"))


def print_player(p: Player, standings: dict[str, list[TeamStanding]], cfg: dict) -> None:
    if not p.healthy:
        line(f"{p.name or f'Player {p.pid}'} — unavailable", 0, DIMMED, ACCENT_RED)
        return
    avg = p.averages
    summary = "  ".join(
        f"{avg[k]} {k.lower()}" for k in ("PTS", "REB", "AST", "MIN") if avg.get(k))
    tooltip_bits = [f"{k} {avg[k]}" for k in ("FG%", "3P%", "FT%", "TO") if avg.get(k)]
    tooltip = f'tooltip="{scrub(" · ".join(tooltip_bits))}"' if tooltip_bits else ""
    style = ACCENT_YELLOW if p.injury else ""
    pos = f" {p.position}" if p.position else ""
    line(f"{TRACKED_MARK} {p.label}{pos}   {summary}", 0, style, tooltip,
         f"href=https://www.espn.com/nba/player/_/id/{p.pid}")

    recent = p.log[: max(0, int(cfg.get("max_past_games") or 5))]
    if recent:
        line(f"Last {len(recent)} game{'s' if len(recent) != 1 else ''}", 1, DIMMED, LABEL_COLOR)
        line(log_row("DATE", "OPP", "W/L", "MIN", "PTS", "REB", "AST", "FG", "3PT"),
             1, TABLE_BOLD, LABEL_COLOR)
        for g in recent:
            row = log_row(f"{g.played_on:%b %d}", f"{g.at_vs} {g.opponent}",
                          g.result or "—", g.stat("MIN"), g.stat("PTS"), g.stat("REB"),
                          g.stat("AST"), g.stat("FG"), g.stat("3PT"))
            res_style = ACCENT_GREEN if g.result == "W" else ACCENT_RED if g.result == "L" else DIMMED
            line(row, 1, TABLE, res_style, f"href={g.url or NBA_HOME}")
    else:
        line("No games logged yet this season", 1, DIMMED, LABEL_COLOR)

    highs = season_highs(p.log)
    if highs:
        shown = " · ".join(f"{int(v) if v == int(v) else v} {k.lower()}"
                           for k, v in highs.items())
        line(f"Season highs: {shown}", 1, DIMMED, LABEL_COLOR)

    mile = milestone(p.career_points, int(cfg.get("milestone_step") or 1000))
    if mile:
        career, nxt, togo = mile
        line(f"Milestone: {career:,} career pts · {togo:,} to {nxt:,}", 1, ACCENT_ORANGE)

    if p.team_id:
        found = standing_for(standings, p.team_id)
        if found:
            conf, t = found
            seed = f"#{t.seed} {conf}" if t.seed else conf
            line(f"{t.abbr} {t.record} · {seed} · {t.streak or '—'} · L10 {t.last_ten or '—'}",
                 1, DIMMED, LABEL_COLOR)

    line(p.injury if p.injury else "Active", 1, ACCENT_YELLOW if p.injury else DIMMED,
         "" if p.injury else LABEL_COLOR)


def print_next_up(bundle: Bundle, tracked: set[str], cfg: dict) -> None:
    games = upcoming_games(bundle.schedules, int(cfg.get("next_up_count") or 3))
    if not games:
        return
    header("NEXT UP")
    for g in games:
        when = f"{relative_day(g.tipoff.date())} {clock_label(g.tipoff)}".ljust(16)
        tv = f"  {g.tv}" if g.tv else ""
        text = f"{when}{g.matchup}{tv}"
        style = ACCENT_ORANGE if g.tipoff.date() == today() else ""
        line(text, 0, TABLE, style, f"href={g.url or NBA_HOME}")
        print(calendar_line(g, 0, f"{relative_day(g.tipoff.date())} {g.matchup}", f" {TABLE}"))
    h2h = head_to_head(bundle.schedules, tracked)
    if h2h:
        line(f"Head to head · {relative_day(h2h.tipoff.date())} "
             f"{clock_label(h2h.tipoff)}  {h2h.matchup}", 0, TABLE, ACCENT_BLUE,
             f"href={h2h.url or NBA_HOME}")


def print_leaders(bundle: Bundle, cfg: dict) -> None:
    if not bundle.leaders:
        return
    front = [c for c in (cfg.get("leader_categories") or []) if c in bundle.leaders]
    extra = [c for c in (cfg.get("more_leader_categories") or []) if c in bundle.leaders]
    tracked_ids = {p.pid for p in bundle.players if p.healthy}
    header("LEAGUE LEADERS" + (" · cached" if "leaders" in bundle.stale else ""))
    if front:
        summary = "   ".join(
            f"{c} {bundle.leaders[c][0].short or bundle.leaders[c][0].name} "
            f"{bundle.leaders[c][0].value}" for c in front)
        line(summary, 0, TABLE, LABEL_COLOR)
    for cat in front + extra:
        entries = bundle.leaders[cat]
        line(f"{cat} · top {len(entries)}", 0, DIMMED)
        for e in entries:
            mark = TRACKED_MARK if e.athlete_id in tracked_ids else " "
            row = leader_row(mark, f"{e.rank}.", e.name, e.team, e.value)
            line(row, 1, TABLE, ACCENT_BLUE if e.athlete_id in tracked_ids else "",
                 f"href=https://www.espn.com/nba/player/_/id/{e.athlete_id}")
        ranked = {e.athlete_id for e in entries}
        for p in bundle.players:
            if p.healthy and p.pid not in ranked:
                value = p.averages.get(cat)
                if value:
                    line(leader_row(TRACKED_MARK, "—", p.name, p.team_abbr, value)
                         + f"  outside top {len(entries)}", 1, TABLE, DIMMED, LABEL_COLOR)


def print_standings(bundle: Bundle, tracked: set[str]) -> None:
    if not bundle.standings:
        return
    header("STANDINGS" + (" · cached" if "standings" in bundle.stale else ""))
    for team_id in sorted(tracked):
        found = standing_for(bundle.standings, team_id)
        if not found:
            continue
        conf, t = found
        line(standings_row(TRACKED_MARK, str(t.seed or "—"), t.abbr, t.record,
                           str(t.games_behind), t.streak or "—", t.last_ten or "—")
             + f"   {conf}", 0, TABLE, ACCENT_BLUE)
    for conf, rows in bundle.standings.items():
        line(conf, 0, DIMMED)
        line(standings_row(" ", "#", "TEAM", "W-L", "GB", "STRK", "L10"),
             1, TABLE_BOLD, LABEL_COLOR)
        for t in rows:
            mark = TRACKED_MARK if t.team_id in tracked else " "
            row = standings_row(mark, str(t.seed or "—"), t.abbr, t.record,
                                str(t.games_behind), t.streak or "—", t.last_ten or "—")
            # The play-in line: seeds 11 and below are dimmed, 7-10 stay readable.
            style = ACCENT_BLUE if t.team_id in tracked else (DIMMED if (t.seed or 99) > 10 else "")
            line(row, 1, TABLE, style,
                 f"href=https://www.espn.com/nba/team/_/name/{t.abbr.lower()}")


def print_title(bundle: Bundle, cfg: dict, state: str, first: Optional[date]) -> None:
    symbol = cfg.get("sf_symbol") or DEFAULT_CONFIG["sf_symbol"]
    if state == "pre":
        if first:
            days = (first - today()).days
            print(f"{days}d | sfimage={symbol} sfcolor=#8E8E93")
        else:
            print(f"| sfimage={symbol} sfcolor=#8E8E93")
        return
    live = [g for g in bundle.games if g.state == "in"]
    if live:
        print(f"{len(live)} | sfimage={symbol} sfcolor=#FF9500")
    elif bundle.games:
        print(f"{len(bundle.games)} | sfimage={symbol}")
    else:
        print(f"| sfimage={symbol}")


def print_openers(bundle: Bundle, first: Optional[date]) -> None:
    header("OFF-SEASON")
    if not first:
        line("No scheduled games found", 0, DIMMED, LABEL_COLOR)
        return
    days = (first - today()).days
    line(f"First tip {first:%b %d} · {days} day{'s' if days != 1 else ''} away", 0, ACCENT_ORANGE)
    for g in upcoming_games(bundle.schedules, 3):
        line(f"{g.tipoff:%b %d}  {g.matchup}  {clock_label(g.tipoff)}", 0, TABLE,
             f"href={g.url or NBA_HOME}")
        print(calendar_line(g, 0, f"{g.tipoff:%b %d} {g.matchup}", f" {TABLE}"))


def print_footer(bundle: Bundle) -> None:
    separator()
    if bundle.errors:
        for section, why in bundle.errors.items():
            shown = "showing cached" if section in bundle.stale else "no data"
            line(f"{section}: {why} ({shown})", 0, "size=11", ACCENT_RED)
    stale_players = [s for s in bundle.stale if s.startswith("player ")]
    for s in stale_players:
        line(f"{s} is cached", 0, "size=11", LABEL_COLOR)
    line("Hold ⌥ on a game to add it to your calendar", 0, "size=11", LABEL_COLOR)
    line("Edit teams & players", 0, "size=11", LABEL_COLOR,
         f"href=file://{CONFIG_PATH.parent}")
    line("Refresh", 0, "refresh=true")


# --- MAIN ----------------------------------------------------------------------------

async def run_menu() -> None:
    cfg = load_config()
    tz = local_zone(cfg)
    cached, _ = load_cache(tz)
    bundle = await gather_all(cfg, tz)
    save_cache(bundle, cached)
    bundle = apply_cache(bundle, cached)

    tracked = tracked_team_ids(bundle.players, cfg)
    state, first = season_state(bundle)

    print_title(bundle, cfg, state, first)
    separator()
    if state == "in":
        print_slate(bundle.games, tracked, "games" in bundle.stale)
    else:
        print_openers(bundle, first)
    separator()
    header("MY GUYS")
    for p in bundle.players:
        print_player(p, bundle.standings, cfg)
    print_next_up(bundle, tracked, cfg)
    separator()
    print_leaders(bundle, cfg)
    separator()
    print_standings(bundle, tracked)
    print_footer(bundle)


async def run_check() -> int:
    """One line per source with what was seen and parsed; exit 1 on any failure."""
    cfg = load_config()
    tz = local_zone(cfg)
    failures = 0
    leader_cats = list(dict.fromkeys(
        (cfg.get("leader_categories") or []) + (cfg.get("more_leader_categories") or [])))

    def report(name: str, ok: bool, detail: str) -> None:
        nonlocal failures
        if not ok:
            failures += 1
        print(f"{'ok  ' if ok else 'FAIL'}  {name.ljust(22)} {detail}")

    async with make_session() as session:
        try:
            data = await fetch_json(session, SCOREBOARD_URL)
            games, seen = parse_scoreboard(data, tz)
            live = sum(1 for g in games if g.state == "in")
            report("scoreboard", seen == 0 or bool(games),
                   f"{seen} events seen, {len(games)} parsed, {live} live")
        except Exception as exc:                          # noqa: BLE001
            report("scoreboard", False, str(exc) or type(exc).__name__)

        try:
            data = await fetch_json(session, STANDINGS_URL)
            table, seen = parse_standings(data)
            report("standings", bool(table),
                   f"{seen} entries seen, {sum(len(v) for v in table.values())} parsed in "
                   f"{len(table)} conference(s): {', '.join(table) or 'none'}")
        except Exception as exc:                          # noqa: BLE001
            report("standings", False, str(exc) or type(exc).__name__)

        try:
            data = await fetch_json(session, LEADERS_URL)
            table, seen = parse_leaders(data, leader_cats)
            missing = [c for c in leader_cats if c not in table]
            report("leaders", not missing,
                   f"{seen} categories seen, {len(table)} wanted and parsed"
                   + (f"; missing {', '.join(missing)}" if missing else ""))
        except Exception as exc:                          # noqa: BLE001
            report("leaders", False, str(exc) or type(exc).__name__)

        players = await asyncio.gather(
            *(process_player(session, p, tz) for p in cfg.get("players") or []),
            return_exceptions=True)
        for pid, p in zip(cfg.get("players") or [], players):
            if isinstance(p, Exception):
                report(f"player {pid}", False, f"crash: {type(p).__name__}")
                continue
            if not p.healthy:
                report(f"player {pid}", False, p.fetch_error or "unknown")
                continue
            report(f"player {pid}", bool(p.averages),
                   f"{p.name} · {p.team_abbr or '?'} · {len(p.averages)} averages, "
                   f"{len(p.log)} logged games, career pts "
                   f"{p.career_points if p.career_points is not None else '—'}")

        good = [p for p in players if isinstance(p, Player) and p.healthy]
        team_ids = tracked_team_ids(good, cfg)
        if not team_ids:
            report("team schedules", False, "no tracked teams resolved")
        for team_id in sorted(team_ids):
            try:
                _, games = await process_team_schedule(session, team_id, tz)
                ahead = len([g for g in games if g.tipoff.date() >= today()])
                report(f"schedule {team_id}", bool(games),
                       f"{len(games)} games parsed, {ahead} upcoming")
            except Exception as exc:                      # noqa: BLE001
                report(f"schedule {team_id}", False, str(exc) or type(exc).__name__)

    print(f"\n{'all sources healthy' if not failures else f'{failures} source(s) failing'}")
    return 1 if failures else 0


def main() -> None:
    if "--check" in sys.argv[1:]:
        sys.exit(asyncio.run(run_check()))
    try:
        asyncio.run(run_menu())
    except Exception as exc:                              # noqa: BLE001 — must still render
        cfg = load_config()
        print(f"! | sfimage={cfg.get('sf_symbol') or 'figure.basketball'} sfcolor=#FF3B30")
        separator()
        line(f"NBA plugin failed: {type(exc).__name__}: {exc}", 0, ACCENT_RED)
        line("Refresh", 0, "refresh=true")


if __name__ == "__main__":
    main()
