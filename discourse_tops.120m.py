#!/usr/bin/env -S PATH="${PATH}:/usr/local/bin" python3
#!unset TERM

# <bitbar.title>Discourse Top Posts</bitbar.title>
# <bitbar.version>v0.8</bitbar.version>
# <bitbar.author>Your Name</bitbar.author>
# <bitbar.author.github>hodgesd</bitbar.author.github>
# <bitbar.desc>Pulls top posts from your favorite Discourse channels.</bitbar.desc>
# <bitbar.dependencies>python, pandas v1.5.0 (currently at release candidate)</bitbar.dependencies>
# <bitbar.droptypes>Supported UTI's for dropping things on menu bar</bitbar.droptypes>

# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

# Todo: add star for posts with high like counts (heatmap ≥ low)
# Todo: add support to exclude categories 

import requests
import pandas as pd


MAX_POSTS = 15


forums = {
    "Automators": "https://talk.automators.fm/top?period=weekly",
    "Obsidian": "https://forum.obsidian.md/top?period=weekly",
    "Level1Techs":"https://forum.level1techs.com/top?period=weekly",
    "Drafts":"https://forums.getdrafts.com/top?period=monthly",
    "Traefik":"https://community.traefik.io/top?period=weekly",
    "Pi-hole":"https://discourse.pi-hole.net/top?period=weekly",
    "Home Assistant":"https://community.home-assistant.io/top?period=weekly",
    "Keyboard Maestro":"https://forum.keyboardmaestro.com/top?period=weekly",
    "Python": "https://discuss.python.org/top?period=weekly",
}


def get_discourse_posts(forum_url:str) -> pd:
    r = requests.get(forum_url)
    url_html = r.text
    posts_df = pd.read_html(url_html, extract_links="all")[0]
    posts_df.columns = ["Headline", "Authors", "Replies", "Views", "Activity"]
    posts_df[['Topic', 'Topic_url']] = pd.DataFrame(posts_df['Headline'].tolist(), index=posts_df.index)   
    posts_df["Category"] = posts_df["Topic"].str.split("  ").str.get(1) # get text before the double space
    posts_df["Topic"] = posts_df["Topic"].str.split("  ").str.get(0) # get text before the double space
    posts_df["Headline"] = [f"{str(x)} [{z}]| href = {y}" for x, y, z in zip(posts_df['Topic'], posts_df['Topic_url'], posts_df['Category'])]
    return posts_df["Headline"].head(MAX_POSTS)

def send_to_bitbar(forum_title:str, posts_df:pd, forum_url:str) -> None:
    print(f"{forum_title} | href = {forum_url}")
    posts_df.apply(lambda x: print(f"--{x}", end='\n'))


# Menu Bar Title
print(":bubble.left.and.bubble.right: | symbolize=true\n")
print("---")

# Dropdown Menu - Top Posts from Discourse Forums
for forum_title, forum_url in forums.items():
    posts_df = get_discourse_posts(forum_url)
    send_to_bitbar(forum_title, posts_df, forum_url)