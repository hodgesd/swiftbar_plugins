#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12

"""
<xbar.title>jsSwiftBar</xbar.title>
<xbar.version>v1.0</xbar.version>
<xbar.author>hodgesd</xbar.author>
<xbar.author.github>hodgesd</xbar.author.github>
<xbar.desc>Test SwiftBar Plugin</xbar.desc>
<xbar.dependencies>python</xbar.dependencies>
<xbar.abouturl>http://varunmalhotra.xyz/blog/2016/02/bitbar-plugins-for-github-and-producthunt.html</xbar.abouturl>
"""

# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

import asyncio

import requests
from bs4 import BeautifulSoup

LINE_LENGTH = 100
# Function to get the DOM of a webpage
async def getDOM(url):
    response = requests.get(url)
    return BeautifulSoup(response.content, 'html.parser')


url = 'https://www.techmeme.com/'

result = asyncio.run(getDOM(url))
stories = result.select('.clus')

print('TM' + '\n---\n')
# log link to techmeme.com
print('Techmeme | href= https://www.techmeme.com/' + '\n---\n')

for story in stories:
    try:
        story_site = story.select('cite')[0].select('a')[0].text
        story_link = story.select('.ourh')[0]['href']
        story_title = story.select('.ourh')[0].text
    except Exception:
        pass
    # display_title = story_title if len(story_title) <= 70 else story_title[:67] + '...'
    # print(f'[{story_site}] {story_title}| href= {story_link} length=90 trim=True tooltip={story_title}')
    print(f'[{story_site}] {story_title} | href={story_link} length=LINE_LENGTH trim=True tooltip={story_title}')
