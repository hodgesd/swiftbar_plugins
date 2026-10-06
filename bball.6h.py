#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "aiohttp>=3.8.0",
#     "beautifulsoup4>=4.9.0",
#     "truststore>=0.9.0",
# ]
# ///

# <swiftbar.title>Local Basketball</swiftbar.title>
# <swiftbar.version>v4.3</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Home games, records and rankings for local high school, JUCO, D1, D2, D3 and NAIA basketball. Teams configurable via ~/.config/swiftbar-plugins/bball.json</swiftbar.desc>
# <swiftbar.dependencies>uv</swiftbar.dependencies>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

"""
Local Basketball — SwiftBar plugin

Sources
  High schools      MaxPreps schedule + rankings pages (the __NEXT_DATA__ JSON carries the
                    schedule with home/away/neutral flags; the HTML table is the fallback)
  Division I        ESPN site API (schedule, record, AP rank) + NCAA NET rankings page
  SWIC              swic.edu schedule table; record from the NJCAA Region 24 standings
  Vincennes         govutrailblazers.com schedule; record from Region 24 standings
  D2 / D3 / NAIA    each school's own Sidearm athletics site (schedule page rows and the
                    "Overall" record in its header); ESPN does not carry these teams
  NJCAA DI poll     NJCAA public GraphQL API (publishedPolls)

Config (optional)   ~/.config/swiftbar-plugins/bball.json — every key has a default; see
                    DEFAULT_CONFIG. Edit it to add or drop teams without a redeploy.
Cache               ~/.cache/swiftbar-plugins/bball_cache.json — last good data per team,
                    shown (marked stale) when a source fails.

CLI
  ./bball.6h.py            SwiftBar output
  ./bball.6h.py --check    one line per source with rows seen / games parsed; exit 1 on
                           any failure. Run this before the season starts.
Env
  BBALL_FAKE_TODAY=YYYY-MM-DD   pretend it is another day (layout testing)
  BBALL_NO_CACHE=1              ignore the cache
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import ssl
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urljoin
from zoneinfo import ZoneInfo

import aiohttp
import truststore
from bs4 import BeautifulSoup

# --- CONFIGURATION -------------------------------------------------------------------

CONFIG_PATH = Path(os.path.expanduser("~/.config/swiftbar-plugins/bball.json"))
_XDG_CACHE = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
CACHE_FILE = Path(_XDG_CACHE) / "swiftbar-plugins" / "bball_cache.json"
CACHE_MAX_AGE_DAYS = 14

DEFAULT_CONFIG: dict[str, Any] = {
    "high_schools": [
        {"url": "https://www.maxpreps.com/il/belleville/belleville-east-lancers/basketball/schedule/"},
        {"url": "https://www.maxpreps.com/il/ofallon/ofallon-panthers/basketball/schedule/"},
        {"url": "https://www.maxpreps.com/il/mascoutah/mascoutah-indians/basketball/schedule/"},
        {"url": "https://www.maxpreps.com/il/belleville/belleville-west-maroons/basketball/schedule/"},
        {"url": "https://www.maxpreps.com/il/east-st-louis/east-st-louis-flyers/basketball/schedule/"},
        {"url": "https://www.maxpreps.com/mo/st-louis/vashon-wolverines/basketball/schedule/"},
    ],
    "colleges": [
        {"espn_id": 139, "net_name": "Saint Louis"},
        {"espn_id": 2565, "net_name": "SIUE"},
        {"espn_id": 356, "net_name": "Illinois"},
        {"espn_id": 2815, "net_name": "Lindenwood"},
    ],
    "community_colleges": {"swic": True, "vincennes": True},
    # Any school whose athletics site runs on Sidearm (the schedule lives at
    # <base>/sports/mens-basketball/schedule/<season>). "level" is the tag shown in the menu.
    "small_colleges": [
        {"name": "McKendree", "level": "D2", "base": "https://mckbearcats.com"},
        {"name": "UMSL", "level": "D2", "base": "https://umsltritons.com"},
        {"name": "Maryville", "level": "D2", "base": "https://maryvillesaints.com"},
        {"name": "WashU", "level": "D3", "base": "https://washubears.com"},
        {"name": "Harris-Stowe", "level": "NAIA", "base": "https://hornetsathletics.com"},
        {"name": "Missouri Baptist", "level": "NAIA", "base": "https://mbuspartans.com"},
    ],
    # A neutral-site game is listed when its venue city (D1) or tournament name (high
    # school) contains one of these.
    "local_venue_cities": [
        "St. Louis", "Saint Louis", "St Louis", "Belleville", "O'Fallon", "Edwardsville",
        "Collinsville", "Mascoutah", "East St. Louis", "Alton", "Granite City", "St. Charles",
        "Lebanon, IL",
    ],
    "max_past_games": 2,
    "next_up_count": 5,
}

FETCH_TIMEOUT_SECONDS = 15
FETCH_RETRY_COUNT = 1
FETCH_LIMIT_PER_HOST = 3
OFF_SEASON_WINDOW_DAYS = 10
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

SWIC_URL = "https://www.swic.edu/students/services/student-life/athletics/mens-basketball/"
VINCENNES_SCHEDULE_URL = "https://govutrailblazers.com/sports/mbkb/{season}/schedule"
SIDEARM_SCHEDULE_PATH = "/sports/mens-basketball/schedule/{season}"
REGION24_STANDINGS_URL = "https://www.njcaaregion24.com/sports/mbkb/{season}/standings"
NCAA_NET_URL = "https://www.ncaa.com/rankings/basketball-men/d1/ncaa-mens-basketball-net-rankings"
ESPN_API = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"
NJCAA_GRAPHQL_URL = "https://public-graphql-api.ardorsportshub.com/public/graphql"
NJCAA_TENANT_ID = "404933495157163011"
NJCAA_SPORT_ID = "2"   # basketball
NJCAA_GENDER_ID = "1"  # men
NJCAA_RANKINGS_PAGE = "https://www.njcaa.org/sports/rankings?sport=mbkb"

REGION24_NAMES = {"swic": "Southwestern Illinois College", "vincennes": "Vincennes University"}
# Sites publish venue-local times; everything is converted to this Mac's time zone.
EASTERN = ZoneInfo("America/New_York")
CENTRAL = ZoneInfo("America/Chicago")          # MaxPreps (IL/MO schools) and SWIC
VINCENNES_TZ = ZoneInfo("America/Indiana/Vincennes")
# Sidearm schools tag a tipoff that is not in their own zone: "12 p.m. ET", "2 p.m. MDT".
TZ_TAGS = {"E": EASTERN, "C": CENTRAL, "M": ZoneInfo("America/Denver"), "P": ZoneInfo("America/Los_Angeles")}

COLOR_MUTED = "#888888"
COLOR_TODAY = "#FFA500"
COLOR_OFFSEASON = "#8E8E93"


# --- MODELS --------------------------------------------------------------------------

@dataclass
class Game:
    date: datetime                     # local calendar date (time 00:00)
    home_away: str                     # "Home" | "Away" | "Neutral"
    opponent: str
    tipoff_time: Optional[datetime] = None   # local date+time, None when TBD
    game_url: Optional[str] = None
    result: Optional[str] = None       # "W" | "L"
    score: Optional[str] = None        # "65-58", our score first
    venue: Optional[str] = None        # neutral-site venue or tournament name
    note: Optional[str] = None         # "Scrimmage", "Exhibition", ...
    local: bool = True                 # False for a neutral game outside the local area


@dataclass
class School:
    key: str
    url: str
    name: Optional[str] = None
    short: Optional[str] = None            # "Belleville East", "Illinois"
    level: Optional[str] = None            # "D2" | "D3" | "NAIA": shown where a rank would be
    record: Optional[str] = None
    ranking: Optional[int] = None          # state rank (HS), AP rank (D1), poll rank (JUCO)
    net_rank: Optional[int] = None
    rankings_tooltip: Optional[str] = None
    streak: Optional[int] = None
    streak_type: Optional[str] = None
    schedule: list[Game] = field(default_factory=list)
    last_season_record: Optional[str] = None
    fetch_error: Optional[str] = None      # the source failed; cached data is shown instead
    warning: Optional[str] = None          # something degraded but the data is usable
    rows_seen: int = 0
    rows_parsed: int = 0
    last_successful_update: Optional[datetime] = None
    stale_since: Optional[datetime] = None

    @property
    def healthy(self) -> bool:
        return not self.fetch_error and self.rows_parsed > 0

    @property
    def problem(self) -> Optional[str]:
        if self.fetch_error:
            return self.fetch_error
        if self.rows_seen == 0:
            return "no schedule rows found"
        if self.rows_parsed == 0:
            return f"0 of {self.rows_seen} rows parsed (layout changed?)"
        return None


# --- TIME HELPERS --------------------------------------------------------------------

def today() -> date:
    fake = os.environ.get("BBALL_FAKE_TODAY")
    if fake:
        return date.fromisoformat(fake)
    return date.today()


def now_local() -> datetime:
    fake = os.environ.get("BBALL_FAKE_TODAY")
    if fake:
        return datetime.combine(date.fromisoformat(fake), datetime.now().time())
    return datetime.now()


def season_start_year(d: Optional[date] = None) -> int:
    """Basketball seasons roll over in October: Oct 2026 - Sep 2027 is the 2026-27 season."""
    d = d or today()
    return d.year if d.month >= 10 else d.year - 1


def season_slug(start_year: Optional[int] = None) -> str:
    y = season_start_year() if start_year is None else start_year
    return f"{y}-{str(y + 1)[-2:]}"


def year_for_month(month: int, start_year: Optional[int] = None) -> int:
    """Calendar year of a month inside the season (Jul-Dec -> start year, Jan-Jun -> next)."""
    y = season_start_year() if start_year is None else start_year
    return y if month >= 7 else y + 1


def to_local(dt: datetime) -> datetime:
    """Aware datetime -> naive datetime in this Mac's zone (DST-aware for that instant)."""
    return dt.astimezone().replace(tzinfo=None)


