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


URL_BELLEVILLE_EAST_SCHEDULE = "https://www.maxpreps.com/il/belleville/belleville-east-lancers/basketball/schedule/"


def fetch_schedule_html(url: str) -> Optional[Tag]:
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    schedule_html = soup.select_one(".keYzcI .bOHsiZ:nth-of-type(2)")
    return schedule_html


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


def parse_schedule(schedule_html: Tag) -> List[Game]:
    games = []
    for tr in schedule_html.find_all('tr'):
        tds = tr.find_all('td')
        if len(tds) >= 4:
            date = parse_date(tds[0].text.strip())
            home_away = parse_home_away(tds[1].text)
            opponent = extract_opponent(tds[1])
            tipoff_time = parse_tipoff_time(tds[2].text.strip())

            games.append(Game(date=date, home_away=home_away, opponent=opponent, tipoff_time=tipoff_time))
    return games


if __name__ == '__main__':
    schedule_html = fetch_schedule_html(URL_BELLEVILLE_EAST_SCHEDULE)
    if schedule_html:
        future_games = parse_schedule(schedule_html)
        for game in future_games:
            print(game)
    else:
        print("No future games found.")
