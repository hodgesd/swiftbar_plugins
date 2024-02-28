#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
import re
import string
from datetime import datetime
from typing import Optional

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
    ranking: Optional[int] = ""
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

    # response = requests.get(url)
    return BeautifulSoup(response.text, 'html.parser')


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
    school.name = extract_text('a.sub-title')
    school.record = extract_text('.record .block:nth-of-type(1) .data')
    extracted_ranking = extract_text('.record .block:nth-of-type(3) .data')
    school.ranking = int(extracted_ranking) if extracted_ranking else None


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
    # print(parsed_date)
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
        if school.ranking:
            ranking = f"[{rank_scope} #{school.ranking}]"
        else:
            ranking = ""
        print(f"{ranking} {school.name} ({school.record})  | href = {school.url}")
        if school.schedule:
            for game in school.schedule:
                if game.home_away == "Home" and game.date.date() >= datetime.now().date():
                    game_message = f"{game.date.strftime('%a, %b %d')}: {game.opponent} {game.tipoff_time.strftime('%I:%M %p') if game.tipoff_time else ''}"
                    game_message = game_message.strip()
                    print(
                        f'--{bold_future(game.date, game_message)} | href = {game.game_url if game.game_url else ""} md=true')


def process_school(url: str) -> School:
    school = School(url=url,
                    last_updated=datetime.now())
    fetch_school_data(school)
    return school


def sort_schools(schools: list[School]) -> list[School]:
    # Sort schools by ranking, placing None values at the end in a Pythonic way
    return sorted(schools, key=lambda school: (school.ranking is None, school.ranking))


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

                # Append the basketball season year to the date string
                date_str += f" {get_basketball_season_year(date_str)}"

                # Format date_str as a datetime object
                date = datetime.strptime(date_str, "%b %d %Y")
                # print(date, current_date, date >= current_date)
                if date.date() >= current_date.date():
                    location = cols[3].text.strip()
                    home_away = 'Home' if location == 'Belleville' else 'Away'

                    tipoff_time = None  # Default tipoff_time to None

                    # Only set tipoff_time when home_away is 'home'
                    if home_away == 'Home':
                        tipoff_time_str = cols[4].text.strip()

                        # Check if the time string ends with "AM" or "PM" and adjust the format string accordingly
                        if tipoff_time_str.endswith("AM") or tipoff_time_str.endswith("PM"):
                            time_format = "%I:%M%p"
                        else:
                            time_format = "%I:%M"

                        tipoff_time = datetime.strptime(tipoff_time_str, time_format) if tipoff_time_str else None

                    game = Game(
                        date=date,
                        home_away=home_away,
                        opponent=cols[2].text.strip(),
                        tipoff_time=tipoff_time,
                        game_url=SWIC_RECORD_URL
                    )
                    games.append(game)
    swic_school = School(name="SWIC", url=SWIC_URL, last_updated=datetime.now(), schedule=games, record=swic_record)

    return swic_school


def extract_overall_record(url):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
        }
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, 'html.parser')
            # print("HTML content:", soup.prettify())  # Debug statement to check HTML content
            overall_record_element = soup.find('span', class_='label', string='Overall')
            if overall_record_element:
                overall_record_value = overall_record_element.find_next_sibling('span', class_='value')
                if overall_record_value:
                    return overall_record_value.get_text(strip=True)
    except Exception as e:
        print(f"An error occurred: {e}")
    return None


def bold_future(game_date: datetime, message: str) -> str:
    """Return the message in bold if the game date is in the future, otherwise return the message as is."""
    # Check if the game date is in the future
    if game_date >= datetime.now():
        # print(game_date, game_date >= datetime.now())
        return f"**{message}**"
    else:
        return message


def extract_future_college_games(soup) -> list[Game]:
    games = []
    games_html = soup.find_all('a', class_='Schedule__Game', href=True)
    for a_tag in games_html:
        if a_tag.find('span', class_='Schedule__Time'):
            opponent_span = a_tag.find('span', class_='Schedule__Team').text.strip().upper()
            game_url = soup.find('a', class_='Schedule__Game')['href']
            date_span = a_tag.find('span', class_='Schedule__Time').text.strip()
            year = get_basketball_season_year(date_span)
            parsed_date = datetime.strptime(f"{date_span}/{year}", '%m/%d/%Y')
            home_away_span = a_tag.find('span', class_='Schedule_atVs tl mr2').text.strip()
            home_away = parse_home_away(home_away_span)
            time_span_elements = a_tag.find_all('span', class_='Schedule__Time')
            time_span = time_span_elements[1].text.strip().upper() if len(time_span_elements) > 1 else None
            if home_away == "Home" and parsed_date >= datetime.now() and time_span:
                tipoff = datetime.strptime(time_span, "%I:%M %p")
            else:
                tipoff = None
            games.append(Game(date=parsed_date, home_away=home_away, opponent=opponent_span, tipoff_time=tipoff,
                              game_url=game_url))
    return games


def process_colleges(college_urls) -> list[School]:
    colleges = []
    for url in college_urls.values():
        soup = fetch_html(url)
        school_name = soup.find('span', class_='db pr3 nowrap fw-bold').text
        mascot = soup.find('span', class_='flex flex-wrap').find('span', class_='db').find_next_sibling('span').text

        record_ranking_ul = soup.find('ul', class_='ClubhouseHeader__Record')
        record = record_ranking_ul.find_all('li')[0].text  # First li element
        ranking_str = record_ranking_ul.find_all('li')[1].text  # Second li element
        ranking = int(re.match(r'\d+', ranking_str).group(0)) if re.match(r'\d+', ranking_str) else None
        schedule = extract_future_college_games(soup)
        college = School(url=url, last_updated=datetime.now(), name=school_name, mascot=mascot, record=record,
                         ranking=ranking, schedule=schedule)
        colleges.append(college)
    return colleges


if __name__ == '__main__':
    print("􁗉")
    schools = scrape_schools_data(school_urls)
    generate_swiftbar_menu(schools, "IL")
    swic = extract_future_swic_games()
    generate_swiftbar_menu([swic])
    college_list = process_colleges(college_urls)
    generate_swiftbar_menu(college_list, "Conf")