def parse_tipoff_time(time_str: str) -> Optional[datetime]:
    """'7:00PM', '7:00 pm', '3:30' (assumed PM) -> time-only datetime; None for TBA."""
    cleaned = time_str.strip().upper()
    for fmt in ("%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    m = re.match(r"^(\d{1,2}):(\d{2})$", cleaned)
    if m:
        return datetime.strptime(f"{m.group(1)}:{m.group(2)} PM", "%I:%M %p")
    return None


def at_time(day: date, t: Optional[datetime], source_tz: ZoneInfo = CENTRAL) -> Optional[datetime]:
    """Combine a date with a venue-local time and convert to this Mac's time zone."""
    return to_local(datetime.combine(day, t.time(), tzinfo=source_tz)) if t else None


def format_relative_date(game_date: datetime) -> str:
    delta = (game_date.date() - today()).days
    if delta == 0:
        return "TODAY"
    if delta == 1:
        return "TOMORROW"
    if 2 <= delta <= 6:
        return game_date.strftime("%a").upper()
    return game_date.strftime("%b %d")


def is_local_venue(text: Optional[str], local_cities: list[str]) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(c.lower() in t for c in local_cities)


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
    d["date"] = g.date.isoformat()
    d["tipoff_time"] = g.tipoff_time.isoformat() if g.tipoff_time else None
    return d


def _game_from_dict(d: dict) -> Game:
    d = dict(d)
    d["date"] = datetime.fromisoformat(d["date"])
    d["tipoff_time"] = datetime.fromisoformat(d["tipoff_time"]) if d.get("tipoff_time") else None
    return Game(**d)


def school_to_dict(s: School) -> dict:
    d = asdict(s)
    d["schedule"] = [_game_to_dict(g) for g in s.schedule]
    for k in ("last_successful_update", "stale_since"):
        d[k] = getattr(s, k).isoformat() if getattr(s, k) else None
    return d


def school_from_dict(d: dict) -> School:
    d = dict(d)
    d["schedule"] = [_game_from_dict(g) for g in d.get("schedule", [])]
    for k in ("last_successful_update", "stale_since"):
        d[k] = datetime.fromisoformat(d[k]) if d.get(k) else None
    return School(**d)


def load_cache() -> dict[str, School]:
    if os.environ.get("BBALL_NO_CACHE"):
        return {}
    try:
        payload = json.loads(CACHE_FILE.read_text())
        age = datetime.now() - datetime.fromisoformat(payload["timestamp"])
        if age > timedelta(days=CACHE_MAX_AGE_DAYS):
            return {}
        return {k: school_from_dict(v) for k, v in payload["schools"].items()}
    except Exception:
        return {}


def save_cache(schools: list[School], previous: dict[str, School]) -> None:
    """Healthy schools overwrite their cache entry; failed ones keep the old entry."""
    try:
        merged = {k: school_to_dict(v) for k, v in previous.items()}
        for s in schools:
            if s.healthy:
                merged[s.key] = school_to_dict(s)
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"timestamp": datetime.now().isoformat(), "schools": merged}))
    except Exception:
        pass


def apply_cache(school: School, cached: dict[str, School]) -> School:
    """Substitute the last good copy of a school whose fetch failed, marked stale."""
    if school.healthy or school.key not in cached:
        return school
    old = cached[school.key]
    old.fetch_error = school.problem
    old.stale_since = old.last_successful_update or datetime.now()
    return old


# --- FETCH ---------------------------------------------------------------------------

async def fetch(session: aiohttp.ClientSession, url: str, *, json_body: Optional[dict] = None,
                want_json: bool = False) -> tuple[Any, Optional[str]]:
    """GET (or POST json_body) with one retry. Returns (text_or_json, error)."""
    headers = {"User-Agent": USER_AGENT}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    empty = {} if want_json else ""
    error = "Unknown error"
    for attempt in range(FETCH_RETRY_COUNT + 1):
        try:
            req = session.post(url, json=json_body, headers=headers) if json_body is not None \
                else session.get(url, headers=headers)
            async with req as response:
                if response.status == 200:
                    if want_json:
                        return await response.json(content_type=None), None
                    return await response.text(), None
                error = f"HTTP {response.status}"
                backoff = 2.0 if response.status == 429 else 0.5 * (attempt + 1)
        except asyncio.TimeoutError:
            error = "Timeout"
            backoff = 0.5 * (attempt + 1)
        except Exception as e:
            error = f"{type(e).__name__}"
            backoff = 0.5 * (attempt + 1)
        if attempt < FETCH_RETRY_COUNT:
            await asyncio.sleep(backoff)
    return empty, error


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


# --- MAXPREPS (high schools) ---------------------------------------------------------

def _next_data(soup: BeautifulSoup) -> dict:
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return {}
    try:
        return json.loads(script.string).get("props", {}).get("pageProps", {}) or {}
    except ValueError:
        return {}


