#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
#!unset TERM

# <bitbar.title>Hacker News Headlines - Lite</bitbar.title>
# <bitbar.author>hodgesd</bitbar.author>
# <bitbar.author.github>hodgesd</bitbar.author.github>
# <bitbar.desc>Display Top Hacker News Headlines</bitbar.desc>
# <bitbar.dependencies>python</bitbar.dependencies>
# <bitbar.image>https://raw.githubusercontent.com/amrrs/hn_headlines_bitbar/master/hn_headlines_bitbar.png</bitbar.image>
# <bitbar.version>1.0</bitbar.version>
# <bitbar.abouturl>https://github.com/amrrs/hn_headlines_bitbar/blob/master/hn_front.120m.py</bitbar.abouturl>

# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

from sys import exit

from requests import exceptions, get


def main():
    print("HN")
    print("---")
    print("Hacker News | href=https://news.ycombinator.com/")
    try:
        content = get("https://hacker-news.firebaseio.com/v0/topstories.json?print=pretty")
    except exceptions.RequestException:
        print("---")
        print("Internet Connection Not available")
        print("Manually refresh | refresh = true")
        exit(1)
    ids = content.json()
    story_base = "https://hacker-news.firebaseio.com/v0/item/"
    hn_link = "https://news.ycombinator.com/item?id="
    print("---")
    for id in ids[:10]:
        story = get(story_base + str(id) + ".json")
        story_json = story.json()
        print(
            story_json["title"] + "| href = https://news.ycombinator.com/item?id=" + str(id)
        )

if __name__ == "__main__":
    main()