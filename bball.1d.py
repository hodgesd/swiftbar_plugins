#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12

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

from datetime import datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel


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

SWIC_URL = "https://www.swic.edu/students/services/student-life/athletics/mens-basketball/"
SWIC_RECORD_URL = "https://www.njcaa.org/sports/mbkb/2023-24/div1/schedule?teamId=mjvavx3krm8kh0zb"


def fetch_html(url: str) -> BeautifulSoup:
    """Fetches HTML content from a given URL and returns a BeautifulSoup object."""
    response = requests.get(url)
    return BeautifulSoup(response.text, 'html.parser')


def fetch_school_data(school: School) -> None:
    soup = fetch_html(school.url)

    def extract_text(selector: str) -> Optional[str]:
        """Extracts text from a given selector."""
        element = soup.select_one(selector)
        return element.get_text(strip=True) if element else None

    # future_schedule_html = soup.select_one(".keYzcI .bOHsiZ:nth-of-type(2)")
    future_schedule_html = soup.select_one("table tbody")
    # print(future_schedule_html)
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


def parse_game_url(game_TD: Tag):
    a_tag = game_TD.find('a')
    if a_tag and a_tag.has_attr('href'):
        return a_tag['href']
    return ""


def parse_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, '%m/%d').replace(year=datetime.now().year)


def parse_tipoff_time(time_str: str) -> Optional[datetime]:
    try:
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
    return opponent_element.text.strip() if opponent_element else ""


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
                if game.home_away == "Home":
                    print(
                        f"--{game.date.strftime('%a, %b %d')}: {game.opponent} {game.tipoff_time.strftime('%I:%M %p') if game.tipoff_time else ''} | href = {game.game_url if game.game_url else ''}"
                    )


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


def extract_future_swic_games():
    soup = fetch_html(SWIC_URL)

    games = []
    current_date = datetime.now()

    def get_basketball_season_year(date_str: str) -> int:
        current_date = datetime.now()
        month, day = date_str.split()
        month_number = datetime.strptime(month, "%b").month

        if month_number < 6:  # Basketball season usually starts in June
            basketball_season_year = current_date.year
        else:
            basketball_season_year = current_date.year - 1
        return basketball_season_year

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
                if date >= current_date:
                    location = cols[3].text.strip()
                    home_away = 'Home' if location == 'Belleville' else 'Away'

                    tipoff_time = None  # Default tipoff_time to None

                    # Only set tipoff_time when home_away is 'home'
                    if home_away == 'home':
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
                    )
                    # print(game)
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


if __name__ == '__main__':
    print("􁗉")

    schools = scrape_schools_data(school_urls)
    generate_swiftbar_menu(schools, "IL")
    swic = extract_future_swic_games()
    generate_swiftbar_menu([swic])