def _maxpreps_contests_to_games(contests: list, my_team_id: str, schedule_url: str,
                                local_cities: list[str]) -> list[Game]:
    """Decode MaxPreps' positional contest arrays. Raises ValueError if the shape is not
    the one this was written against, so the caller can fall back to the HTML table.

    contest[0]   two team entries: [1] teamId, [5] "W"/"L", [6] score,
                 [11] site (0 home, 1 away, 2 neutral), [14] school name
    contest[3]   True for placeholder entries without a game page (skipped)
    contest[5]   venue / tournament name
    contest[11]  local ISO datetime
    contest[18]  game page URL
    contest[21]  "Game"
    """
    games: list[Game] = []
    site_map = {0: "Home", 1: "Away", 2: "Neutral"}
    for ct in contests:
        if not (isinstance(ct, list) and len(ct) > 21 and isinstance(ct[0], list)):
            raise ValueError("contest shape")
        teams = [t for t in ct[0] if isinstance(t, list) and len(t) > 21]
        if len(teams) != 2:
            raise ValueError("team shape")
        if ct[3] is True or ct[21] != "Game":
            continue
        me = next((t for t in teams if t[1] == my_team_id), None)
        opp = next((t for t in teams if t[1] != my_team_id), None)
        if me is None or opp is None:
            raise ValueError("team ids")
        try:
            dt = datetime.fromisoformat(ct[11])
        except (TypeError, ValueError):
            raise ValueError("date")
        if me[11] not in site_map:
            raise ValueError("site code")
        ha = site_map[me[11]]
        result = me[5] if me[5] in ("W", "L") else None
        score = f"{me[6]}-{opp[6]}" if isinstance(me[6], int) and isinstance(opp[6], int) else None
        venue = ct[5] if ha == "Neutral" and ct[5] else None
        games.append(Game(
            date=datetime.combine(dt.date(), datetime.min.time()),
            home_away=ha,
            opponent=str(opp[14]),
            tipoff_time=at_time(dt.date(), dt) if dt.time() != datetime.min.time() else None,
            game_url=ct[18] or schedule_url,
            result=result,
            score=score,
            venue=venue,
            local=(ha != "Neutral") or is_local_venue(venue, local_cities),
        ))
    return games


def _maxpreps_table_to_games(soup: BeautifulSoup, schedule_url: str) -> tuple[list[Game], int]:
    """Fallback: read the schedule table by cell content, not column position."""
    games: list[Game] = []
    rows = soup.select("table tbody tr")
    for tr in rows:
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if not cells:
            continue
        joined = " | ".join(cells)
        m_date = re.search(r"\b(\d{1,2})/(\d{1,2})\b", joined)
        if not m_date:
            continue
        month, day = int(m_date.group(1)), int(m_date.group(2))
        try:
            game_day = date(year_for_month(month), month, day)
        except ValueError:
            continue
        m_time = re.search(r"\b(\d{1,2}:\d{2}\s*[ap]m)\b", joined, re.I)
        m_res = re.search(r"\b([WL])\s*(\d+)-(\d+)\b", joined)
        name_el = tr.find("span", class_="name")
        opp_cell = next((c for c in cells if re.match(r"^(vs|@)\b", c)), "")
        opponent = (name_el.get_text(strip=True) if name_el else re.sub(r"^(vs|@)\s*", "", opp_cell)).rstrip("*").strip()
        ha = "Away" if opp_cell.startswith("@") else "Home"
        link = tr.find("a", href=re.compile(r"/game/"))
        games.append(Game(
            date=datetime.combine(game_day, datetime.min.time()),
            home_away=ha,
            opponent=opponent,
            tipoff_time=at_time(game_day, parse_tipoff_time(m_time.group(1))) if m_time else None,
            game_url=urljoin(schedule_url, link["href"]) if link else schedule_url,
            result=m_res.group(1) if m_res else None,
            score=f"{m_res.group(2)}-{m_res.group(3)}" if m_res else None,
        ))
    return games, len(rows)


def parse_maxpreps_schedule(html: str, schedule_url: str, local_cities: list[str]) -> School:
    """Pure parser for a MaxPreps team schedule page."""
    school = School(key=schedule_url, url=schedule_url)
    soup = soup_of(html)
    props = _next_data(soup)
    team_ctx = props.get("teamContext") or {}
    data = team_ctx.get("data") or {}

    if data.get("schoolName"):
        school.name = " ".join(x for x in (data.get("schoolName"), data.get("schoolMascot")) if x)
        school.short = data["schoolName"]
    else:
        title = soup.select_one(".sub-title")
        school.name = title.get_text(strip=True) if title else None

    standing = (team_ctx.get("standingsData") or {}).get("overallStanding") or {}
    school.record = standing.get("overallWinLossTies")
    school.streak = standing.get("streak")
    school.streak_type = standing.get("streakResult")
    last = (team_ctx.get("lastYearStandingsData") or {}).get("overallStanding") or {}
    school.last_season_record = last.get("overallWinLossTies")

    contests = props.get("contests")
    games: list[Game] = []
    if isinstance(contests, list) and contests and data.get("teamId"):
        try:
            games = _maxpreps_contests_to_games(contests, data["teamId"], schedule_url, local_cities)
            school.rows_seen = len(contests)
        except ValueError as e:
            school.warning = f"contests JSON shape changed ({e}); used the HTML table"
            games = []
    if not games:
        games, rows = _maxpreps_table_to_games(soup, schedule_url)
        school.rows_seen = max(school.rows_seen, rows)
    school.schedule = games
    school.rows_parsed = len(games)
    return school


def parse_maxpreps_rankings(html: str, state_abbrev: str) -> tuple[Optional[int], Optional[str]]:
    """Returns (state_rank, tooltip) from a MaxPreps team rankings page."""
    soup = soup_of(html)
    rank_list = ((_next_data(soup).get("teamContext") or {}).get("rankingsData") or {}).get("data") or []
    state_rank = div_rank = stl_rank = None
    for r in rank_list:
        val = r.get("rank")
        if not isinstance(val, int):
            continue
        ctx = r.get("contextName") or ""
        if r.get("rankingType") == 1:
            state_rank = val
        elif "Division" in ctx or "Class" in ctx:
            div_rank = val
        elif "St. Louis" in ctx:
            stl_rank = val
    if state_rank is None:  # HTML fallback
        text = soup.get_text()
        m = re.search(r"(Illinois|Missouri)\s+#(\d+)", text)
        if m:
            state_rank = int(m.group(2))
        m = re.search(r"St\. Louis\s+#(\d+)", text)
        if m:
            stl_rank = int(m.group(1))
    parts = []
    if state_rank is not None:
        parts.append(f"{state_abbrev}# {state_rank}")
    if div_rank is not None:
        parts.append(f"{state_abbrev} Div# {div_rank}")
    if stl_rank is not None:
        parts.append(f"STL# {stl_rank}")
    return state_rank, (" | ".join(parts) if parts else None)


async def process_high_school(session: aiohttp.ClientSession, schedule_url: str, cfg: dict) -> School:
    rankings_url = schedule_url.replace("/schedule/", "/rankings/")
    (html_sched, err_sched), (html_rank, err_rank) = await asyncio.gather(
        fetch(session, schedule_url), fetch(session, rankings_url))
    if err_sched:
        return School(key=schedule_url, url=schedule_url, fetch_error=f"schedule: {err_sched}")
    try:
        school = parse_maxpreps_schedule(html_sched, schedule_url, cfg["local_venue_cities"])
    except Exception as e:
        return School(key=schedule_url, url=schedule_url, fetch_error=f"parse error: {type(e).__name__}")
    state = "MO" if "/mo/" in schedule_url.lower() else "IL"
    if err_rank:
        school.fetch_error = (school.fetch_error + "; " if school.fetch_error else "") + f"rankings: {err_rank}"
    else:
        try:
            school.ranking, school.rankings_tooltip = parse_maxpreps_rankings(html_rank, state)
        except Exception:
            pass
    if school.healthy:
        school.last_successful_update = datetime.now()
    return school


# --- ESPN (Division I) ---------------------------------------------------------------

