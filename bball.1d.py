#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.8"
# dependencies = [
#     "aiohttp>=3.8.0",
#     "beautifulsoup4>=4.9.0",
# ]
# ///
import asyncio
import re
import string
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import aiohttp
from bs4 import BeautifulSoup, Tag


# <bitbar.title>Max Preps Basketball Schedule</bitbar.title>
# <bitbar.author>hodgesd</bitbar.author>
# <bitbar.desc>Display local preps/college basketball schedules/ranking/records</bitbar.desc>
# <bitbar.dependencies>python,aiohttp</bitbar.dependencies>
# <bitbar.version>3.2</bitbar.version>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

@dataclass
class Game:
    date: datetime
    home_away: str
    opponent: str
    tipoff_time: Optional[datetime] = None
    game_url: Optional[str] = None
    result: Optional[str] = None  # "W" or "L"
    score: Optional[str] = None  # "65-58"


@dataclass
class School:
    url: str
    last_updated: datetime
    name: Optional[str] = None
    mascot: Optional[str] = None
    record: Optional[str] = None
    ranking: Optional[int] = None
    rankings_tooltip: Optional[str] = None
    streak: Optional[int] = None  # Number of games in current streak
    streak_type: Optional[str] = None  # "W" or "L"
    schedule: list[Game] = field(default_factory=list)
    fetch_error: Optional[str] = None
    last_successful_update: Optional[datetime] = None


# --- CONFIGURATION ---
MAX_PAST_GAMES_DISPLAY = 2
FETCH_TIMEOUT_SECONDS = 10
FETCH_RETRY_COUNT = 1
SHOW_SECTION_HEADERS = True

il_school_urls = {
    "BELLEVILLE_EAST": "https://www.maxpreps.com/il/belleville/belleville-east-lancers/basketball/schedule/",
    "O'FALLON": "https://www.maxpreps.com/il/ofallon/ofallon-panthers/basketball/schedule/",
    "MASCOUTAH": "https://www.maxpreps.com/il/mascoutah/mascoutah-indians/basketball/schedule/",
    "BELLEVILLE_WEST": "https://www.maxpreps.com/il/belleville/belleville-west-maroons/basketball/schedule/",
    "EAST_ST_LOUIS": "https://www.maxpreps.com/il/east-st-louis/east-st-louis-flyers/basketball/schedule/",
}

mo_school_urls = {
    "VASHON": "https://www.maxpreps.com/mo/st-louis/vashon-wolverines/basketball/schedule/",
}

college_urls = {
    "SLU": "https://www.espn.com/mens-college-basketball/team/_/id/139/saint-louis-billikens",
    "SIUE": "https://www.espn.com/mens-college-basketball/team/_/id/2565/siu-edwardsville-cougars",
    "ILLINOIS": "https://www.espn.com/mens-college-basketball/team/_/id/356/illinois-fighting-illini",
    "Lindenwood": "https://www.espn.com/mens-college-basketball/team/_/id/2815/lindenwood-lions"
}

SWIC_URL = "https://www.swic.edu/students/services/student-life/athletics/mens-basketball/"


# --- HELPERS ---
def get_basketball_season_year(date_str: str) -> int:
    """Determine year based on month. Season: Oct-Dec (current year), Jan-Jul (next year)."""
    try:
        if '/' in date_str:
            month = int(date_str.split('/')[0])
        else:
            month = datetime.strptime(date_str.split()[0][:3], "%b").month
    except ValueError:
        return datetime.now().year

    current_date = datetime.now()
    if month <= 6 and current_date.month >= 7:
        return current_date.year + 1
    elif month >= 10 and current_date.month <= 6:
        return current_date.year - 1

    return current_date.year


def get_current_season_slug() -> str:
    """Returns season string like '2025-26'. Rollover in Oct."""
    d = datetime.now()
    start_year = d.year if d.month >= 10 else d.year - 1
    end_year = start_year + 1
    return f"{start_year}-{str(end_year)[-2:]}"


def get_swic_record_url() -> str:
    """Generates SWIC record URL using the stable team slug."""
    return f"https://www.njcaaregion24.com/sports/mbkb/{get_current_season_slug()}/teams/southwesternillinoiscollege"


