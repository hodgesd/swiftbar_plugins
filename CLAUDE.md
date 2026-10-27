# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a collection of SwiftBar/xBar plugins written in Python that display various information in the macOS menu bar. SwiftBar/xBar reads the stdout output from these scripts and renders them as menu bar items using a special formatting syntax.

## Development Setup

**Package Manager**: This project uses `uv` for dependency management.

**Bootstrap environment**:
```bash
just bootstrap
```

**Dependencies**: Managed via `pyproject.toml`:
- `aiohttp` - Async HTTP requests
- `avwx-engine` - Aviation weather (METAR) data
- `beautifulsoup4` - HTML parsing
- `feedparser` - RSS/Atom feed parsing
- `pydantic` - Data validation
- `requests` - HTTP requests

## Plugin Architecture

### SwiftBar Output Format

All plugins follow SwiftBar's stdout-based rendering format:
- First line: Menu bar icon/text
- `---` separator: Starts dropdown menu items
- `--` prefix: Creates submenu items (can be nested)
- Special parameters: `href=`, `color=`, `tooltip=`, `refresh=true`, `length=`, `trim=true`
- Interactive commands: `bash='path' param1='value' terminal=false refresh=true`

### Plugin Naming Convention

Filenames use format: `name.{interval}.py` where interval is:
- `1d` - Daily
- `6hr` - Every 6 hours
- `120m` - Every 120 minutes
- etc.

### Plugin Structure Patterns

**Shebang**: All plugins start with:
```python
#!/Users/hodgesd/PycharmProjects/swiftbar_plugins/.venv/bin/python3.12
```

**Metadata**: SwiftBar/xBar metadata in comments:
```python
# <xbar.title>Plugin Title</xbar.title>
# <xbar.version>v1.0</xbar.version>
# <xbar.author>Author Name</xbar.author>
# <xbar.desc>Description</xbar.desc>
# <xbar.dependencies>python,requests</xbar.dependencies>
```

**Common Patterns**:
1. **News/RSS aggregators**: Use `feedparser` or `BeautifulSoup` to scrape headlines, display with date stamps and URLs
2. **Interactive plugins**: Accept command-line arguments to handle user interactions (add/remove items, configuration)
3. **Async fetching**: Modern plugins use `asyncio` and `aiohttp` for concurrent data fetching (see `tech_news.py`)
4. **Data validation**: Newer plugins use `Pydantic` models for structured data (see `news.6hr.py`)

### Key Plugin Examples

**airport-wx.py**: Interactive METAR display with configuration management
- Uses JSON config files in SwiftBar's data directory
- Command-line interface for add/remove operations
- Color-coded flight rules (VFR=green, IFR=red, MVFR=blue, LIFR=purple)

**tech_news.py**: Async aggregator combining multiple sources
- Fetches Techmeme, Hacker News, and Lobsters concurrently
- Uses `StringIO` buffers and `asyncio.gather()` for parallel fetching
- Implements headline trimming with tooltips for full text

**news.6hr.py**: RSS/HTML parser with Pydantic models
- Defines `Article` and `NewsSource` models with validation
- Supports both RSS feeds and HTML scraping
- Date parsing with recency filtering (7 days by default)

**bball.6h.py**: Local basketball schedules (MaxPreps, ESPN API, SWIC, Vincennes, NJCAA GraphQL, Sidearm school sites)
- D2 / D3 / NAIA schools come from their own Sidearm athletics sites via `parse_sidearm_schedule`; add one with a `small_colleges` entry (`name`, `level`, `base`)
- Pure `parse_*` functions per source, tested in `tests/test_bball_parsers.py` against trimmed fixtures
- `./bball.6h.py --check` prints rows seen / games parsed per source and exits 1 on any failure; run it before the season
- Teams in `~/.config/swiftbar-plugins/bball.json` (defaults in `DEFAULT_CONFIG`); last-good cache in `~/.cache/swiftbar-plugins/`
- `BBALL_FAKE_TODAY=YYYY-MM-DD` renders the in-season / off-season layouts on any day