def parse_espn_schedule(data: dict, team_id: str, local_cities: list[str]) -> tuple[list[Game], int]:
    """Pure parser for ESPN's team schedule JSON. Returns (games, events_seen)."""
    events = data.get("events") or []
    games: list[Game] = []
    for ev in events:
        try:
            comp = ev["competitions"][0]
            competitors = comp["competitors"]
            me = next(c for c in competitors if str(c["team"]["id"]) == team_id)
            opp = next(c for c in competitors if str(c["team"]["id"]) != team_id)
            start = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
        except (KeyError, IndexError, StopIteration, ValueError):
            continue
        time_valid = comp.get("timeValid", ev.get("timeValid", True))
        if time_valid:
            local_start = to_local(start)
            game_day, tipoff = local_start.date(), local_start
        else:
            # ESPN stores TBD games at midnight Eastern; read the date in that zone.
            game_day, tipoff = start.astimezone(EASTERN).date(), None
        neutral = bool(comp.get("neutralSite"))
        ha = "Neutral" if neutral else ("Home" if me.get("homeAway") == "home" else "Away")
        venue_info = comp.get("venue") or {}
        venue = venue_info.get("fullName")
        city = (venue_info.get("address") or {}).get("city")
        result = score = None
        if (comp.get("status") or {}).get("type", {}).get("completed"):
            my_s, op_s = (me.get("score") or {}).get("displayValue"), (opp.get("score") or {}).get("displayValue")
            if my_s and op_s:
                score = f"{my_s}-{op_s}"
            if me.get("winner") is True:
                result = "W"
            elif me.get("winner") is False:
                result = "L"
        link = next((l["href"] for l in ev.get("links", []) if "desktop" in l.get("rel", [])), None)
        games.append(Game(
            date=datetime.combine(game_day, datetime.min.time()),
            home_away=ha,
            opponent=opp["team"].get("shortDisplayName") or opp["team"].get("displayName", "?"),
            tipoff_time=tipoff,
            game_url=link,
            result=result,
            score=score,
            venue=venue if neutral else None,
            local=(not neutral) or is_local_venue(city, local_cities) or is_local_venue(venue, local_cities),
        ))
    return games, len(events)


def parse_espn_team(data: dict) -> tuple[Optional[str], Optional[str], Optional[int], Optional[str], Optional[str]]:
    """Returns (display name, short name, overall record, AP rank, clubhouse URL)."""
    team = data.get("team") or {}
    record = None
    for item in (team.get("record") or {}).get("items") or []:
        if item.get("type") == "total" and item.get("summary"):
            record = item["summary"]
            break
    rank = team.get("rank") if isinstance(team.get("rank"), int) else None
    url = next((l["href"] for l in team.get("links", []) if "clubhouse" in l.get("rel", [])), None)
    return team.get("displayName"), team.get("shortDisplayName"), record, rank, url


async def process_college(session: aiohttp.ClientSession, espn_id: int, cfg: dict,
                          season_end_year: Optional[int] = None) -> School:
    team_id = str(espn_id)
    end_year = season_end_year or season_start_year() + 1
    key = f"espn:{team_id}"
    team_url = f"{ESPN_API}/teams/{team_id}"
    sched_urls = [f"{ESPN_API}/teams/{team_id}/schedule?season={end_year}&seasontype={t}" for t in (2, 3)]
    (team_data, err_team), (reg, err_reg), (post, err_post) = await asyncio.gather(
        fetch(session, team_url, want_json=True),
        *(fetch(session, u, want_json=True) for u in sched_urls))
    school = School(key=key, url=f"https://www.espn.com/mens-college-basketball/team/_/id/{team_id}")
    if err_team:
        school.fetch_error = f"team: {err_team}"
    else:
        name, short, record, rank, url = parse_espn_team(team_data)
        school.name, school.short, school.record, school.ranking = name, short, record or "0-0", rank
        if url:
            school.url = url
    if err_reg:
        school.fetch_error = (school.fetch_error + "; " if school.fetch_error else "") + f"schedule: {err_reg}"
        return school
    games, seen = parse_espn_schedule(reg, team_id, cfg["local_venue_cities"])
    if not err_post:
        more, more_seen = parse_espn_schedule(post, team_id, cfg["local_venue_cities"])
        games += more
        seen += more_seen
    school.schedule = sorted(games, key=lambda g: g.date)
    school.rows_seen, school.rows_parsed = seen, len(games)
    if school.healthy:
        school.last_successful_update = datetime.now()
    return school


async def fetch_espn_last_season_record(session: aiohttp.ClientSession, espn_id: int) -> Optional[str]:
    """Final record of last season, read from our entry in the last completed game."""
    url = f"{ESPN_API}/teams/{espn_id}/schedule?season={season_start_year()}&seasontype=2"
    data, err = await fetch(session, url, want_json=True)
    if err:
        return None
    best: Optional[str] = None
    for ev in data.get("events") or []:
        try:
            comp = ev["competitions"][0]
            if not comp["status"]["type"]["completed"]:
                continue
            me = next(c for c in comp["competitors"] if str(c["team"]["id"]) == str(espn_id))
            summary = next((r.get("displayValue") for r in me.get("record", []) if r.get("type") == "total"), None)
            if summary:
                best = summary
        except (KeyError, IndexError, StopIteration):
            continue
    return best


# --- NCAA NET ------------------------------------------------------------------------

def parse_net_rankings(html: str) -> tuple[dict[str, int], Optional[date]]:
    """Returns ({school: rank}, through-games date if shown)."""
    soup = soup_of(html)
    result: dict[str, int] = {}
    table = soup.find("table")
    if table:
        for tr in (table.find("tbody") or table).find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) >= 2 and tds[0].get_text(strip=True).isdigit():
                result[tds[1].get_text(strip=True)] = int(tds[0].get_text(strip=True))
    through = None
    m = re.search(r"Through Games\s+([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})", soup.get_text())
    if m:
        for fmt in ("%b %d %Y", "%B %d %Y"):
            try:
                through = datetime.strptime(f"{m.group(1)[:3] if fmt == '%b %d %Y' else m.group(1)} {m.group(2)} {m.group(3)}", fmt).date()
                break
            except ValueError:
                continue
    return result, through


def net_rank_for(net_name: Optional[str], rankings: dict[str, int]) -> Optional[int]:
    if not net_name or not rankings:
        return None
    if net_name in rankings:
        return rankings[net_name]
    return next((r for n, r in rankings.items() if n.lower() == net_name.lower()), None)


async def fetch_net_rankings(session: aiohttp.ClientSession) -> tuple[dict[str, int], Optional[str]]:
    """NET table for the current season; empty (with a reason) while NCAA still shows last season."""
    html, err = await fetch(session, NCAA_NET_URL)
    if err:
        return {}, f"NET: {err}"
    rankings, through = parse_net_rankings(html)
    if not rankings:
        return {}, "NET: no table"
    if through and through < date(season_start_year(), 10, 1):
        return {}, f"NET: last season's table (through {through:%b %d})"
    return rankings, None


# --- NJCAA POLL ----------------------------------------------------------------------

NJCAA_SEASONS_QUERY = "query { listSeasons { seasons { seasonId season } } }"
NJCAA_POLLS_QUERY = """
query Polls($tenantId: ID!, $seasonId: ID!, $sportId: ID!, $genderId: ID, $limit: Int) {
  publishedPolls(tenantId: $tenantId, seasonId: $seasonId, sportId: $sportId, genderId: $genderId, limit: $limit) {
    publication { title divisionCode genderCode publishedAt }
    results { rank ranked teamName record }
  }
}"""


def parse_njcaa_polls(data: dict, division: str = "d1") -> tuple[dict[str, int], Optional[str]]:
    """Most recent poll for the division: ({team name: rank}, poll title)."""
    polls = ((data.get("data") or {}).get("publishedPolls")) or []
    polls = [p for p in polls if (p.get("publication") or {}).get("divisionCode") == division]
    polls.sort(key=lambda p: p["publication"].get("publishedAt") or "", reverse=True)
    if not polls:
        return {}, None
    poll = polls[0]
    ranks = {r["teamName"]: int(r["rank"]) for r in poll.get("results") or []
             if r.get("ranked", True) and isinstance(r.get("rank"), int) and r.get("teamName")}
    return ranks, poll["publication"].get("title")


