#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
import asyncio
import re
import string
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

import aiohttp
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel


# <bitbar.title>Max Preps Basketball Schedule</bitbar.title>
# <bitbar.author>hodgesd</bitbar.author>
# <bitbar.author.github>hodgesd</bitbar.author.github>
# <bitbar.desc>Display the local preps basketball schedules/ranking/records</bitbar.desc>
# <bitbar.dependencies>python,aiohttp</bitbar.dependencies>
# <bitbar.version>2.0</bitbar.version>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>


class Game(BaseModel):
    date: datetime
    home_away: str
    opponent: str
    tipoff_time: Optional[datetime]
    game_url: str = None


class School(BaseModel):
    name: Optional[str] = None
    mascot: Optional[str] = None
    url: Optional[str] = None
    last_updated: datetime
    record: Optional[str] = None
    ranking: Optional[int] = None
    schedule: Optional[list[Game]] = None


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

# Constants for filtering
DAYS_TO_SHOW = 30  # Show games within next 30 days


def get_current_basketball_season() -> str:
    """Returns current basketball season in format '2024-25' based on current date."""
    current_date = datetime.now()
    # Basketball season runs roughly Nov-Mar, so if we're in Nov-Dec use current year,
    # if Jan-Oct use previous year as start year
    if current_date.month >= 11:  # November or December
        start_year = current_date.year
    else:
        start_year = current_date.year - 1
    end_year = start_year + 1
    return f"{start_year}-{str(end_year)[-2:]}"


def get_swic_record_url() -> str:
    """Generates SWIC record URL with current season."""
    season = get_current_basketball_season()
    return f"https://www.njcaa.org/sports/mbkb/{season}/div1/schedule?teamId=mjvavx3krm8kh0zb"


