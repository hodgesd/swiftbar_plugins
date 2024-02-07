#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12


from datetime import datetime
from typing import List, Optional

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
    ranking: Optional[str] = None
    schedule: Optional[List[Game]] = None


URL_BELLEVILLE_EAST_SCHEDULE = "https://www.maxpreps.com/il/belleville/belleville-east-lancers/basketball/schedule/"


def fetch_schedule_html(url: str) -> Optional[Tag]:
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    schedule_html = soup.select_one(".keYzcI .bOHsiZ:nth-of-type(2)")
    return schedule_html


def fetch_school_data(school: School) -> None:
    response = requests.get(school.url)
    soup = BeautifulSoup(response.text, 'html.parser')

    def extract_text(selector: str) -> Optional[str]:
        """Extracts text from a given selector."""
        element = soup.select_one(selector)
        return element.get_text(strip=True) if element else None

    # Use the helper function to extract data
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


def swiftbar_menu(school: School) -> None:
    print("􁗉")
    print("---")
    print(f"{school.name} ({school.record}) IL #:{school.ranking} | href = {school.url}")
    games = get_future_games(school)
    # print each game as a submenu item
    for game in games:
        print(
            f"--{game.date.strftime('%a, %b %d')}: {game.opponent} {game.home_away} {game.tipoff_time.strftime('%I:%M %p') if game.tipoff_time else ''} "

        )


def get_future_games(school: School) -> List[Game]:
    school = fetch_schedule_html(school.url)
    if school:
        future_games = parse_schedule(school)
        if future_games:
            return future_games
        else:
            print("No future games found.")
    else:
        print("No future games found.")


if __name__ == '__main__':
    sample_school = School(name="Belleville East", mascot="Lancers", url=URL_BELLEVILLE_EAST_SCHEDULE,
                           last_updated=datetime.now())
    sample_school.schedule = fetch_schedule_html(sample_school.url)
    fetch_school_data(sample_school)
    swiftbar_menu(sample_school)