async def fetch_njcaa_rankings(session: aiohttp.ClientSession) -> tuple[dict[str, int], Optional[str]]:
    seasons, err = await fetch(session, NJCAA_GRAPHQL_URL, json_body={"query": NJCAA_SEASONS_QUERY}, want_json=True)
    if err:
        return {}, f"NJCAA: {err}"
    slug = season_slug()
    season_id = next((s.get("seasonId") for s in ((seasons.get("data") or {}).get("listSeasons") or {}).get("seasons", [])
                      if s.get("season") == slug), None)
    if not season_id:
        return {}, f"NJCAA: season {slug} not listed"
    body = {"query": NJCAA_POLLS_QUERY, "variables": {
        "tenantId": NJCAA_TENANT_ID, "seasonId": season_id, "sportId": NJCAA_SPORT_ID,
        "genderId": NJCAA_GENDER_ID, "limit": 12}}
    data, err = await fetch(session, NJCAA_GRAPHQL_URL, json_body=body, want_json=True)
    if err:
        return {}, f"NJCAA: {err}"
    ranks, _title = parse_njcaa_polls(data)
    return ranks, (None if ranks else "NJCAA: no DI poll yet")


# --- REGION 24 STANDINGS (JUCO records) ----------------------------------------------

def parse_region24_standings(html: str) -> dict[str, str]:
    """{team name: season record} from the Region 24 standings table.

    Rows are: <th>Team</th> then GP/Record/Win% twice ("Region", "Overall"). The first
    Record's GP matched the team game logs last season (35 for SWIC) while the "Overall"
    GP did not (61), so the first record is used.
    """
    out: dict[str, str] = {}
    for tr in soup_of(html).select("table tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) < 3 or not cells[0]:
            continue
        rec = next((c for c in cells[1:] if re.fullmatch(r"\d+-\d+", c)), None)
        if rec:
            out.setdefault(cells[0], rec)
    return out


async def fetch_region24_records(session: aiohttp.ClientSession, slug: Optional[str] = None) -> tuple[dict[str, str], Optional[str]]:
    html, err = await fetch(session, REGION24_STANDINGS_URL.format(season=slug or season_slug()))
    if err:
        return {}, f"Region 24: {err}"
    records = parse_region24_standings(html)
    return records, (None if records else "Region 24: no standings table")


# --- SWIC ----------------------------------------------------------------------------

def parse_swic_schedule(html: str) -> School:
    school = School(key="swic", url=SWIC_URL, name="SWIC")
    soup = soup_of(html)
    games: list[Game] = []
    rows_seen = 0
    tbody = soup.find("tbody")
    for row in (tbody.find_all("tr") if tbody else []):
        cols = row.find_all("td")
        if len(cols) < 5:
            continue
        d_str = cols[0].get_text(" ", strip=True)
        if not d_str or d_str.lower() == "date":
            continue
        rows_seen += 1
        if "-" in d_str and d_str[:3].isalpha():      # "Oct 17-18"
            d_str = d_str.split("-")[0].strip()
        try:
            month = datetime.strptime(d_str.split()[0][:3], "%b").month
            game_day = date(year_for_month(month), month, int(d_str.split()[1]))
        except (ValueError, IndexError):
            continue
        parts = [p for p in cols[2].get_text("\n", strip=True).split("\n") if p]
        if not parts:
            continue
        note = None
        if len(parts) > 1 and re.search(r"scrimmage|jamboree|tournament|classic", parts[0], re.I):
            note, opponent = parts[0], " ".join(parts[1:])
        else:
            opponent = " ".join(parts)
        loc = cols[3].get_text(" ", strip=True)
        is_home = loc.lower() == "home" or any(x in loc for x in ("Belleville", "SWIC", "Sam Wolf"))
        result = score = None
        if len(cols) >= 6:
            m = re.search(r"([WL])?\s*(\d+)\s*-\s*(\d+)", cols[5].get_text(" ", strip=True))
            if m:
                result, score = m.group(1), f"{m.group(2)}-{m.group(3)}"
        games.append(Game(
            date=datetime.combine(game_day, datetime.min.time()),
            home_away="Home" if is_home else "Away",
            opponent=opponent,
            tipoff_time=at_time(game_day, parse_tipoff_time(cols[4].get_text(" ", strip=True))),
            game_url=SWIC_URL,
            result=result,
            score=score,
            note=note,
        ))
    school.schedule, school.rows_seen, school.rows_parsed = games, rows_seen, len(games)
    return school


async def process_swic(session: aiohttp.ClientSession) -> School:
    html, err = await fetch(session, SWIC_URL)
    if err:
        return School(key="swic", url=SWIC_URL, name="SWIC", fetch_error=f"schedule: {err}")
    try:
        school = parse_swic_schedule(html)
    except Exception as e:
        return School(key="swic", url=SWIC_URL, name="SWIC", fetch_error=f"parse error: {type(e).__name__}")
    if school.healthy:
        school.last_successful_update = datetime.now()
    return school


# --- VINCENNES -----------------------------------------------------------------------

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September",
     "October", "November", "December"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["Sept"] = 9


def parse_vincennes_schedule(html: str, schedule_url: str) -> School:
    school = School(key="vincennes", url=schedule_url, name="Vincennes")
    soup = soup_of(html)
    games: list[Game] = []
    seen_keys: set[tuple] = set()
    rows = soup.find_all("div", class_="event-row")
    rows_seen = 0
    for row in rows:
        classes = row.get("class") or []
        opp_el = row.find(class_="event-opponent-name") or row.find(class_="team-name")
        opponent = opp_el.get_text(strip=True) if opp_el else ""
        if not opponent:
            continue
        rows_seen += 1
        if "Jamboree" in opponent or "Do not count" in opponent:
            continue
        month_el = row.find_previous(class_="month-heading")
        month_num = MONTHS.get(month_el.get_text(strip=True)) if month_el else None
        date_el = row.find(class_="date")
        dateinfo_el = row.find(class_="event-dateinfo")
        date_txt = date_el.get_text(strip=True) if date_el else ""
        dateinfo_txt = dateinfo_el.get_text(" ", strip=True) if dateinfo_el else ""
        m = re.match(r"^([A-Za-z]+)\.?\s*(\d{1,2})$", date_txt)
        if not m:
            continue
        if m.group(1) in MONTHS:          # "Oct 11" (the next-event card)
            month_num, day_num = MONTHS[m.group(1)], int(m.group(2))
        else:                              # "Sun. 11" under a month heading
            day_num = int(m.group(2))
        if not month_num:
            continue
        try:
            game_day = date(year_for_month(month_num), month_num, day_num)
        except ValueError:
            continue
        key = (game_day, opponent)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        ha = "Home" if "home" in classes else ("Neutral" if "neutral" in classes else "Away")
        result = score = tipoff = None
        if "Final" in dateinfo_txt:
            sm = re.search(r"([WL]),\s*(\d+-\d+)", dateinfo_txt)
            if sm:
                result, score = sm.group(1), sm.group(2)
        else:
            tm = re.search(r"\b((?:[1-9]|1[0-2]):\d{2}\s*[AP]M)\b", dateinfo_txt, re.I)
            if tm:
                t = parse_tipoff_time(tm.group(1))
                if t:  # the site shows venue time (Eastern); convert to ours
                    tipoff = at_time(game_day, t, VINCENNES_TZ)
        games.append(Game(
            date=datetime.combine(game_day, datetime.min.time()),
            home_away=ha,
            opponent=opponent,
            tipoff_time=tipoff,
            game_url=schedule_url,
            result=result,
            score=score,
            note="Exhibition" if "exhibition" in classes else None,
            local=ha != "Neutral",
        ))
    school.schedule, school.rows_seen, school.rows_parsed = games, rows_seen, len(games)
    return school


async def process_vincennes(session: aiohttp.ClientSession) -> School:
    url = VINCENNES_SCHEDULE_URL.format(season=season_slug())
    html, err = await fetch(session, url)
    if err:
        return School(key="vincennes", url=url, name="Vincennes", fetch_error=f"schedule: {err}")
    try:
        school = parse_vincennes_schedule(html, url)
    except Exception as e:
        return School(key="vincennes", url=url, name="Vincennes", fetch_error=f"parse error: {type(e).__name__}")
    if school.healthy:
        school.last_successful_update = datetime.now()
    return school


# --- SIDEARM (Division II, Division III, NAIA) ----------------------------------------

def sidearm_schedule_url(base: str, slug: Optional[str] = None) -> str:
    return base.rstrip("/") + SIDEARM_SCHEDULE_PATH.format(season=slug or season_slug())


def parse_sidearm_time(text: str) -> tuple[Optional[datetime], ZoneInfo]:
    """'7:00 PM', '6 p.m.', 'Noon (CT)', '12 p.m. ET' -> (time-only datetime, zone).

    TBA/TBD or an empty string gives (None, CENTRAL). An untagged time is Central: every
    school this parser serves is in the St. Louis area and posts its own local time.
    """
    tag = re.search(r"\b([ECMP])[SD]?T\b", text)
    tz = TZ_TAGS[tag.group(1)] if tag else CENTRAL
    if re.search(r"\bnoon\b", text, re.I):
        return datetime.strptime("12:00 PM", "%I:%M %p"), tz
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\b", text, re.I)
    if not m:
        return None, tz
    try:
        return datetime.strptime(f"{m.group(1)}:{m.group(2) or '00'} {m.group(3).upper()}M", "%I:%M %p"), tz
    except ValueError:
        return None, tz


def parse_sidearm_record(html_or_soup: Any) -> Optional[str]:
    """Overall record from the "Season Record" strip at the top of a Sidearm schedule."""
    soup = soup_of(html_or_soup) if isinstance(html_or_soup, str) else html_or_soup
    block = soup.select_one(".sidearm-schedule-record")
    m = re.search(r"Overall\s+(\d+-\d+)", block.get_text(" ", strip=True)) if block else None
    return m.group(1) if m else None


def parse_sidearm_schedule(html: str, schedule_url: str, local_cities: list[str]) -> School:
    """Pure parser for a Sidearm men's basketball schedule page.

    One <li class="sidearm-schedule-game"> per game. The li's classes carry home / away /
    neutral and, once played, W or L. Some sites (UMSL) repeat the inner blocks for mobile
    and desktop, so only the first match of each selector inside the li is read.
    """
    school = School(key=schedule_url, url=schedule_url)
    soup = soup_of(html)
    school.record = parse_sidearm_record(soup)
    games: list[Game] = []
    rows = soup.select("li.sidearm-schedule-game")
    for row in rows:
        classes = row.get("class") or []
        opp_el = row.select_one(".sidearm-schedule-game-opponent-name")
        # A "|" would end the SwiftBar title ("(2) | RV William Jewell College").
        opponent = re.sub(r"\s*\|\s*", " ", opp_el.get_text(" ", strip=True)) if opp_el else ""
        # "Lewis University vs. Kentucky Wesleyan College": another pair's game in a
        # tournament this school hosts.
        if not opponent or re.search(r"\svs\.?\s", opponent, re.I):
            continue
        result_el = row.select_one(".sidearm-schedule-game-result")
        result_txt = result_el.get_text(" ", strip=True) if result_el else ""
        if re.search(r"cancel|postpon", result_txt, re.I):
            continue
        date_el = row.select_one(".sidearm-schedule-game-opponent-date")
        spans = [s.get_text(" ", strip=True) for s in date_el.find_all("span", recursive=False)] if date_el else []
        dm = re.match(r"([A-Za-z]+)\.?\s+(\d{1,2})", spans[0]) if spans else None
        if not dm or dm.group(1)[:3].title() not in MONTHS:
            continue
        month = MONTHS[dm.group(1)[:3].title()]
        try:
            game_day = date(year_for_month(month), month, int(dm.group(2)))
        except ValueError:
            continue
        # The time is the second date span on most sites; UMSL only has it in the
        # opponent link's aria-label ("UHSP on November 21 6 p.m.").
        link = opp_el.find("a")
        am = re.search(r" on [A-Za-z]+ \d{1,2}\b(.*)$", link.get("aria-label") or "") if link else None
        tip, tz = parse_sidearm_time(am.group(1) if am else " ".join(spans[1:]))
        note = None
        if re.search(r"[\[(]\s*exh[^\])]*[\])]", opponent, re.I):
            opponent = re.sub(r"\s*[\[(]\s*exh[^\])]*[\])]", "", opponent, flags=re.I).strip()
            note = "Exhibition"
        elif re.search(r"\balumni\b|\bscrimmage\b", opponent, re.I):
            note = "Exhibition"
        if "sidearm-schedule-home-game" in classes:
            ha = "Home"
        elif "sidearm-schedule-neutral-game" in classes:
            ha = "Neutral"
        else:
            ha = "Away"
        loc_el = row.select_one(".sidearm-schedule-game-location")
        location = re.sub(r"\s+", " ", loc_el.get_text(" ", strip=True).replace("|", "/")) if loc_el else ""
        result = score = None
        if "sidearm-schedule-game-completed" in classes:
            result = "W" if "W" in classes else "L" if "L" in classes else None
            sm = re.search(r"(\d+)\s*-\s*(\d+)", result_txt)
            if sm:
                score = f"{sm.group(1)}-{sm.group(2)}"
        box = row.select_one(".sidearm-schedule-game-links-boxscore a")
        games.append(Game(
            date=datetime.combine(game_day, datetime.min.time()),
            home_away=ha,
            opponent=opponent,
            tipoff_time=at_time(game_day, tip, tz),
            game_url=urljoin(schedule_url, box["href"]) if box and box.get("href") else schedule_url,
            result=result,
            score=score,
            venue=(location.split(" / ")[-1] or None) if ha == "Neutral" else None,
            note=note,
            local=ha != "Neutral" or is_local_venue(location, local_cities),
        ))
    school.schedule, school.rows_seen, school.rows_parsed = games, len(rows), len(games)
    return school


def small_college_key(entry: dict) -> str:
    return "sidearm:" + re.sub(r"^https?://(www\.)?", "", str(entry.get("base", ""))).strip("/")


async def process_small_college(session: aiohttp.ClientSession, entry: dict, cfg: dict) -> School:
    key, name, level = small_college_key(entry), entry.get("name"), entry.get("level")
    url = sidearm_schedule_url(entry["base"])
    blank = School(key=key, url=url, name=name, short=name, level=level)
    html, err = await fetch(session, url)
    if err:
        blank.fetch_error = f"schedule: {err}"
        return blank
    try:
        school = parse_sidearm_schedule(html, url, cfg["local_venue_cities"])
    except Exception as e:
        blank.fetch_error = f"parse error: {type(e).__name__}"
        return blank
    school.key, school.name, school.short, school.level = key, name, name, level
    if school.healthy:
        school.last_successful_update = datetime.now()
    return school


async def fetch_sidearm_last_season_record(session: aiohttp.ClientSession, entry: dict) -> Optional[str]:
    html, err = await fetch(session, sidearm_schedule_url(entry["base"], season_slug(season_start_year() - 1)))
    return None if err else parse_sidearm_record(html)


# --- SEASON STATE --------------------------------------------------------------------

def listed_games(s: School) -> list[Game]:
    """Games that belong in the menu: home games and local neutral games."""
    return [g for g in s.schedule if g.home_away == "Home" or (g.home_away == "Neutral" and g.local)]


def season_state(schools: list[School]) -> tuple[str, Optional[date]]:
    """('in', None) | ('pre', first game date) | ('over', None), judged on the games we list."""
    t = today()
    dates = sorted({g.date.date() for s in schools for g in listed_games(s) if not g.note})
    if any(abs((d - t).days) <= OFF_SEASON_WINDOW_DAYS for d in dates):
        return "in", None
    future = [d for d in dates if d > t]
    if future:
        return "pre", future[0]
    return "over", None


def sort_schools(schools: list[School]) -> list[School]:
    def key(s: School):
        r = s.net_rank or s.ranking
        return (r is None, r or 0, s.level or "", s.name or "")
    return sorted(schools, key=key)


# --- DISPLAY -------------------------------------------------------------------------

def school_line(s: School, rank_scope: str, state: str = "in") -> str:
    streak_txt = ""
    if s.streak and s.streak_type and s.streak > 2:
        streak_txt = f"{'🔥' if s.streak_type == 'W' else '❄️'}{s.streak_type}{s.streak}"
    has_game_today = any(g.date.date() == today() for g in listed_games(s))
    dot = "🟠" if has_game_today else ""
    warn = "⚠️ " if s.fetch_error else ""
    rec = f"({s.record})" if s.record else ""
    if state == "pre" and s.last_season_record:
        rec = f"({s.last_season_record}) last season"
    if rank_scope:   # high schools: "[IL #123]"
        rank_col = (f"[{rank_scope} #{s.ranking}]" if s.ranking else "[  -  ]").ljust(10)
        name_col = (s.name or "Unknown").ljust(24)
        text = f"{warn}{rank_col} {name_col} {rec.ljust(6)} {streak_txt.ljust(4)} {dot}"
    else:            # colleges: NET (D1) or poll (JUCO) rank
        r = s.net_rank or s.ranking
        rank_col = (f"[# {r}]" if r else f"[{s.level}]" if s.level else "[  -  ]").ljust(8)
        name_col = (s.name or "Unknown").ljust(28)
        text = f"{warn}{rank_col}{name_col} {rec.ljust(6)} {streak_txt.ljust(4)} {dot}"
    return text.rstrip()


def school_tooltip(s: School, rank_scope: str) -> str:
    parts = []
    if s.rankings_tooltip:
        parts.append(s.rankings_tooltip)
    if not rank_scope:
        if s.net_rank:
            parts.append(f"NET #{s.net_rank}")
        if s.ranking:
            parts.append(f"{'AP' if s.key.startswith('espn:') else 'NJCAA'} #{s.ranking}")
    if s.stale_since:
        parts.append(f"Showing cached data from {s.stale_since:%b %d %H:%M}")
    elif s.last_successful_update:
        parts.append(f"Updated: {s.last_successful_update:%b %d %H:%M}")
    if s.fetch_error:
        parts.append(f"Problem: {s.fetch_error}")
    if s.warning:
        parts.append(f"Note: {s.warning}")
    return " | ".join(parts).replace('"', "'")


def game_label(g: Game) -> str:
    """'Opponent' plus venue/note tags."""
    label = g.opponent
    if g.home_away == "Neutral":
        label += f" (N) {g.venue}" if g.venue else " (N)"
    if g.note:
        label += f" · {g.note}"
    return label


def calendar_line(g: Game, school_name: str, depth: int, label: str, style: str = "") -> str:
    """The ⌥ alternate of a game row: hands Fantastical a natural-language sentence.
    SwiftBar swaps it in for the line printed just before it while Option is held, so it
    must directly follow its game row at the same depth. `label` is that row's text.
    A game with no tipoff yet becomes an all-day event tagged '(time TBD)'."""
    where = g.venue if g.home_away == "Neutral" and g.venue else school_name
    title = f"{g.opponent} at {school_name}" if g.home_away == "Home" else f"{school_name} vs {g.opponent}"
    if g.tipoff_time:
        appt = f'{g.date:%Y/%m/%d} at {g.tipoff_time:%H%M} "{title}" at {where}'
    else:
        appt = f'{g.date:%Y/%m/%d} "{title} (time TBD)" at {where}'
    return (f"{'-' * depth}Add to Calendar · {label} | href=x-fantastical3://parse?add=1&sentence={quote(appt)} "
            f"sfimage=calendar.badge.plus alternate=true terminal=false{style}")


def print_game_line(g: Game, s: School, depth: int) -> None:
    t = today()
    prefix = "-" * depth
    if g.date.date() < t:
        emoji = "✅" if g.result == "W" else "❌" if g.result == "L" else "📊"
        res = f" ({g.result})" if g.result else ""
        score = f" {g.score}" if g.score else ""
        print(f'{prefix}{emoji} {g.date:%b %d}: {game_label(g)}{res}{score} | href={g.game_url or s.url} size=11 color={COLOR_MUTED}')
        return
    when = f"@ {g.tipoff_time:%H%M}" if g.tipoff_time else "(TBD)"
    msg = f"{format_relative_date(g.date)}: {game_label(g)} {when}"
    is_today = g.date.date() == t
    md = f"**{msg}**" if is_today else msg
    color = f" color={COLOR_TODAY}" if is_today else ""
    print(f'{prefix}{md} | href={g.game_url or s.url} md=true{color}')
    print(calendar_line(g, s.name or "", depth, md, f" md=true{color}"))


def print_section(schools: list[School], rank_scope: str, header: str, state: str, cfg: dict) -> None:
    if not schools:
        return
    print("---")
    print(f"{header} | size=13 color={COLOR_MUTED}")
    t = today()
    for s in sort_schools(schools):
        line = school_line(s, rank_scope, state)
        print(f'{line} | href={s.url} font=Menlo-Bold tooltip="{school_tooltip(s, rank_scope)}"')
        games = sorted(listed_games(s), key=lambda g: g.date)
        upcoming = [g for g in games if g.date.date() >= t]
        past = [g for g in games if g.date.date() < t]
        recent = past[-cfg["max_past_games"]:] if (state == "in" or not upcoming) else []
        if upcoming:
            word = "upcoming " if state == "in" else ""
            print(f"--{len(upcoming)} {word}home game{'s' if len(upcoming) != 1 else ''} | size=11 color=#666666")
        elif recent:
            print(f"--Last home games | size=11 color=#666666")
        for g in recent + upcoming:
            print_game_line(g, s, 2)


def short_name(s: School) -> str:
    return s.short or s.name or "Unknown"


def upcoming_listed(schools: list[School]) -> list[tuple[School, Game]]:
    """All listed games from today on, across schools, soonest first; tracked-vs-tracked once."""
    t = today()
    pairs = [(s, g) for s in schools for g in listed_games(s) if g.date.date() >= t]
    pairs.sort(key=lambda sg: (sg[1].date, sg[1].tipoff_time or sg[1].date + timedelta(hours=23)))
    by_short = {short_name(o).lower(): o for o in schools}
    seen: set[tuple] = set()
    out = []
    for s, g in pairs:
        other = by_short.get(g.opponent.lower())
        key = (g.date.date(), frozenset((s.key, other.key))) if other and other is not s and g.home_away == "Neutral" else None
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append((s, g))
    return out


def print_next_up(schools: list[School], count: int) -> None:
    pairs = upcoming_listed(schools)[:count]
    if not pairs:
        return
    print("---")
    print(f"NEXT UP | size=13 color={COLOR_MUTED}")
    for s, g in pairs:
        when = f"{g.tipoff_time:%H%M}" if g.tipoff_time else "TBD "
        if g.home_away == "Home":
            who = f"{g.opponent} at {short_name(s)}"
        else:
            who = f"{short_name(s)} vs {g.opponent}" + (f" · {g.venue}" if g.venue else "")
        if g.note:
            who += f" · {g.note}"
        is_today = g.date.date() == today()
        color = f" color={COLOR_TODAY}" if is_today else ""
        print(f"{format_relative_date(g.date).ljust(8)} {when}  {who} | href={g.game_url or s.url} font=Menlo{color}")
        print(calendar_line(g, s.name or "", 0, f"{format_relative_date(g.date)} {when.strip()}  {who}", f" font=Menlo{color}"))


def print_openers(schools: list[School], first_game: Optional[date]) -> None:
    """Off-season header: countdown and each team's first home game, grouped by date."""
    print("---")
    if first_game:
        days = (first_game - today()).days
        print(f"OFF-SEASON · first tip {first_game:%b %d} ({days} day{'s' if days != 1 else ''}) | size=13 color={COLOR_MUTED}")
    else:
        print(f"SEASON OVER · final records | size=13 color={COLOR_MUTED}")
        return
    firsts: dict[date, list[tuple[School, Game]]] = {}
    for s in schools:
        up = [g for g in listed_games(s) if g.date.date() >= today()]
        if up:
            firsts.setdefault(up[0].date.date(), []).append((s, up[0]))
    if not firsts:
        return
    print(f"Season openers | size=11 color=#666666")
    for d in sorted(firsts):
        parts = [f"{short_name(s)} vs {g.opponent}" + (f" · {g.note}" if g.note else "") for s, g in firsts[d]]
        print(f"{d:%b %d}  {' · '.join(parts)} | href={firsts[d][0][1].game_url or firsts[d][0][0].url} font=Menlo size=12")


def print_title(schools: list[School], state: str, first_game: Optional[date]) -> None:
    if state == "in":
        n_today = sum(1 for s in schools for g in listed_games(s) if g.date.date() == today())
        if n_today:
            print(f"{n_today} | sfimage=basketball.fill sfcolor={COLOR_TODAY}")
        else:
            print("| sfimage=basketball")
    elif state == "pre":
        print(f"| sfimage=basketball sfcolor={COLOR_OFFSEASON}")
    else:
        print(f"| sfimage=basketball sfcolor={COLOR_OFFSEASON}")


def print_footer(notes: list[str], calendar_hint: bool = False) -> None:
    print("---")
    for n in notes:
        print(f"{n} | size=11 color={COLOR_MUTED}")
    if calendar_hint:
        print(f"Hold ⌥ on a game to add it to your calendar | size=11 color={COLOR_MUTED}")
    print(f"Refresh | refresh=true")


# --- MAIN ----------------------------------------------------------------------------

def make_session() -> aiohttp.ClientSession:
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    connector = aiohttp.TCPConnector(limit_per_host=FETCH_LIMIT_PER_HOST, ssl=ctx)
    return aiohttp.ClientSession(connector=connector, timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS))


