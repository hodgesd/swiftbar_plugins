SWIFTBAR PLUGIN BUILD PROMPTS (FINAL)

==================================================

NOTES / DESIGN DECISIONS (READ FIRST)

==================================================

NOTAM APIs:

- FAA NOTAM “API” is NOT a true public REST API. The site ([https://notams.aim.faa.gov/notamSearch/](https://notams.aim.faa.gov/notamSearch/)) is primarily a web UI and is difficult to scrape reliably.

- ICAO NOTAM feeds are NOT freely/publicly accessible without credentials in most cases.

- Conclusion: Do NOT rely on FAA scraping or ICAO secured feeds.

RECOMMENDED SOURCE:

- Use a reliable aviation data proxy such as:

  - [https://api.adsb.lol](https://api.adsb.lol) (free aviation data)

  - Or fallback: FAA CSV downloads / third-party mirrors if available

- LLM should be instructed to build an abstraction layer so source can be swapped later.

CONFIG FORMAT:

- YAML is easier than JSON for human editing (especially lists like airports)

- Use YAML for config

MARKET DATA:

- Yahoo Finance DOES NOT provide EMA directly

- EMA must be calculated manually from historical data

- Use ~6 months daily candles for accurate 200 EMA

==================================================

PROMPT 1 — NOTAMS FOR HOME AIRPORTS

==================================================

You are an expert macOS developer building SwiftBar plugins in Python.

Build a production-quality SwiftBar plugin that displays active NOTAMs for user-configured airports.

IMPORTANT DATA SOURCE CONSTRAINT:

- Do NOT scrape FAA NOTAM search UI

- Assume FAA and ICAO feeds are NOT directly usable without auth

- Implement a pluggable data source layer

- Default to a free/public aviation API (e.g., ADS-B Exchange style endpoints or similar)

- Structure code so the NOTAM source can be swapped easily later

CORE FUNCTIONALITY:

- Read airport list from YAML config file:

  ~/.swiftbar_notams.yaml

Example YAML:

airports:

  - KSTL

  - KSUS

  - KCPS

filter_trip_impacting: true

- Fetch active NOTAMs for each airport

- Parse and classify NOTAMs into:

  - Runway closures (CRITICAL)

  - Taxiway closures

  - Airspace restrictions (TFRs)

  - Lighting/nav outages

  - Other

TRIP IMPACT LOGIC:

- “Trip-impacting” includes:

  - Runway closures

  - TFRs

  - Major operational limitations

UI / SWIFTBAR OUTPUT:

- Menu bar:

  - 🔴 if any critical NOTAM exists

  - 🟡 if only caution-level NOTAMs

  - 🟢 if none

- Dropdown:

  - Group by airport

  - Each NOTAM:

    - Red = critical

    - Yellow = caution

    - Default = informational

  - Truncate long NOTAM text for readability

- Include toggle:

  - “Show Only Trip-Impacting NOTAMs”

  - Controlled via YAML config

REFRESH:

- Every 6 hours

TECH REQUIREMENTS:

- Python 3

- requests library only (no heavy deps)

- Local caching (JSON cache file)

- Graceful API failure handling

CODE STRUCTURE:

- load_config()

- fetch_notams()

- classify_notam()

- filter_notams()

- render_menu()

OUTPUT:

- Full SwiftBar script

- YAML config example

- Setup instructions in comments

GOAL:

Fast, reliable NOTAM awareness without fragile scraping.

==================================================

PROMPT 2 — MARKET INDICES QUICK GLANCE

==================================================

You are an expert in financial data scripting and SwiftBar plugin development.

Build a SwiftBar plugin in Python that displays major market indices using Vanguard ETF equivalents and generates actionable signals.

SYMBOLS (USE ETFs INSTEAD OF INDICES):

- VOO (S&P 500)

- QQQ (NASDAQ proxy)

- DIA (Dow Jones)

- VXUS (International markets)

DATA SOURCE:

- Yahoo Finance API:

[https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?range=6mo&interval=1d](https://query1.finance.yahoo.com/v8/finance/chart/<SYMBOL>?range=6mo&interval=1d)

IMPORTANT:

- EMA values are NOT provided by the API

- You MUST calculate:

  - 50-day EMA

  - 200-day EMA

CALCULATIONS:

- Current price

- Daily % change

- % drop from opening price

- 50 EMA

- 200 EMA

SIGNAL LOGIC:

- STRONG BUY:

  - Price < 200 EMA

- BUY:

  - Price < 50 EMA

- WATCH:

  - Intraday drop > 1% from open

- OTHERWISE:

  - Neutral

UI / SWIFTBAR OUTPUT:

- Menu bar:

  - Cycle one symbol per refresh:

    Example: “VOO -0.8%”

  - Color:

    - Green = positive day

    - Red = negative day

- Dropdown:

  For each ETF:

  - Current price

  - % change

  - Distance to 50 EMA

  - Distance to 200 EMA

  - Signal (BUY / STRONG BUY / WATCH / NEUTRAL)

STYLE:

- Clean and minimal

- No clutter

REFRESH:

- Every 30 minutes

TECH REQUIREMENTS:

- Python 3

- requests only (no pandas unless optional fallback)

- Manual EMA calculation

CODE STRUCTURE:

- fetch_data()

- calculate_ema()

- evaluate_signal()

- render_output()

ERROR HANDLING:

- If API fails:

  - Show “Data unavailable”

GOAL:

A fast, actionable macro market dashboard—not a trading system.

==================================================

PROMPT 3 — NWS SEVERE WEATHER ALERTS

==================================================

You are an expert Python developer building lightweight monitoring tools for SwiftBar.

Build a SwiftBar plugin that displays active National Weather Service alerts.

DATA SOURCE:

[https://api.weather.gov/alerts/active?zone=ILC163](https://api.weather.gov/alerts/active?zone=ILC163)

CORE FUNCTIONALITY:

- Fetch alerts for St. Clair County, IL

- Parse alert types:

  - Tornado Warning (CRITICAL)

  - Severe Thunderstorm Warning

  - Flood Warning

  - Watch / Advisory

UI / SWIFTBAR OUTPUT:

- Menu bar:

  - ✅ if no alerts

  - ⚠️ or 🚨 if alerts present (based on severity)

- Dropdown:

  - Each alert shows:

    - Title

    - Severity (color-coded)

    - Short description

    - Clickable link (href) to full alert

COLOR LOGIC:

- Red = Tornado / extreme

- Orange = severe warnings

- Yellow = watches/advisories

REFRESH:

- Every 30 minutes

TECH REQUIREMENTS:

- Python 3

- requests library

- Clean JSON parsing

ROBUSTNESS:

- Handle empty responses

- Handle API downtime

- Cache last successful result

OPTIONAL:

- Support multiple zones

- Optional notification hook (commented)

CODE STRUCTURE:

- fetch_alerts()

- classify_alert()

- render_menu()

GOAL:

Immediate, always-visible severe weather awareness.

==================================================

END OF FILE

==================================================