**nba.1h.py**: NBA league tracker with tracked players (all ESPN)
- League first: the menu bar counts today's whole slate (orange while any game is live) and the dropdown leads with it. Tracked players (`players`, ESPN athlete IDs — LeBron 1966 and Harden 3992 by default) are a section under it, not the subject; their teams are re-derived from `athlete.team` every run, so a trade needs no config edit
- Endpoints and their traps, all probed 2026-10-25: scoreboard scores are strings but team-schedule scores are `{value, displayValue}`, and the records differ the same way (`records[].summary` vs `record[].displayValue`); the two also use different `broadcasts` shapes (`{market: "away", names: []}` vs `{market: {type: "National"}, media: {shortName}}`). Standings `entries` arrive unsorted, so rows are re-sorted by `playoffSeed`. Leaders categories must be matched by `name` (`pointsPerGame`), never `abbreviation` — PTS is shared by the per-game and the season-total category
- Career points for the milestone line come from `/athletes/{id}/stats` → `categories[name=totals].totals` (last column), not from GP × PPG
- Leaders abbreviations are not the overview endpoint's average labels, so the tracked-player row under a top ten translates via `LEADER_AVERAGE_LABELS` (MPG → MIN). 3PM has no overview average and is averaged from the game log's `3PT` makes; PER has no source at all and gets no row
- Days are decided in the configured zone (`set_zone`, called by both entry points), never the machine's, because every tipoff is converted to it. ESPN's "today" scoreboard is a UTC-ish day, so it routinely holds games that tipped the previous evening in Central — which is why a cached slate is dated by when it was *fetched* (the cache stamp), not by its tipoffs, and is dropped once that day is over. Every other cached section stays useful for days
- Menu: TODAY/LIVE slate → MY GUYS (averages, last-N game log, season highs, milestone, team standing, injury) → NEXT UP + HEAD TO HEAD → LEAGUE LEADERS → STANDINGS. Every Menlo table's header and rows come from one shared row builder (`slate_row`, `log_row`, `standings_row`, `leader_row`) so they cannot drift; `scrub()` deliberately keeps leading spaces because that is what aligns the columns. A slate row's numeric column is a record before tip and the score after, so its header reads REC, SCORE, or REC/SC for a mixed slate. `DIMMED` is empty on purpose (secondary text follows the system label color); rows meant to recede — finished games, seeds below the play-in — use `MUTED`
- `sf_symbol` config defaults to `figure.basketball` (a dribbling figure) to stay distinct from bball.6h.py's `basketball`
- `./nba.1h.py --check` prints per-source counts and exits 1 on failure; tests in `tests/test_nba_parsers.py`. Config `~/.config/swiftbar-plugins/nba.json`, per-section last-good cache in `~/.cache/swiftbar-plugins/nba_cache.json`, `NBA_FAKE_TODAY` / `NBA_NO_CACHE` for testing
- The interval is fixed at 1h by the filename; SwiftBar cannot speed a plugin up for a live game, so use the dropdown's Refresh for a live score

**home_comps_uv.8hr.py**: Neighborhood comps from Redfin (stdlib only)
- Redfin map-search JSON (`stingray/api/gis`) with a bounding `poly` around the home; `mpt=99` is required or the polygon is ignored. Status 9 = active + coming soon, 130 = pending, 9 + `sold_within_days` = sold
- The CSV endpoint returns no rows for the St. Louis MLS, and autocomplete / initialInfo return 403; the Redfin Estimate comes from the `stingray/api/home/details/avm` JSON (property id from the home URL) because the HTML home page gets an AWS WAF challenge (202). Year built isn't in the AVM, so it comes from the home lookup in `location.json`. Redfin requests are spaced `REDFIN_GAP` (1.5s) apart
- Home lat/lon come from the US Census geocoder and the home URL from a tiny-box sold-history search, both cached in `location.json`
- `listings_state.json` tracks first-seen dates and prices for the NEW and price-change badges; failed fetches fall back to the last good cache
- Results carry `uiPropertyType` (same codes as the `uipt` filter: 1 house, 2 condo, 3 townhouse, 5 land); `property_types` in config defaults to houses. Sold results have no list price or DOM, so sale-to-list / days-to-sell come from `listings_state.json` and fill in over time
- `listings_state.json` also keeps a daily Redfin Estimate history for the trend line
- Comps value: the 5 most similar sales in the last `comp_days` (default 365) within 75–133% of the home's sqft; each price is grown `appreciation` %/yr since sale and adjusted `SIZE_ADJ` ($50) per sqft toward the home's size, then the median gets `BED_ADJ` per extra bedroom. Scaling median $/sf by sqft was dropped because larger comps sell for less per sqft. It's the menu bar value unless `title` is `"redfin"`, falling back to the Redfin Estimate when no comps qualify
- Subdivisions come from Redfin's `location` field (MLS subdivision, free text); when it's empty or just the city, the St. Clair County parcel layer is queried by point and cached forever in `subdivisions.json`. `exclude_subdivisions` / `subdivision` match case-insensitive substrings, and exclusion wins (e.g. "Spyglass Vlg/Orchards")
- Menu layout (v2.3): the home is one title line (click → Redfin), then comps and estimate lines. The on-market table ("6 PENDING · 8 ACTIVE") puts pending rows (PEND, teal) above active ones, and SOLD is another. Addresses print without the street type (`short_address`; tooltips keep it) and every date is mm/dd/yy (`fmt_date`). Each table's title line doubles as its column header (Menlo-Bold, padded to line up) and links to Redfin's view-all search. Prices print as `$307K` via `fmt_short`. Colors are `color=<light>,<dark>` pairs so day mode stays readable; the comps submenu is a Menlo table with a header row and `short_subdiv` names (display only; matching uses the raw name)
- `./home_comps_uv.8hr.py --check` prints per-source counts and exits 1 on failure