async def gather_all(cfg: dict) -> tuple[dict[str, list[School]], list[str]]:
    """Fetch every source. Returns ({section: schools}, notes about rankings sources)."""
    notes: list[str] = []
    cc_cfg = cfg.get("community_colleges") or {}
    small_cfg = [e for e in (cfg.get("small_colleges") or []) if isinstance(e, dict) and e.get("base")]
    async with make_session() as session:
        hs_tasks = [process_high_school(session, hs["url"], cfg) for hs in cfg["high_schools"]]
        d1_tasks = [process_college(session, c["espn_id"], cfg) for c in cfg["colleges"]]
        cc_tasks = []
        if cc_cfg.get("swic", True):
            cc_tasks.append(process_swic(session))
        if cc_cfg.get("vincennes", True):
            cc_tasks.append(process_vincennes(session))
        small_tasks = [process_small_college(session, e, cfg) for e in small_cfg]
        hs, d1, cc, small, (net, net_note), (njcaa, njcaa_note), (records, rec_note) = await asyncio.gather(
            asyncio.gather(*hs_tasks, return_exceptions=True),
            asyncio.gather(*d1_tasks, return_exceptions=True),
            asyncio.gather(*cc_tasks, return_exceptions=True),
            asyncio.gather(*small_tasks, return_exceptions=True),
            fetch_net_rankings(session),
            fetch_njcaa_rankings(session),
            fetch_region24_records(session),
        )

        def ok(results, keys):
            out = []
            for r, k in zip(results, keys):
                out.append(r if isinstance(r, School) else School(key=k, url="", fetch_error=f"crash: {type(r).__name__}"))
            return out

        hs = ok(hs, [h["url"] for h in cfg["high_schools"]])
        d1 = ok(d1, [f"espn:{c['espn_id']}" for c in cfg["colleges"]])
        cc = ok(cc, [k for k in ("swic", "vincennes") if cc_cfg.get(k, True)])
        small = ok(small, [small_college_key(e) for e in small_cfg])

        for s, c in zip(d1, cfg["colleges"]):
            s.net_rank = net_rank_for(c.get("net_name"), net)
        for s in cc:
            s.ranking = njcaa.get(REGION24_NAMES.get(s.key, ""))
            s.record = records.get(REGION24_NAMES.get(s.key, ""))
        for n in (net_note, njcaa_note, rec_note):
            if n:
                notes.append(n)

        state, _ = season_state(hs + d1 + cc + small)
        if state == "pre":
            last_records, _ = await fetch_region24_records(session, season_slug(season_start_year() - 1))
            for s in cc:
                s.last_season_record = last_records.get(REGION24_NAMES.get(s.key, ""))
            finals = await asyncio.gather(*(fetch_espn_last_season_record(session, c["espn_id"]) for c in cfg["colleges"]))
            for s, rec in zip(d1, finals):
                s.last_season_record = rec
            finals = await asyncio.gather(*(fetch_sidearm_last_season_record(session, e) for e in small_cfg))
            for s, rec in zip(small, finals):
                s.last_season_record = rec

    sections = {
        "il": [s for s in hs if "/mo/" not in s.key.lower()],
        "mo": [s for s in hs if "/mo/" in s.key.lower()],
        "cc": cc,
        "d1": d1,
        "small": small,
    }
    return sections, notes


