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
from typing import List, Optional, Dict

import requests
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel


class Game(BaseModel):
    date: datetime
    home_away: str
    opponent: str
    tipoff_time: Optional[datetime]


class School(BaseModel):
    name: Optional[str] = None
    mascot: Optional[str] = None
    url: Optional[str] = None
    last_updated: datetime
    record: Optional[str] = None
    ranking: Optional[int] = None
    schedule: Optional[List[Game]] = None


school_urls = {
    "BELLEVILLE_EAST": "https://www.maxpreps.com/il/belleville/belleville-east-lancers/basketball/schedule/",
    "O'FALLON":"https://www.maxpreps.com/il/ofallon/ofallon-panthers/basketball/schedule/",
    "MASCOUTAH":"https://www.maxpreps.com/il/mascoutah/mascoutah-indians/basketball/schedule/",
    "BELLEVILLE_WEST":"https://www.maxpreps.com/il/belleville/belleville-west-maroons/basketball/schedule/"
}


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

    future_schedule_html = soup.select_one(".keYzcI .bOHsiZ:nth-of-type(2)")
    if future_schedule_html:
        school.schedule = parse_schedule(future_schedule_html)
    school.name = extract_text('a.sub-title')
    school.record = extract_text('.record .block:nth-of-type(1) .data')
    school.ranking = extract_text('.record .block:nth-of-type(3) .data')


def parse_schedule(schedule_tag: Tag) -> List[Game]:
    games = []
    for tr in schedule_tag.find_all('tr'):
        tds = tr.find_all('td')
        if len(tds) >= 4:
            date = parse_date(tds[0].text.strip())
            home_away = parse_home_away(tds[1].text)
            opponent = extract_opponent(tds[1])
            tipoff_time = parse_tipoff_time(tds[2].text.strip())

            games.append(Game(date=date, home_away=home_away, opponent=opponent, tipoff_time=tipoff_time))
    return games


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


def generate_swiftbar_menu(urls: Dict[str, str]) -> None:
    print("􁗉")
    print("---")
    for url_str, url in urls.items():
        school = process_school(url)
        print(f"{school.name} ({school.record}) IL #:{school.ranking} | href = {school.url}")
        for game in school.schedule:
            print(
                f"--{game.date.strftime('%a, %b %d')}: {game.opponent} {game.home_away} {game.tipoff_time.strftime('%I:%M %p') if game.tipoff_time else ''} "

            )


def process_school(url: str) -> School:
    school = School(url=url,
                    last_updated=datetime.now())
    fetch_school_data(school)
    return school


if __name__ == '__main__':
    generate_swiftbar_menu(school_urls)
