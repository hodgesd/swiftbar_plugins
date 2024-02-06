import requests
from bs4 import BeautifulSoup

URL_BELLEVILLE_EAST_SCHEDULE = "https://www.maxpreps.com/il/belleville/belleville-east-lancers/basketball/schedule/"


def get_future_games_schedule(URL_BELLEVILLE_EAST_SCHEDULE: str):
    html_data = requests.get(URL_BELLEVILLE_EAST_SCHEDULE).text
    soup = BeautifulSoup(html_data, 'html.parser')
    full_schedule_html = soup.select(".keYzcI .bOHsiZ:nth-of-type(2)")
    if full_schedule_html:
        future_games = full_schedule_html[0]
        print(future_games)
        return future_games
    else:
        return "No matching elements found."


if __name__ == '__main__':
    get_future_games_schedule(URL_BELLEVILLE_EAST_SCHEDULE)