async def run_menu() -> None:
    cfg = load_config()
    cached = load_cache()
    sections, notes = await gather_all(cfg)
    all_schools = [s for group in sections.values() for s in group]
    save_cache(all_schools, cached)
    for name, group in sections.items():
        sections[name] = [apply_cache(s, cached) for s in group]
    all_schools = [s for group in sections.values() for s in group]

    state, first_game = season_state(all_schools)

    print_title(all_schools, state, first_game)
    if state == "in":
        print_next_up(all_schools, cfg["next_up_count"])
    else:
        print_openers(all_schools, first_game)
    print_section(sections["il"], "IL", "ILLINOIS HIGH SCHOOLS", state, cfg)
    print_section(sections["mo"], "MO", "MISSOURI HIGH SCHOOLS", state, cfg)
    print_section(sections["cc"], "", "COMMUNITY COLLEGE", state, cfg)
    print_section(sections["d1"], "", "DIVISION I", state, cfg)
    print_section(sections["small"], "", "DIVISION II · III · NAIA", state, cfg)
    problems = [f"⚠️ {s.name or s.key}: {s.fetch_error}" for s in all_schools if s.fetch_error]
    has_upcoming = any(g.date.date() >= today() for s in all_schools for g in listed_games(s))
    print_footer(problems + notes, calendar_hint=has_upcoming)


async def run_check() -> int:
    cfg = load_config()
    sections, notes = await gather_all(cfg)
    failures = 0
    print(f"{'STATUS':6} {'TEAM':30} {'ROWS':>4} {'GAMES':>5} {'HOME':>4}  RECORD  RANK   DETAIL")
    for group in sections.values():
        for s in group:
            problem = s.problem or (f"warning: {s.warning}" if s.warning else None)
            status = "FAIL" if s.problem else ("WARN" if s.warning else "OK")
            failures += bool(s.problem)
            home = len(listed_games(s))
            rank = f"NET {s.net_rank}" if s.net_rank else (f"#{s.ranking}" if s.ranking else "-")
            print(f"{status:6} {(s.name or s.key)[:30]:30} {s.rows_seen:4d} {s.rows_parsed:5d} {home:4d}  {(s.record or '-'):7} {rank:6} {problem or ''}")
            failures += bool(s.warning)
    for n in notes:
        print(f"NOTE   {n}")
    state, first_game = season_state([s for g in sections.values() for s in g])
    print(f"season state: {state}" + (f", first game {first_game}" if first_game else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(asyncio.run(run_check()))
    asyncio.run(run_menu())
