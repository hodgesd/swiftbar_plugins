#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
# <swiftbar.title>CPRT New Posts</swiftbar.title>
# <swiftbar.version>v1.3</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Parses the CPRT Feed</swiftbar.desc>
# <swiftbar.dependencies>python3,requests,beautifulsoup4</swiftbar.dependencies>

# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>false</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
# <swiftbar.schedule>3600</swiftbar.schedule>

import os
import re
import sys
import time
from datetime import datetime
import requests
import feedparser
from html.parser import HTMLParser

# Set your direct URL with authentication key here
# Replace this with your actual URL that includes username and key
FEED_URL = "https://chiefpilotsforum.com/forums/forum/5-chief-pilots-roundtable.xml/?member=166&key=14b0f53796b65a0515017f89b26c78d8"

# Set to True to enable debug mode (saves content to file and prints debug info)
DEBUG = False


class MLStripper(HTMLParser):
    """Simple HTML Parser to strip HTML tags from content"""

    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.text = []

    def handle_data(self, d):
        self.text.append(d)

    def get_data(self):
        return "".join(self.text)


def strip_tags(html):
    """Remove HTML tags from text"""
    if not html:
        return ""
    s = MLStripper()
    s.feed(html)
    return s.get_data().strip()


class ForumEntry:
    def __init__(
        self, title, url, content, date, author=None, post_id=None, category="Post"
    ):
        self.title = title.strip() if title else "Untitled Post"
        self.url = url.strip() if url else ""

        # Clean HTML from content
        self.content = strip_tags(content) if content else ""

        # Store raw date string
        self.date_str = date

        self.author = author
        self.post_id = post_id
        self.category = category

        # Parse date
        self.datetime = None
        if date:
            try:
                # Try to parse RSS standard date format
                self.datetime = datetime.strptime(date, "%a, %d %b %Y %H:%M:%S %z")
            except (ValueError, TypeError):
                try:
                    # Try without timezone
                    self.datetime = datetime.strptime(date, "%a, %d %b %Y %H:%M:%S")
                except (ValueError, TypeError):
                    # Leave as None if we can't parse
                    pass

    def __str__(self):
        date_str = (
            self.datetime.strftime("%m/%d %H:%M") if self.datetime else "Unknown date"
        )
        return f"{self.title} ({date_str})"


def fetch_forum_content():
    """Fetch the forum content using requests."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"
        }
        response = requests.get(FEED_URL, headers=headers, timeout=15)

        if response.status_code == 200:
            content = response.text

            # Save the content for debugging
            if DEBUG:
                with open(
                    os.path.expanduser("~/cpf_debug.txt"), "w", encoding="utf-8"
                ) as f:
                    f.write(content)

                print(f"Fetched content length: {len(content)} bytes")
                print(f"First 100 characters: {content[:100]}")

            return content
        else:
            print(f"Error: Failed to fetch data (Status code: {response.status_code})")
            return None
    except Exception as e:
        print(f"Error fetching content: {str(e)}")
        return None


def parse_rss_feed(content):
    """Parse RSS feed using feedparser"""
    entries = []

    if not content:
        return entries

    # Parse the feed with feedparser
    feed = feedparser.parse(content)

    # Check if we have entries
    if not hasattr(feed, "entries") or not feed.entries:
        if DEBUG:
            print("No entries found in the feed")
        return entries

    # Process each entry
    for item in feed.entries:
        try:
            # Get title
            title = item.get("title", "Untitled Post")

            # Get link/url
            url = item.get("link", "")

            # Get content
            content = ""
            if "description" in item:
                content = item.description
            elif "summary" in item:
                content = item.summary
            elif "content" in item and len(item.content) > 0:
                content = item.content[0].value

            # Get date
            date = item.get("published", item.get("pubdate", None))

            # Get ID
            post_id = None
            if "id" in item:
                post_id = item.id
            elif "guid" in item:
                post_id = item.guid

            # Determine category based on URL
            category = "Post"
            if url:
                if "/files/file/" in url:
                    category = "File"
                elif "/contact-list/" in url:
                    category = "Profile"

            # Create entry
            entry = ForumEntry(title, url, content, date, None, post_id, category)
            entries.append(entry)

        except Exception as e:
            if DEBUG:
                print(f"Error parsing entry: {str(e)}")
            continue

    # Sort entries by date (newest first)
    entries.sort(key=lambda x: x.datetime if x.datetime else datetime.min, reverse=True)
    return entries


def main():
    # Fetch and parse the forum content
    content = fetch_forum_content()

    if content:
        entries = parse_rss_feed(content)
    else:
        entries = []

    # Count total entries
    total_entries = len(entries)

    # Print the SwiftBar menu
    print(f"CPF ({total_entries}) | dropdown=false")
    print("---")

    if not entries:
        print("No unread content | color=gray")
        print("---")
        print("Refresh | refresh=true")
        print("Open Forum | href=https://chiefpilotsforum.com")
        return

    # Group entries by category
    posts = [e for e in entries if e.category == "Post"]

    # Display posts
    print(f"Posts ({len(posts)}) | color=#007AFF")
    if posts:
        for entry in posts:
            date_str = entry.datetime.strftime("%m/%d %H:%M") if entry.datetime else ""
            truncated_title = (
                entry.title[:40] + "..." if len(entry.title) > 40 else entry.title
            )
            print(f"{truncated_title} ({date_str}) | href={entry.url} length=50")

            # Show a preview of the content in the submenu
            if entry.content:
                content_preview = entry.content[:200].replace("\n", " ")
                if len(entry.content) > 200:
                    content_preview += "..."
                print(f"-- {content_preview} | alternate=true length=200")
    else:
        print("-- No unread posts")

    print("---")

    print("Refresh | refresh=true")

    print("Open Forum | href=https://chiefpilotsforum.com")

    # Add debug option if debug is enabled
    if DEBUG:
        print("---")
        print("Debug Log | href=file://" + os.path.expanduser("~/cpf_debug.txt"))


if __name__ == "__main__":
    main()