async def fetch_html(session: aiohttp.ClientSession, url: str) -> tuple[BeautifulSoup, Optional[str]]:
    """Fetch HTML with retry logic. Returns (soup, error_message)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"}
    
    for attempt in range(FETCH_RETRY_COUNT + 1):
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS)) as response:
                if response.status == 200:
                    return BeautifulSoup(await response.text(), 'html.parser'), None
                else:
                    error_msg = f"HTTP {response.status}"
                    if attempt < FETCH_RETRY_COUNT:
                        await asyncio.sleep(0.5 * (attempt + 1))  # Exponential backoff
                        continue
                    return BeautifulSoup("", 'html.parser'), error_msg
        except asyncio.TimeoutError:
            error_msg = "Timeout"
            if attempt < FETCH_RETRY_COUNT:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            return BeautifulSoup("", 'html.parser'), error_msg
        except Exception as e:
            error_msg = f"Error: {type(e).__name__}"
            if attempt < FETCH_RETRY_COUNT:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            return BeautifulSoup("", 'html.parser'), error_msg
    
    return BeautifulSoup("", 'html.parser'), "Unknown error"


def parse_tipoff_time(time_str: str) -> Optional[datetime]:
    """Robust time parser."""
    try:
        cleaned = time_str.strip().upper()
        for fmt in ('%I:%M %p', '%I:%M%p'):
            try:
                return datetime.strptime(cleaned, fmt)
            except ValueError:
                continue
        if not cleaned.endswith(('AM', 'PM')):
            cleaned = time_str.rstrip(string.ascii_letters).strip() + ' PM'
            return datetime.strptime(cleaned, '%I:%M %p')
    except ValueError:
        pass
    return None


def parse_home_away(text: str) -> str:
    if "@" in text: return "Away"
    if "vs" in text: return "Home"
    return "Neutral"


def format_relative_date(game_date: datetime) -> str:
    """Format game date as relative string (TODAY, TOMORROW, day name, or date)."""
    today = datetime.now().date()
    game_day = game_date.date()
    delta = (game_day - today).days
    
    if delta == 0:
        return "TODAY"
    elif delta == 1:
        return "TOMORROW"
    elif 2 <= delta <= 6:
        return game_date.strftime('%a').upper()  # MON, TUE, etc.
    else:
        return game_date.strftime('%b %d')  # Jan 25


async def parse_past_game_scores(session: aiohttp.ClientSession, game_url: str) -> tuple[Optional[str], Optional[str]]:
    """Parse game result and score from MaxPreps box score page. Returns (result, score)."""
    if not game_url or game_url.startswith("http") is False:
        return None, None
    
    try:
        soup, error = await fetch_html(session, game_url)
        if error:
            return None, None
        
        # Look for score elements in MaxPreps box score page
        # Format varies, but typically has team scores in specific divs
        score_divs = soup.select('.score')
        if len(score_divs) >= 2:
            team_score = score_divs[0].text.strip()
            opp_score = score_divs[1].text.strip()
            
            if team_score.isdigit() and opp_score.isdigit():
                result = "W" if int(team_score) > int(opp_score) else "L"
                score = f"{team_score}-{opp_score}"
                return result, score
        
        # Alternative: Check for win/loss text indicators
        result_text = soup.get_text()
        if "Win" in result_text or "Victory" in result_text:
            return "W", None
        elif "Loss" in result_text or "Defeat" in result_text:
            return "L", None
            
    except Exception:
        pass
    
    return None, None


# --- MAXPREPS LOGIC ---
async def process_school(session: aiohttp.ClientSession, schedule_url: str) -> School:
    school = School(url=schedule_url, last_updated=datetime.now())
    errors = []
    
    try:
        # 1. Fetch Schedule Page AND Rankings Page in parallel
        rankings_url = schedule_url.replace('/schedule/', '/rankings/')

        (soup_sched, err_sched), (soup_rank, err_rank) = await asyncio.gather(
            fetch_html(session, schedule_url),
            fetch_html(session, rankings_url)
        )
        
        if err_sched:
            errors.append(f"Schedule: {err_sched}")
        if err_rank:
            errors.append(f"Rankings: {err_rank}")

        # --- PARSE SCHEDULE (from Schedule Soup) ---
        all_tables = soup_sched.select("table tbody")
        schedule_table = all_tables[1] if len(all_tables) > 1 else (all_tables[0] if all_tables else None)
        if schedule_table and not err_sched:
            games = []
            for tr in schedule_table.find_all('tr'):
                tds = tr.find_all('td')
                if not tds or len(tds) < 4:
                    continue
                    
                try:
                    game_date = datetime.strptime(tds[0].text.strip(), '%m/%d').replace(
                        year=get_basketball_season_year(tds[0].text.strip()))
                    ha = parse_home_away(tds[1].text)
                    opp = tds[1].find('span', class_="name").text.rstrip('*').strip() if tds[1].find('span', class_="name") else ""
                    tipoff = parse_tipoff_time(tds[2].text.strip())
                    g_url = tds[2].find('a')['href'] if tds[2].find('a') else ""
                    
                    # Check if game is in the past - parse score if available
                    result = None
                    score = None
                    if len(tds) >= 4 and game_date.date() < datetime.now().date():
                        score_cell = tds[3].text.strip()
                        # MaxPreps shows "W 65-58" or "L 58-65" format
                        score_match = re.match(r'^([WL])\s*(\d+-\d+)', score_cell)
                        if score_match:
                            result = score_match.group(1)
                            score = score_match.group(2)
                    
                    games.append(Game(
                        date=game_date,
                        home_away=ha,
                        opponent=opp,
                        tipoff_time=tipoff,
                        game_url=g_url,
                        result=result,
                        score=score
                    ))
                except (ValueError, AttributeError):
                    continue
            
            school.schedule = games
            if not errors:
                school.last_successful_update = datetime.now()

        # Name & Record (from Schedule Soup)
        title = soup_sched.select_one('.sub-title')
        school.name = title.text if title else None

        # Get Record and Streak from Schedule Header JSON
        import json
        script_sched = soup_sched.find('script', id='__NEXT_DATA__')
        if script_sched:
            data = json.loads(script_sched.string)
            team_ctx = data.get('props', {}).get('pageProps', {}).get('teamContext', {}) or data.get('props', {}).get(
                'pageProps', {}).get('team', {})
            overall_standing = team_ctx.get('standingsData', {}).get('overallStanding', {})
            school.record = overall_standing.get('overallWinLossTies')
            school.streak = overall_standing.get('streak')
            school.streak_type = overall_standing.get('streakResult')

        # --- PARSE RANKINGS (from Rankings Soup) ---
        # Detect state from URL for proper labeling
        state_abbrev = "IL" if "/il/" in schedule_url.lower() else "MO" if "/mo/" in schedule_url.lower() else "IL"
        
        # We need the full rankings data which is usually better populated on the Rankings page
        script_rank = soup_rank.find('script', id='__NEXT_DATA__')

        state_rank = "NR"
        div_rank = "NR"
        stl_rank = "NR"

        found_data = False

        if script_rank:
            try:
                r_data = json.loads(script_rank.string)
                # Navigate deep into rankings data
                rank_ctx = r_data.get('props', {}).get('pageProps', {}).get('teamContext', {}) or r_data.get('props',
                                                                                                             {}).get(
                    'pageProps', {}).get('team', {})
                rank_list = rank_ctx.get('rankingsData', {}).get('data', [])

                for r in rank_list:
                    # 1. State Rank (Type 1)
                    if r.get('rankingType') == 1:
                        val = r.get('rank', 'NR')
                        state_rank = val
                        school.ranking = val  # Use State rank for sorting

                    # 2. Other Ranks (Check contextName, not name)
                    context_name = r.get('contextName', '')
                    val = r.get('rank', 'NR')

                    if 'Division' in context_name or 'Class' in context_name:
                        div_rank = val
                    elif 'St. Louis' in context_name:
                        stl_rank = val

                found_data = True
            except (KeyError, ValueError, AttributeError):
                pass

        # Fallback: Scrape HTML if JSON fails
        if not found_data:
            # Simple text search fallback for the soup
            text = soup_rank.get_text()
            # Try to find state ranking (Illinois or Missouri)
            state_pattern = r'(Illinois|Missouri)\s+#(\d+)'
            state_match = re.search(state_pattern, text)
            if state_match:
                state_rank = state_match.group(2)
                school.ranking = int(state_match.group(2))

            # Try to find "St. Louis #5"
            stl_match = re.search(r'St\. Louis\s+#(\d+)', text)
            if stl_match: stl_rank = stl_match.group(1)

        # Build formatted tooltip with state-specific labels
        tooltip_parts = []
        if state_rank != "NR": tooltip_parts.append(f"{state_abbrev}# {state_rank}")
        if div_rank != "NR": tooltip_parts.append(f"{state_abbrev} Div# {div_rank}")
        if stl_rank != "NR": tooltip_parts.append(f"STL# {stl_rank}")

        if tooltip_parts:
            school.rankings_tooltip = " | ".join(tooltip_parts)
        
        # Store any errors encountered
        if errors:
            school.fetch_error = "; ".join(errors)
        elif school.name:  # Only mark successful if we got basic data
            school.last_successful_update = datetime.now()

    except Exception as e:
        school.fetch_error = f"Parse error: {type(e).__name__}"
    
    return school


# --- SWIC LOGIC ---
async def extract_future_swic_games(session: aiohttp.ClientSession):
    school = School(name="SWIC", url=SWIC_URL, last_updated=datetime.now())
    errors = []
    
    try:
        soup, err_schedule = await fetch_html(session, SWIC_URL)
        if err_schedule:
            errors.append(f"Schedule: {err_schedule}")
        
        record_url = get_swic_record_url()

        # Get Record (Robust Regex)
        record_soup, err_record = await fetch_html(session, record_url)
        if err_record:
            errors.append(f"Record: {err_record}")
        
        record_match = re.search(r'Overall\s*:?\s*(\d+-\d+)', record_soup.get_text(), re.IGNORECASE)
        swic_record = record_match.group(1) if record_match else None

        games = []
        tbody = soup.find('tbody')
        if tbody and not err_schedule:
            for row in tbody.find_all('tr'):
                cols = row.find_all('td')
                if len(cols) >= 5:
                    d_str = cols[0].text.strip()
                    if not d_str or d_str.lower() == "date": continue
                    if '-' in d_str and d_str[0:3].isalpha(): d_str = d_str.split('-')[0].strip()

                    try:
                        full_date_str = f"{d_str} {get_basketball_season_year(d_str)}"
                        dt = datetime.strptime(full_date_str, "%b %d %Y")

                        loc = cols[3].text.strip()
                        is_home = any(x in loc for x in ['Belleville', 'SWIC', 'Sam Wolf'])
                        
                        # Include both past and future home games
                        if is_home:
                            result = None
                            score = None
                            # Check if game is in past - look for score in appropriate column
                            if dt.date() < datetime.now().date() and len(cols) >= 6:
                                score_text = cols[5].text.strip()
                                # SWIC format might be "W 75-60" or just score
                                score_match = re.match(r'^([WL])?\s*(\d+-\d+)', score_text)
                                if score_match:
                                    result = score_match.group(1) if score_match.group(1) else None
                                    score = score_match.group(2)
                            
                            games.append(Game(
                                date=dt,
                                home_away='Home',
                                opponent=cols[2].text.strip(),
                                tipoff_time=parse_tipoff_time(cols[4].text.strip()),
                                game_url=record_url,
                                result=result,
                                score=score
                            ))
                    except ValueError:
                        continue
        
        school.schedule = games
        school.record = swic_record
        
        if errors:
            school.fetch_error = "; ".join(errors)
        elif games or swic_record:
            school.last_successful_update = datetime.now()
        
        return school
        
    except Exception as e:
        school.fetch_error = f"Parse error: {type(e).__name__}"
        return school


# --- COLLEGE LOGIC ---
async def process_single_college(session: aiohttp.ClientSession, url: str) -> School:
    school = School(url=url, last_updated=datetime.now())
    try:
        soup, error = await fetch_html(session, url)
        if error:
            school.fetch_error = error
            return school
        
        name_el = soup.find('span', class_='db pr3 nowrap fw-bold')
        school.name = name_el.text if name_el else None

        rec_ul = soup.find('ul', class_='ClubhouseHeader__Record')
        if rec_ul:
            lis = rec_ul.find_all('li')
            if len(lis) > 0: school.record = lis[0].text
            if len(lis) > 1:
                match = re.search(r'#(\d+)', lis[1].text)
                if match: school.ranking = int(match.group(1))

        games = []
        for a in soup.find_all('a', class_='Schedule__Game', href=True):
            if a.find('span', class_='Schedule__Time'):
                try:
                    d_txt = a.find('span', class_='Schedule__Time').text.strip()
                    parsed_dt = datetime.strptime(f"{d_txt}/{get_basketball_season_year(d_txt)}", '%m/%d/%Y')

                    ha_span = a.find('span', class_='Schedule_atVs')
                    ha = parse_home_away(ha_span.text) if ha_span else "Neutral"

                    times = a.find_all('span', class_='Schedule__Time')
                    t_str = times[1].text.strip().upper() if len(times) > 1 else None
                    tipoff = datetime.strptime(t_str, "%I:%M %p") if (
                                ha == "Home" and t_str and parsed_dt >= datetime.now()) else None

                    # Check for score/result if game is in past
                    result = None
                    score = None
                    if parsed_dt.date() < datetime.now().date():
                        score_div = a.find('div', class_='Schedule__Score')
                        if score_div:
                            # ESPN typically shows "W 75-60" or "L 60-75"
                            score_text = score_div.text.strip()
                            score_match = re.match(r'^([WL])\s*(\d+-\d+)', score_text)
                            if score_match:
                                result = score_match.group(1)
                                score = score_match.group(2)

                    games.append(Game(
                        date=parsed_dt,
                        home_away=ha,
                        opponent=a.find('span', class_='Schedule__Team').text.strip().upper(),
                        tipoff_time=tipoff,
                        game_url=a['href'],
                        result=result,
                        score=score
                    ))
                except (ValueError, AttributeError):
                    continue
        
        school.schedule = games
        if school.name and games:
            school.last_successful_update = datetime.now()
            
    except Exception as e:
        school.fetch_error = f"Parse error: {type(e).__name__}"
    
    return school


# --- DISPLAY ---
def generate_swiftbar_menu(schools: list[School], rank_scope: str = "", section_header: str = "", games_with_fantastical: set = None) -> None:
    """Generate SwiftBar menu. games_with_fantastical is a set of game IDs that should show Fantastical links."""
    if not schools: return
    if games_with_fantastical is None:
        games_with_fantastical = set()

    sorted_schools = sorted(schools, key=lambda s: (s.ranking is None, s.ranking or float('inf')))

    # Show section header if configured
    if SHOW_SECTION_HEADERS and section_header:
        print("---")
        print(f"{section_header} | size=13 color=#888888")
    else:
        print("---")
    
    today = datetime.now().date()

    for s in sorted_schools:
        rank_txt = f"[{rank_scope} #{s.ranking}]" if (s.ranking and rank_scope) else (
            f"[#{s.ranking}]" if s.ranking else "")

        # UI: Win/Loss streak indicator
        streak_txt = ""
        if s.streak and s.streak_type:
            emoji = "🔥" if s.streak_type == "W" else "❄️"
            streak_txt = f"{emoji}{s.streak_type}{s.streak}"

        # UI: Orange dot if home game today
        has_game_today = any(g.home_away == "Home" and g.date.date() == today for g in s.schedule)
        dot = "🟠" if has_game_today else ""
        
        # UI: Warning icon if data fetch failed
        error_icon = "⚠️ " if s.fetch_error else ""

        # UI: Tooltip with extra rankings and error info
        tooltip_parts = []
        if s.rankings_tooltip:
            tooltip_parts.append(s.rankings_tooltip)
        if s.last_successful_update:
            tooltip_parts.append(f"Updated: {s.last_successful_update.strftime('%b %d %H:%M')}")
        if s.fetch_error:
            tooltip_parts.append(f"Error: {s.fetch_error}")
        
        tooltip = f' tooltip="{" | ".join(tooltip_parts)}"' if tooltip_parts else ""

        # Align columns for high schools (monospace font)
        # Format: [IL #123] School Name Mascot   (5-1) 🔥W5 🟠
        if rank_scope:  # High schools with rankings
            rank_col = rank_txt.ljust(10)  # "[IL #328]"
            name_col = (s.name or "Unknown").ljust(24)  # "School Name Mascot  "
            rec_col = f"({s.record})" if s.record else ""
            rec_col = rec_col.ljust(6)  # "(5-1) "
            streak_col = streak_txt.ljust(4)  # "🔥W5"

            display_text = f"{error_icon}{rank_col} {name_col} {rec_col} {streak_col} {dot}".strip()
        else:  # Colleges/SWIC - align records with high schools
            # High schools: rank(10) + space + name(24) + space = 36 chars before record
            name_col = (s.name or "Unknown").ljust(35)  # Align at column 36 with high schools
            rec_col = f"({s.record})" if s.record else ""
            rec_col = rec_col.ljust(6)  # "(5-1) "
            streak_col = streak_txt.ljust(4)  # Streak indicator

            display_text = f"{error_icon}{name_col} {rec_col} {streak_col} {dot}".strip()

        print(f"{display_text} | href={s.url} font=Menlo-Bold{tooltip}")
        
        # Show count of upcoming home games
        upcoming_home = [g for g in s.schedule if g.home_away == "Home" and g.date.date() >= today]
        if upcoming_home:
            print(f"--{len(upcoming_home)} upcoming home game{'s' if len(upcoming_home) != 1 else ''} | size=11 color=#666666")
        
        # Show past games (most recent first, limited)
        past_games = [g for g in s.schedule if g.home_away == "Home" and g.date.date() < today]
        past_games.sort(key=lambda g: g.date, reverse=True)
        
        for g in past_games[:MAX_PAST_GAMES_DISPLAY]:
            result_emoji = "✅" if g.result == "W" else "❌" if g.result == "L" else "📊"
            score_text = f" {g.score}" if g.score else ""
            result_text = f" ({g.result})" if g.result else ""
            msg = f"{g.date.strftime('%b %d')}: {g.opponent}{result_text}{score_text}"
            print(f'--{result_emoji} {msg} | href={g.game_url or ""} size=11 color=#888888')

        # Show upcoming home games with relative dates
        for g in upcoming_home:
            display_time = f"@ {g.tipoff_time.strftime('%H%M')}" if g.tipoff_time else "(TBD)"
            relative_date = format_relative_date(g.date)
            msg = f"{relative_date}: {g.opponent} {display_time}"

            # UI: Highlight today's game
            is_today = g.date.date() == today
            prefix = "**" if is_today else ""
            suffix = "**" if is_today else ""
            color = " color=#FFA500" if is_today else ""

            print(f'--{prefix}{msg}{suffix} | href={g.game_url or ""} md=true{color}')

            # Create unique game ID and check if this game should have Fantastical link
            game_id = (s.name, g.date, g.opponent)
            if g.tipoff_time and game_id in games_with_fantastical:
                title = f'"{g.opponent} at {s.name}"'
                appt = f"{g.date.strftime('%Y/%m/%d')} at {g.tipoff_time.strftime('%H%M')} {title} at {s.name}"
                print(
                    f'----Add to Fantastical | href=x-fantastical3://parse?add=1&sentence={quote(appt)} terminal=false')


# --- MAIN ---
async def main():
    async with aiohttp.ClientSession() as session:
        il_hs_tasks = [process_school(session, u) for u in il_school_urls.values()]
        mo_hs_tasks = [process_school(session, u) for u in mo_school_urls.values()]
        coll_tasks = [process_single_college(session, u) for u in college_urls.values()]

        il_hs_res, mo_hs_res, coll_res, swic_res = await asyncio.gather(
            asyncio.gather(*il_hs_tasks, return_exceptions=True),
            asyncio.gather(*mo_hs_tasks, return_exceptions=True),
            asyncio.gather(*coll_tasks, return_exceptions=True),
            extract_future_swic_games(session)
        )

        valid_il_hs = [r for r in il_hs_res if isinstance(r, School)]
        valid_mo_hs = [r for r in mo_hs_res if isinstance(r, School)]
        valid_coll = [r for r in coll_res if isinstance(r, School)]
        valid_swic = [swic_res] if isinstance(swic_res, School) else []

        # -- MENU BAR LOGIC --
        # Always show SF Symbol in menu bar (no game details)
        print("􁔛")

        # -- DROPDOWN --
        # Identify the next 5 games chronologically that should have Fantastical links
        MAX_FANTASTICAL_LINKS = 5
        games_for_fantastical = []

        for s in valid_il_hs + valid_mo_hs + valid_coll + valid_swic:
            for g in s.schedule:
                if g.home_away == "Home" and g.date.date() >= datetime.now().date() and g.tipoff_time:
                    games_for_fantastical.append((s.name, g.date, g.opponent))

        # Sort by date and take first N
        games_for_fantastical.sort(key=lambda x: x[1])
        games_with_fantastical = set(games_for_fantastical[:MAX_FANTASTICAL_LINKS])

        generate_swiftbar_menu(valid_il_hs, "IL", "ILLINOIS HIGH SCHOOLS", games_with_fantastical)
        generate_swiftbar_menu(valid_mo_hs, "MO", "MISSOURI HIGH SCHOOLS", games_with_fantastical)
        if valid_swic: generate_swiftbar_menu(valid_swic, "", "COMMUNITY COLLEGE", games_with_fantastical)
        generate_swiftbar_menu(valid_coll, "", "DIVISION I", games_with_fantastical)


if __name__ == '__main__':
    asyncio.run(main())
