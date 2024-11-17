#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
import re
import string
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel


# <bitbar.title>Max Preps Basketball Schedule</bitbar.title>
# <bitbar.author>hodgesd</bitbar.author>
# <bitbar.author.github>hodgesd</bitbar.author.github>
# <bitbar.desc>Display the local preps basketball schedules/ranking/records</bitbar.desc>
# <bitbar.dependencies>python</bitbar.dependencies>
# <bitbar.version>1.0</bitbar.version>
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
SWIC_RECORD_URL = "https://www.njcaa.org/sports/mbkb/2023-24/div1/schedule?teamId=mjvavx3krm8kh0zb"


def fetch_html(url: str) -> BeautifulSoup:
    """Fetches HTML content from a given URL and returns a BeautifulSoup object."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
    }
    response = requests.get(url, headers=headers)
    return BeautifulSoup(response.text, 'html.parser')


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


def fetch_school_data(school: School) -> None:
    soup = fetch_html(school.url)

    def extract_text(selector: str) -> Optional[str]:
        """Extracts text from a given selector."""
        element = soup.select_one(selector)
        return element.get_text(strip=True) if element else None

    all_tables = soup.select("table tbody")

    if len(all_tables) > 1:
        for tr in all_tables[1].find_all('tr'):
            all_tables[0].append(tr)

    future_schedule_html = all_tables[0]
    if future_schedule_html:
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
    try:
        time_str.rstrip(string.ascii_letters) + " PM"
        return datetime.strptime(time_str.upper(), '%I:%M%p')
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
    sorted_schools = sort_schools(list_of_schools)
    print("---")

    for school in sorted_schools:
        ranking_text = f"[{rank_scope} #{school.ranking}]" if school.ranking is not None else ""
        school_name = school.name or ""
        school_record = f"({school.record})" if school.record else ""
        print(f"{ranking_text} {school_name} {school_record} | href = {school.url}")
        if school.schedule:
            for game in school.schedule:
                if game.home_away == "Home" and game.date.date() >= datetime.now().date():
                    game_message = f"{game.date.strftime('%a, %b %d')}: {game.opponent} {game.tipoff_time.strftime('%I:%M %p') if game.tipoff_time else ''}"
                    game_message = game_message.strip()
                    print(
                        f'--{bold_future(game.date, game_message)} | href = {game.game_url if game.game_url else ""} md=true')
                    appointment_str = f"{game.date.strftime('%Y/%m/%d')} at {game.tipoff_time.strftime('%H%M') if game.tipoff_time else ''} {game.opponent} vs {school_name} at {school_name}"
                    appointment_url_scheme = f'x-fantastical3://parse?add=1&sentence={quote(appointment_str)}'
                    print(f'----Add to Fantastical | href = {appointment_url_scheme} terminal=false')


def process_school(url: str) -> School:
    school = School(url=url, last_updated=datetime.now())
    fetch_school_data(school)
    return school


def sort_schools(schools: list[School]) -> list[School]:
    if not schools:
        return []
    return sorted(schools, key=lambda school: (school.ranking is None, school.ranking or float('inf')))


def scrape_schools_data(urls: dict[str, str]) -> list[School]:
    return [process_school(url) for url_str, url in urls.items()]


def get_month_number(date_string: str) -> int:
    if '/' in date_string:
        month_str, day_str = date_string.split("/")
        return int(month_str)
    else:
        month, day = date_string.split()
        return datetime.strptime(month, "%b").month


def get_basketball_season_year(date_str: str) -> int:
    current_date = datetime.now()
    month_number = get_month_number(date_str)

    if month_number < 6:  # Basketball season usually starts in June
        basketball_season_year = current_date.year
    else:
        basketball_season_year = current_date.year - 1
    return basketball_season_year


def extract_future_swic_games():
    soup = fetch_html(SWIC_URL)
    games = []
    current_date = datetime.now()

    swic_record = extract_overall_record(SWIC_RECORD_URL)
    tbody = soup.find('tbody')
    if tbody:
        for row in tbody.find_all('tr'):
            cols = row.find_all('td')
            if len(cols) == 6:
                date_str = cols[0].text.strip()
                date_str += f" {get_basketball_season_year(date_str)}"
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
                        game_url=SWIC_RECORD_URL
                    )
                    games.append(game)

    return School(
        name="SWIC",
        url=SWIC_URL,
        last_updated=datetime.now(),
        schedule=games,
        record=swic_record
    )


def extract_overall_record(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
        }
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, 'html.parser')
            overall_record_element = soup.find('span', class_='label', string='Overall')
            if overall_record_element:
                overall_record_value = overall_record_element.find_next_sibling('span', class_='value')
                if overall_record_value:
                    return overall_record_value.get_text(strip=True)
    except Exception as e:
        print(f"An error occurred: {e}")
    return None


def bold_future(game_date: datetime, message: str) -> str:
    if game_date >= datetime.now():
        return f"**{message}**"
    else:
        return message


def extract_future_college_games(soup) -> list[Game]:
    games = []
    games_html = soup.find_all('a', class_='Schedule__Game', href=True)
    for a_tag in games_html:
        if a_tag.find('span', class_='Schedule__Time'):
            opponent_span = a_tag.find('span', class_='Schedule__Team').text.strip().upper()
            game_url = a_tag['href']  # Changed to use the current a_tag's href
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
    return games


def process_colleges(college_urls) -> list[School]:
    colleges = []
    for url in college_urls.values():
        try:
            soup = fetch_html(url)
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

            college = School(
                url=url,
                last_updated=datetime.now(),
                name=school_name,
                mascot=mascot,
                record=record,
                ranking=ranking,
                schedule=schedule
            )
            colleges.append(college)
        except Exception:
            expected = School(
                url=url,
                last_updated=datetime.now(),
                name=None,
                mascot=None,
                record=None,
                ranking=None,
                schedule=[]
            )
            colleges.append(expected)

    return colleges


if __name__ == '__main__':
    print("􁗉")
    schools = scrape_schools_data(school_urls)
    generate_swiftbar_menu(schools, "IL")

    swic = extract_future_swic_games()
    generate_swiftbar_menu([swic] if swic else [], "")

    college_list = process_colleges(college_urls)
    generate_swiftbar_menu(college_list if college_list else [], "Conf")