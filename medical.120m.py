#!/usr/bin/env -S PATH="${PATH}:/usr/local/bin" python3
#!source ~/.bash_profile
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

# youtube tutorial: https://youtu.be/qYSWWGz9Z6s
from playwright.sync_api import Playwright, sync_playwright, expect
import time
import creds

# todo: bold the medical status
# todo: change color of medical status
# todo: change last update to time since last update
# todo: retrieve user and password from environment variables
# todo: limit updates to business hours

NOT_APPROVED = ":airplane.arrival: | symbolize=true\n"
APPROVED = ":airplane.departure: | symbolize=true\n"
FAA_USER = creds.FAA_USER
FAA_PASSWORD = creds.FAA_PASSWORD


# Check medical status at https://medxpress.faa.gov/MedXpress/Login.aspx
def run(playwright: Playwright) -> None:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    page.goto("https://medxpress.faa.gov/MedXpress/Login.aspx")
    page.get_by_placeholder("Email Address").click()
    page.get_by_placeholder("Email Address").fill(FAA_USER)
    page.get_by_placeholder("Email Address").press("Tab")
    page.get_by_placeholder("Password").fill(FAA_PASSWORD)
    page.get_by_role("button", name="Login").click()
    page.get_by_label(
        "I have read and accept the Terms of Service Agreement and Privacy Statement."
    ).check()
    page.get_by_role("button", name="Submit").click()
    page.get_by_role("link", name="Application Status").click()
    time.sleep(1)
    underlined_div = page.query_selector(
        'div[style="color: #000;text-decoration: underline;"]'
    )
    status = underlined_div.inner_text().upper()
    title = APPROVED if status == "Certification Decision" else NOT_APPROVED
    print(title)
    print("---")
    print(
        f"Medical Status: {status} | href=https://medxpress.faa.gov/MedXpress/Login.aspx"
    )
    print(
        f"Last Updated: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())} | size=12"
    )

    # ---------------------
    context.close()
    browser.close()


with sync_playwright() as playwright:
    run(playwright)
