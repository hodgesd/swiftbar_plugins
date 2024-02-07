from datetime import datetime, time
from typing import List, Optional

import requests
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel


# Updated Pydantic model with datetime and time
class Game(BaseModel):
    date: datetime
    home_away: str
    opponent: str
    tipoff_time: Optional[time]


URL_BELLEVILLE_EAST_SCHEDULE = "https://www.maxpreps.com/il/belleville/belleville-east-lancers/basketball/schedule/"


def get_future_games_schedule(url: str) -> List[Game]:
    html_data = requests.get(url).text
    soup = BeautifulSoup(html_data, 'html.parser')
    full_schedule_html = soup.select(".keYzcI .bOHsiZ:nth-of-type(2)")
    if full_schedule_html:
        return parse_schedule(full_schedule_html[0])
    else:
        return []


def parse_schedule(schedule_html: Tag) -> List[Game]:
    games = []
    for tr in schedule_html.find_all('tr'):
        tds = tr.find_all('td')
        if len(tds) >= 4:
            game_date_str = tds[0].text.strip()
            game_date = datetime.strptime(game_date_str, '%m/%d').replace(year=datetime.now().year)

            home_away = "Away" if "@" in tds[1].text else "Home" if "vs" in tds[1].text else "Neutral"
            opponent_element = tds[1].find('span', class_="name")
            opponent = opponent_element.text.strip() if opponent_element else ""

            # Preprocess and extract tipoff time from the third <td>
            tipoff_time_str = tds[2].text.strip().upper()  # Convert AM/PM to uppercase
            try:
                tipoff_time = datetime.strptime(tipoff_time_str, '%I:%M%p').time()
            except ValueError:
                tipoff_time = None

            games.append(Game(date=game_date, home_away=home_away, opponent=opponent, tipoff_time=tipoff_time))
    return games

if __name__ == '__main__':
    future_games = get_future_games_schedule(URL_BELLEVILLE_EAST_SCHEDULE)
    for game in future_games:
        print(game)
