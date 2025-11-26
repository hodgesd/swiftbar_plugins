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

**cprt.1d.py**: Authenticated RSS feed parser
- Demonstrates handling private/authenticated feeds
- Custom HTML stripping for content display
- Debug mode for troubleshooting feed issues

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