**cprt.1h.py**: Chief Pilots Roundtable topics you haven't posted in yet (stdlib only, read-only — never log in or post)
- Invision Community RSS has no author field, so it diffs two member-keyed feeds: the forum's Latest Topics RSS minus an Activity Stream RSS (Topics, Ownership "Content I have posted in", Show me "Content items only"). Both sort by last activity, so the diff is exact within the latest 25 topics
- Feed URLs carry a private key: `CPRT_FEED_URL` / `CPRT_POSTED_FEED_URL`, or the sops-nix files `/run/secrets/cprt-feed-url` and `/run/secrets/cprt-posted-feed-url`
- HTML pages are login-gated behind a Cloudflare challenge and the host 429s request bursts; keep it to the two feed requests per run. Fetch attempts (good or failed) are at least `MIN_FETCH_INTERVAL` (5 min) apart, so wake / Refresh reruns replay the cache. A failed fetch shows the last good pair from `~/.cache/swiftbar-plugins/cprt/`, marked stale
- `./cprt.1h.py --check` prints per-feed counts and exits 1 on failure; tests in `tests/test_cprt.py`

## Configuration Management

Plugins that need persistent configuration use:
- `os.getenv('SWIFTBAR_PLUGIN_DATA_PATH')` to locate SwiftBar's data directory
- JSON files for storing user preferences (e.g., airport codes, feed URLs)
- Helper functions: `load_config()`, `save_config()` pattern

## Development Commands

**Lock dependencies** (update requirements.txt from requirements.in):
```bash
just lock
```

Note: The project is transitioning from `requirements.in/txt` to `pyproject.toml` + `uv.lock`. Some older workflows may reference requirements files, but new development should use `pyproject.toml`.

## Testing Plugins

**Run the parser tests** (bball, nba, home_comps, deals, cprt):
```bash
uv run --with pytest --with aiohttp --with beautifulsoup4 --with truststore pytest tests/
```

**Test a plugin manually**:
```bash
python plugin_name.py
```

This outputs the SwiftBar menu format to stdout. Verify:
- First line is the menu bar text/icon
- `---` separators are in the right places
- Menu items use correct SwiftBar syntax (`href=`, `color=`, etc.)

**Test interactive features**:
```bash
python plugin_name.py command arg1 arg2
```

For example, testing airport addition:
```bash
python airport-wx.py add KSTL
```

## Creating New Plugins

When creating new plugins:
1. Copy shebang and metadata structure from existing plugins (use `tech_news.py` or `news.6hr.py` as templates)
2. Use appropriate data fetching library (async `aiohttp` for speed, `requests` for simplicity)
3. Implement error handling with fallback error messages in SwiftBar format (errors should still render as menu items)
4. For interactive plugins, handle command-line arguments in `if __name__ == '__main__'` block
5. Test output manually by running script - verify SwiftBar formatting renders correctly
6. Use SF Symbols for menu bar icons (e.g., `􀤦` for news, `✈️` for aviation)
