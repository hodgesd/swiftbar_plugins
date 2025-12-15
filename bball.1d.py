#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
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


@dataclass
class School:
    url: str
    last_updated: datetime
    name: Optional[str] = None
    mascot: Optional[str] = None
    record: Optional[str] = None
    ranking: Optional[int] = None
    rankings_tooltip: Optional[str] = None
    schedule: list[Game] = field(default_factory=list)


# --- CONFIGURATION ---
school_urls = {
    "BELLEVILLE_EAST": "https://www.maxpreps.com/il/belleville/belleville-east-lancers/basketball/schedule/",
    "O'FALLON": "https://www.maxpreps.com/il/ofallon/ofallon-panthers/basketball/schedule/",
    "MASCOUTAH": "https://www.maxpreps.com/il/mascoutah/mascoutah-indians/basketball/schedule/",
    "BELLEVILLE_WEST": "https://www.maxpreps.com/il/belleville/belleville-west-maroons/basketball/schedule/",
    "EAST_ST_LOUIS": "https://www.maxpreps.com/il/east-st-louis/east-st-louis-flyers/basketball/schedule/",
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


async def fetch_html(session: aiohttp.ClientSession, url: str) -> BeautifulSoup:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
            return BeautifulSoup(await response.text(), 'html.parser')
    except Exception:
        return BeautifulSoup("", 'html.parser')


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


# --- MAXPREPS LOGIC ---
async def process_school(session: aiohttp.ClientSession, schedule_url: str) -> School:
    school = School(url=schedule_url, last_updated=datetime.now())
    try:
        # 1. Fetch Schedule Page AND Rankings Page in parallel
        rankings_url = schedule_url.replace('/schedule/', '/rankings/')

        soup_sched, soup_rank = await asyncio.gather(
            fetch_html(session, schedule_url),
            fetch_html(session, rankings_url)
        )

        # --- PARSE SCHEDULE (from Schedule Soup) ---
        all_tables = soup_sched.select("table tbody")
        schedule_table = all_tables[1] if len(all_tables) > 1 else (all_tables[0] if all_tables else None)
        if schedule_table:
            school.schedule = [
                Game(
                    date=datetime.strptime(tds[0].text.strip(), '%m/%d').replace(
                        year=get_basketball_season_year(tds[0].text.strip())),
                    home_away=parse_home_away(tds[1].text),
                    opponent=tds[1].find('span', class_="name").text.rstrip('*').strip() if tds[1].find('span',
                                                                                                        class_="name") else "",
                    tipoff_time=parse_tipoff_time(tds[2].text.strip()),
                    game_url=tds[2].find('a')['href'] if tds[2].find('a') else ""
                )
                for tr in schedule_table.find_all('tr')
                if (tds := tr.find_all('td')) and len(tds) >= 4
            ]

        # Name & Record (from Schedule Soup)
        title = soup_sched.select_one('.sub-title')
        school.name = title.text if title else None

        # Get Record from Schedule Header JSON
        import json
        script_sched = soup_sched.find('script', id='__NEXT_DATA__')
        if script_sched:
            data = json.loads(script_sched.string)
            team_ctx = data.get('props', {}).get('pageProps', {}).get('teamContext', {}) or data.get('props', {}).get(
                'pageProps', {}).get('team', {})
            school.record = team_ctx.get('standingsData', {}).get('overallStanding', {}).get('overallWinLossTies')

        # --- PARSE RANKINGS (from Rankings Soup) ---
        # We need the full rankings data which is usually better populated on the Rankings page
        script_rank = soup_rank.find('script', id='__NEXT_DATA__')

        il_rank = "NR"
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
                        il_rank = val
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
            # Try to find "Illinois #5" pattern
            il_match = re.search(r'Illinois\s+#(\d+)', text)
            if il_match:
                il_rank = il_match.group(1)
                school.ranking = int(il_match.group(1))

            # Try to find "St. Louis #5"
            stl_match = re.search(r'St\. Louis\s+#(\d+)', text)
            if stl_match: stl_rank = stl_match.group(1)

        # Build formatted tooltip: "IL# xxx | IL Div# yy | STL # xx"
        # Only include if rank exists (not NR) to keep it clean, or show NR if preferred.
        # User requested specific format.
        tooltip_parts = []
        if il_rank != "NR": tooltip_parts.append(f"IL# {il_rank}")
        if div_rank != "NR": tooltip_parts.append(f"IL Div# {div_rank}")
        if stl_rank != "NR": tooltip_parts.append(f"STL# {stl_rank}")

        if tooltip_parts:
            school.rankings_tooltip = " | ".join(tooltip_parts)

    except Exception:
        pass
    return school


# --- SWIC LOGIC ---
async def extract_future_swic_games(session: aiohttp.ClientSession):
    try:
        soup = await fetch_html(session, SWIC_URL)
        record_url = get_swic_record_url()

        # Get Record (Robust Regex)
        record_soup = await fetch_html(session, record_url)
        record_match = re.search(r'Overall\s*:?\s*(\d+-\d+)', record_soup.get_text(), re.IGNORECASE)
        swic_record = record_match.group(1) if record_match else None

        games = []
        tbody = soup.find('tbody')
        if tbody:
            for row in tbody.find_all('tr'):
                cols = row.find_all('td')
                if len(cols) >= 5:
                    d_str = cols[0].text.strip()
                    if not d_str or d_str.lower() == "date": continue
                    if '-' in d_str and d_str[0:3].isalpha(): d_str = d_str.split('-')[0].strip()

                    try:
                        full_date_str = f"{d_str} {get_basketball_season_year(d_str)}"
                        dt = datetime.strptime(full_date_str, "%b %d %Y")

                        if dt.date() >= datetime.now().date():
                            loc = cols[3].text.strip()
                            is_home = any(x in loc for x in ['Belleville', 'SWIC', 'Sam Wolf'])
                            if is_home:
                                games.append(Game(
                                    date=dt,
                                    home_away='Home',
                                    opponent=cols[2].text.strip(),
                                    tipoff_time=parse_tipoff_time(cols[4].text.strip()),
                                    game_url=record_url
                                ))
                    except ValueError:
                        continue

        return School(name="SWIC", url=SWIC_URL, last_updated=datetime.now(), schedule=games, record=swic_record)
    except Exception:
        return School(name="SWIC", url=SWIC_URL, last_updated=datetime.now())


# --- COLLEGE LOGIC ---
async def process_single_college(session: aiohttp.ClientSession, url: str) -> School:
    school = School(url=url, last_updated=datetime.now())
    try:
        soup = await fetch_html(session, url)
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
                d_txt = a.find('span', class_='Schedule__Time').text.strip()
                parsed_dt = datetime.strptime(f"{d_txt}/{get_basketball_season_year(d_txt)}", '%m/%d/%Y')

                ha_span = a.find('span', class_='Schedule_atVs')
                ha = parse_home_away(ha_span.text) if ha_span else "Neutral"

                times = a.find_all('span', class_='Schedule__Time')
                t_str = times[1].text.strip().upper() if len(times) > 1 else None
                tipoff = datetime.strptime(t_str, "%I:%M %p") if (
                            ha == "Home" and t_str and parsed_dt >= datetime.now()) else None

                games.append(Game(
                    date=parsed_dt,
                    home_away=ha,
                    opponent=a.find('span', class_='Schedule__Team').text.strip().upper(),
                    tipoff_time=tipoff,
                    game_url=a['href']
                ))
        school.schedule = games
    except Exception:
        pass
    return school


# --- DISPLAY ---
def generate_swiftbar_menu(schools: list[School], rank_scope: str = "") -> None:
    if not schools: return
    sorted_schools = sorted(schools, key=lambda s: (s.ranking is None, s.ranking or float('inf')))

    print("---")
    today = datetime.now().date()

    for s in sorted_schools:
        rank_txt = f"[{rank_scope} #{s.ranking}] " if (s.ranking and rank_scope) else (
            f"[#{s.ranking}] " if s.ranking else "")
        rec_txt = f" ({s.record})" if s.record else ""

        # UI: Orange dot if home game today
        has_game_today = any(g.home_away == "Home" and g.date.date() == today for g in s.schedule)
        dot = " 🟠" if has_game_today else ""

        # UI: Tooltip with extra rankings
        # Using " | " separator as requested
        tooltip = f' tooltip="{s.rankings_tooltip}"' if s.rankings_tooltip else ""

        print(f"{rank_txt}{s.name or ''}{rec_txt}{dot} | href={s.url} font=Menlo-Bold{tooltip}")

        for g in s.schedule:
            if g.home_away == "Home" and g.date.date() >= today:
                display_time = f"@ {g.tipoff_time.strftime('%H%M')}" if g.tipoff_time else "(TBD)"
                msg = f"{g.date.strftime('%a, %b %d')}: {g.opponent} {display_time}"

                # UI: Highlight today's game
                is_today = g.date.date() == today
                prefix = "**" if g.date >= datetime.now() else ""
                suffix = "**" if g.date >= datetime.now() else ""
                color = " color=#FFA500" if is_today else ""

                print(f'--{prefix}{msg}{suffix} | href={g.game_url or ""} md=true{color}')

                if g.tipoff_time:
                    title = f'"{g.opponent} at {s.name}"'
                    appt = f"{g.date.strftime('%Y/%m/%d')} at {g.tipoff_time.strftime('%H%M')} {title} at {s.name}"
                    print(
                        f'----Add to Fantastical | href=x-fantastical3://parse?add=1&sentence={quote(appt)} terminal=false')


# --- MAIN ---
async def main():
    async with aiohttp.ClientSession() as session:
        hs_tasks = [process_school(session, u) for u in school_urls.values()]
        coll_tasks = [process_single_college(session, u) for u in college_urls.values()]

        hs_res, coll_res, swic_res = await asyncio.gather(
            asyncio.gather(*hs_tasks, return_exceptions=True),
            asyncio.gather(*coll_tasks, return_exceptions=True),
            extract_future_swic_games(session)
        )

        valid_hs = [r for r in hs_res if isinstance(r, School)]
        valid_coll = [r for r in coll_res if isinstance(r, School)]
        valid_swic = [swic_res] if isinstance(swic_res, School) else []

        # -- MENU BAR LOGIC --
        # Find the very next home game across all schools
        all_games = []
        for s in valid_hs + valid_coll + valid_swic:
            for g in s.schedule:
                if g.home_away == "Home" and g.date.date() >= datetime.now().date():
                    all_games.append(g)

        all_games.sort(key=lambda x: x.date)

        # If the next game is TODAY, show it in the menu bar
        if all_games and all_games[0].date.date() == datetime.now().date():
            next_g = all_games[0]
            t_str = next_g.tipoff_time.strftime('%H%M') if next_g.tipoff_time else "TBD"
            print(f"🏀 vs {next_g.opponent} @ {t_str}")
        else:
            print("🏀")

        # -- DROPDOWN --
        generate_swiftbar_menu(valid_hs, "IL")
        if valid_swic: generate_swiftbar_menu(valid_swic, "")
        generate_swiftbar_menu(valid_coll, "Conf")


if __name__ == '__main__':
    asyncio.run(main())