async def fetch_html(session: aiohttp.ClientSession, url: str) -> BeautifulSoup:
    """Fetches HTML content from a given URL and returns a BeautifulSoup object."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
    }
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
            text = await response.text()
            return BeautifulSoup(text, 'html.parser')
    except Exception as e:
        # Return empty soup on error
        return BeautifulSoup("", 'html.parser')


def extract_ranking_number(ranking_str: str) -> Optional[int]:
    """
    Extracts the numeric ranking from a string that might contain additional information.
    Returns None if no valid ranking can be extracted.
    """
    if not ranking_str:
        return None

    # If it contains a parenthesis (like "0-0(1st)"), just return None
    if '(' in ranking_str:
        return None

    # Try to extract just numeric values
    try:
        return int(ranking_str)
    except (ValueError, TypeError):
        return None


async def fetch_school_data(session: aiohttp.ClientSession, school: School) -> None:
    """Fetch school data from MaxPreps with error handling."""
    try:
        soup = await fetch_html(session, school.url)

        def extract_text(selector: str) -> Optional[str]:
            """Extracts text from a given selector."""
            element = soup.select_one(selector)
            return element.get_text(strip=True) if element else None

        all_tables = soup.select("table tbody")

        # Use only the future games table (table 1) which has times in td[2]
        # Table 0 has past games with different column structure
        if len(all_tables) > 1:
            future_schedule_html = all_tables[1]
            school.schedule = parse_schedule(future_schedule_html)
        elif len(all_tables) == 1:
            # Fallback to single table if structure changes
            future_schedule_html = all_tables[0]
            school.schedule = parse_schedule(future_schedule_html)

        # Get school name from the title element
        title_div = soup.select_one('.sub-title')
        school.name = title_div.text if title_div else None

        # Get record from the record section
        record_div = soup.select_one('.record div.block:first-child .data')
        school.record = record_div.text if record_div else None

        # Get conference standing - this includes ranking info
        conf_div = soup.select_one('.record div.block:nth-child(2) .data')
        conf_text = conf_div.text if conf_div else None

        # Try to extract ranking if it exists
        ranking = None
        if conf_text:
            try:
                if '(' in conf_text:
                    ranking_text = conf_text.split('(')[0].strip()
                    if ranking_text.isdigit():
                        ranking = int(ranking_text)
            except (ValueError, IndexError):
                pass

        school.ranking = ranking
    except Exception as e:
        # On error, leave school with minimal data (already has URL and timestamp)
        pass

def parse_schedule(schedule_tag: Tag) -> list[Game]:
    return [
        Game(
            date=parse_date(tds[0].text.strip()),
            home_away=parse_home_away(tds[1].text),
            opponent=extract_opponent(tds[1]),
            tipoff_time=parse_tipoff_time(tds[2].text.strip()),
            game_url=parse_game_url(tds[2])
        )
        for tr in schedule_tag.find_all('tr')
        if (tds := tr.find_all('td')) and len(tds) >= 4
    ]


def parse_game_url(game_td: Tag):
    a_tag = game_td.find('a')
    if a_tag and a_tag.has_attr('href'):
        return a_tag['href']
    return ""


def parse_date(date_str: str) -> datetime:
    parse_year = get_basketball_season_year(date_str)
    parsed_date = datetime.strptime(date_str, '%m/%d').replace(year=parse_year)
    return parsed_date


def parse_tipoff_time(time_str: str) -> Optional[datetime]:
    """Parse tipoff time from various formats like '7:30pm', '7:30 PM', or '7:30'."""
    try:
        cleaned = time_str.strip().upper()

        # Try parsing with space first: "7:30 PM"
        try:
            return datetime.strptime(cleaned, '%I:%M %p')
        except ValueError:
            pass

        # Try parsing without space: "7:30PM"
        try:
            return datetime.strptime(cleaned, '%I:%M%p')
        except ValueError:
            pass

        # If no AM/PM suffix, add PM and try again
        if not cleaned.endswith(('AM', 'PM')):
            cleaned = time_str.rstrip(string.ascii_letters).strip() + ' PM'
            return datetime.strptime(cleaned, '%I:%M %p')

        return None
    except ValueError:
        return None


def parse_home_away(text: str) -> str:
    if "@" in text:
        return "Away"
    elif "vs" in text:
        return "Home"
    return "Neutral"


def extract_opponent(td: Tag) -> str:
    opponent_element = td.find('span', class_="name")
    return opponent_element.text.rstrip('*').strip() if opponent_element else ""


def generate_swiftbar_menu(list_of_schools: list[School], rank_scope: str = "") -> None:
    """Generate SwiftBar menu with date filtering for upcoming games."""
    sorted_schools = sort_schools(list_of_schools)
    print("---")

    # Calculate date range for filtering
    today = datetime.now().date()
    max_date = today + timedelta(days=DAYS_TO_SHOW)

    for school in sorted_schools:
        ranking_text = f"[{rank_scope} #{school.ranking}]" if school.ranking is not None else ""
        school_name = school.name or ""
        school_record = f"({school.record})" if school.record else ""
        print(f"{ranking_text} {school_name} {school_record} | href = {school.url}")

        if school.schedule:
            for game in school.schedule:
                game_date = game.date.date()
                # Filter: home games, today or future, within DAYS_TO_SHOW window
                if game.home_away == "Home" and today <= game_date <= max_date:
                    # Show time if available, otherwise show TBD
                    time_str = game.tipoff_time.strftime('%I:%M %p') if game.tipoff_time else 'TBD'
                    game_message = f"{game.date.strftime('%a, %b %d')}: {game.opponent} {time_str}"
                    print(
                        f'--{bold_future(game.date, game_message)} | href = {game.game_url if game.game_url else ""} md=true')

                    # Only show Fantastical option if we have an actual time
                    if game.tipoff_time:
                        appointment_str = f"{game.date.strftime('%Y/%m/%d')} at {game.tipoff_time.strftime('%H%M')} {game.opponent} vs {school_name} at {school_name}"
                        appointment_url_scheme = f'x-fantastical3://parse?add=1&sentence={quote(appointment_str)}'
                        print(f'----Add to Fantastical | href = {appointment_url_scheme} terminal=false')


async def process_school(session: aiohttp.ClientSession, url: str) -> School:
    """Process a single school asynchronously."""
    school = School(url=url, last_updated=datetime.now())
    await fetch_school_data(session, school)
    return school


def sort_schools(schools: list[School]) -> list[School]:
    if not schools:
        return []
    return sorted(schools, key=lambda school: (school.ranking is None, school.ranking or float('inf')))


async def scrape_schools_data(session: aiohttp.ClientSession, urls: dict[str, str]) -> list[School]:
    """Scrape all schools concurrently."""
    tasks = [process_school(session, url) for url_str, url in urls.items()]
    return await asyncio.gather(*tasks, return_exceptions=True)


def get_month_number(date_string: str) -> int:
    if '/' in date_string:
        month_str, day_str = date_string.split("/")
        return int(month_str)
    else:
        month, day = date_string.split()
        return datetime.strptime(month, "%b").month


def get_basketball_season_year(date_str: str) -> int:
    """
    Determine the year for a basketball game date.
    Basketball season runs Nov-Mar, spanning two calendar years.
    """
    current_date = datetime.now()
    month_number = get_month_number(date_str)
    current_month = current_date.month

    # Basketball season: Nov(11), Dec(12), Jan(1), Feb(2), Mar(3)
    # If game is in Jan-Jun and we're currently in Jul-Dec, game is next year
    if month_number <= 6 and current_month >= 7:
        return current_date.year + 1
    # If game is in Nov-Dec and we're currently in Jan-Jun, game was last year
    elif month_number >= 11 and current_month <= 6:
        return current_date.year - 1
    # Otherwise, game is in current year
    else:
        return current_date.year


async def extract_future_swic_games(session: aiohttp.ClientSession):
    """Extract SWIC games with error handling."""
    try:
        soup = await fetch_html(session, SWIC_URL)
        games = []
        current_date = datetime.now()

        swic_record_url = get_swic_record_url()
        swic_record = await extract_overall_record(session, swic_record_url)

        tbody = soup.find('tbody')
        if tbody:
            for row in tbody.find_all('tr'):
                cols = row.find_all('td')
                if len(cols) == 6:
                    date_str = cols[0].text.strip()

                    # Handle date ranges like "Oct 4-5 2024" by extracting the first date
                    if '-' in date_str and date_str[0:3].isalpha():  # Check if it's a month abbreviation
                        # Split on the dash and take the first part
                        parts = date_str.split('-')
                        month_day = parts[0].strip()  # e.g., "Oct 4"
                        date_str = month_day

                    date_str += f" {get_basketball_season_year(date_str)}"

                    try:
                        date = datetime.strptime(date_str, "%b %d %Y")
                        if date.date() >= current_date.date():
                            location = cols[3].text.strip()
                            home_away = 'Home' if location == 'Belleville' else 'Away'
                            tipoff_time = None

                            if home_away == 'Home':
                                tipoff_time_str = cols[4].text.strip()
                                time_format = "%I:%M%p" if tipoff_time_str.endswith(("AM", "PM")) else "%I:%M"
                                tipoff_time = datetime.strptime(tipoff_time_str, time_format) if tipoff_time_str else None

                            game = Game(
                                date=date,
                                home_away=home_away,
                                opponent=cols[2].text.strip(),
                                tipoff_time=tipoff_time,
                                game_url=swic_record_url
                            )
                            games.append(game)
                    except ValueError as e:
                        # Skip rows with unparseable dates
                        continue

        return School(
            name="SWIC",
            url=SWIC_URL,
            last_updated=datetime.now(),
            schedule=games,
            record=swic_record
        )
    except Exception as e:
        # Return empty school on error
        return School(
            name="SWIC",
            url=SWIC_URL,
            last_updated=datetime.now(),
            schedule=[],
            record=None
        )


async def extract_overall_record(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    """Extract overall record from NJCAA page with error handling."""
    try:
        soup = await fetch_html(session, url)
        overall_record_element = soup.find('span', class_='label', string='Overall')
        if overall_record_element:
            overall_record_value = overall_record_element.find_next_sibling('span', class_='value')
            if overall_record_value:
                return overall_record_value.get_text(strip=True)
    except Exception as e:
        pass
    return None


def bold_future(game_date: datetime, message: str) -> str:
    if game_date >= datetime.now():
        return f"**{message}**"
    else:
        return message


def extract_future_college_games(soup) -> list[Game]:
    """Extract college games from ESPN page."""
    games = []
    try:
        games_html = soup.find_all('a', class_='Schedule__Game', href=True)
        for a_tag in games_html:
            if a_tag.find('span', class_='Schedule__Time'):
                opponent_span = a_tag.find('span', class_='Schedule__Team').text.strip().upper()
                game_url = a_tag['href']
                date_span = a_tag.find('span', class_='Schedule__Time').text.strip()
                year = get_basketball_season_year(date_span)
                parsed_date = datetime.strptime(f"{date_span}/{year}", '%m/%d/%Y')
                home_away_span = a_tag.find('span', class_='Schedule_atVs tl mr2')
                home_away = parse_home_away(home_away_span.text.strip() if home_away_span else "")
                time_span_elements = a_tag.find_all('span', class_='Schedule__Time')
                time_span = time_span_elements[1].text.strip().upper() if len(time_span_elements) > 1 else None
                tipoff = datetime.strptime(time_span,
                                           "%I:%M %p") if home_away == "Home" and parsed_date >= datetime.now() and time_span else None

                games.append(Game(
                    date=parsed_date,
                    home_away=home_away,
                    opponent=opponent_span,
                    tipoff_time=tipoff,
                    game_url=game_url
                ))
    except Exception:
        pass
    return games


async def process_single_college(session: aiohttp.ClientSession, url: str) -> School:
    """Process a single college asynchronously with error handling."""
    try:
        soup = await fetch_html(session, url)
        name_element = soup.find('span', class_='db pr3 nowrap fw-bold')
        school_name = name_element.text if name_element else None

        flex_wrap = soup.find('span', class_='flex flex-wrap')
        mascot = None
        if flex_wrap:
            db_spans = flex_wrap.find_all('span', class_='db')
            if len(db_spans) > 1:
                mascot = db_spans[1].text

        record = None
        ranking = None
        record_ranking_ul = soup.find('ul', class_='ClubhouseHeader__Record')
        if record_ranking_ul:
            li_elements = record_ranking_ul.find_all('li')
            if len(li_elements) > 0:
                record = li_elements[0].text
            if len(li_elements) > 1:
                ranking_text = li_elements[1].text
                ranking_match = re.search(r'#(\d+)', ranking_text)
                if ranking_match:
                    ranking = int(ranking_match.group(1))

        schedule = extract_future_college_games(soup)

        return School(
            url=url,
            last_updated=datetime.now(),
            name=school_name,
            mascot=mascot,
            record=record,
            ranking=ranking,
            schedule=schedule
        )
    except Exception:
        return School(
            url=url,
            last_updated=datetime.now(),
            name=None,
            mascot=None,
            record=None,
            ranking=None,
            schedule=[]
        )


async def process_colleges(session: aiohttp.ClientSession, college_urls: dict[str, str]) -> list[School]:
    """Process all colleges concurrently."""
    tasks = [process_single_college(session, url) for url in college_urls.values()]
    return await asyncio.gather(*tasks, return_exceptions=True)


async def main():
    """Main async function to fetch and display all basketball data."""
    print("􁗉")

    async with aiohttp.ClientSession() as session:
        # Fetch all data sources concurrently
        schools_task = scrape_schools_data(session, school_urls)
        swic_task = extract_future_swic_games(session)
        colleges_task = process_colleges(session, college_urls)

        schools, swic, college_list = await asyncio.gather(
            schools_task,
            swic_task,
            colleges_task,
            return_exceptions=True
        )

        # Filter out any exceptions and display valid results
        if isinstance(schools, list):
            # Filter out any School objects that are exceptions
            valid_schools = [s for s in schools if isinstance(s, School)]
            generate_swiftbar_menu(valid_schools, "IL")

        if isinstance(swic, School):
            generate_swiftbar_menu([swic], "")

        if isinstance(college_list, list):
            # Filter out any School objects that are exceptions
            valid_colleges = [c for c in college_list if isinstance(c, School)]
            generate_swiftbar_menu(valid_colleges, "Conf")


if __name__ == '__main__':
    asyncio.run(main())
