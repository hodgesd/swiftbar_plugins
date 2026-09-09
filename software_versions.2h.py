#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

# <swiftbar.title>Software Versions</swiftbar.title>
# <swiftbar.version>v1.0</swiftbar.version>
# <swiftbar.author>Derrick Hodges</swiftbar.author>
# <swiftbar.author.github>hodgesd</swiftbar.author.github>
# <swiftbar.desc>Current versions of tracked apps, read from a self-hosted changedetection.io (watches tagged "software"). Configure via ~/.config/swiftbar-plugins/software_versions.json</swiftbar.desc>
# <swiftbar.dependencies>uv</swiftbar.dependencies>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>false</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
# <swiftbar.environment>[SOFTWARE_VERSIONS_BASE_URL:, SOFTWARE_VERSIONS_TAG:software]</swiftbar.environment>

"""
Software Versions — SwiftBar Plugin
===================================
Lists the current version of every app tracked by a self-hosted
changedetection.io instance (watches carrying one tag, "software" by
default), sorted by most recently changed. changedetection.io stores
each watch's latest snapshot but never shows it on its overview page,
so this plugin reads the snapshot through the API, shows the version in
the menu bar, and writes it back into the watch title
("<name> · <version>") so the overview page shows it as well.

Refresh: every 2 hours (filename convention: software_versions.2h.py).
The watches themselves check on their own schedules; this only reads
what changedetection.io already knows.

SETUP
-----
1. Config file ~/.config/swiftbar-plugins/software_versions.json:

     {
       "base_url": "https://changes.example.ts.net",
       "tag": "software",
       "recent_days": 14,
       "update_titles": true,
       "keychain_service": "changedetection.io",
       "keychain_account": "api"
     }

   Only base_url is required. SOFTWARE_VERSIONS_BASE_URL and
   SOFTWARE_VERSIONS_TAG env vars (SwiftBar settings panel) override the
   file.

2. API key in the macOS Keychain (Settings > API in changedetection.io):

     security add-generic-password -U -s changedetection.io -a api -w '<key>'

   The plugin reads it with `security find-generic-password -w`. The
   first run may show one Keychain "Allow" prompt for `security`.
   SOFTWARE_VERSIONS_API_KEY in the environment bypasses the Keychain
   (useful for testing from a shell; not recommended for SwiftBar's
   settings panel, which stores env vars in plain text).

WATCH CONVENTION
----------------
Filter each software watch so its current version is the first
version-looking token in the snapshot (e.g. `xpath://item/title` on a
Sparkle appcast, `json:$[*].tag_name` on a GitHub releases API URL).
Titles are managed as "<name> · <version>"; the part after the last
" · " is replaced on every version change, the name is left alone.
"""

import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ─── Constants ───────────────────────────────────────────────────────────

REQUEST_TIMEOUT = 10
CACHE_MAX_AGE_HOURS = 72
TITLE_SEP = " · "
VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.]+)?\b")
SKIP_LINES = {"[", "]", "{", "}"}
ICON = "sfimage=shippingbox"

COLOR_ACCENT = "#FF9F0A"
COLOR_ERROR = "#FF3B30"
COLOR_DIM = "#8E8E93"
FONT_MONO = "font=SFMono-Regular size=11"

DEBUG = os.environ.get("SOFTWARE_VERSIONS_DEBUG") == "1"

# ─── Paths ───────────────────────────────────────────────────────────────

_PLUGIN_NAME = os.path.splitext(os.path.splitext(os.path.basename(__file__))[0])[0]
CONFIG_DIR = os.path.expanduser("~/.config/swiftbar-plugins")
CONFIG_PATH = os.path.join(CONFIG_DIR, f"{_PLUGIN_NAME}.json")

_plugin_data = os.getenv("SWIFTBAR_PLUGIN_DATA_PATH", "")
DATA_DIR = Path(_plugin_data).parent if _plugin_data else Path.home()
CACHE_FILE = DATA_DIR / "software_versions_cache.json"


def debug(msg: str) -> None:
    if DEBUG:
        print(f"[software_versions] {msg}", file=sys.stderr)


# ─── Config ──────────────────────────────────────────────────────────────


def print_setup(problem: str, lines: list[str]) -> None:
    """Render a Setup menu (valid SwiftBar output) and exit cleanly."""
    print(f"Setup | {ICON}")
    print("---")
    print(f"{problem} | {FONT_MONO} color={COLOR_ERROR}")
    print("---")
    for line in lines:
        print(f"{line} | {FONT_MONO} color={COLOR_DIM}")
    print("---")
    print("Refresh | refresh=true")
    sys.exit(0)


def load_config() -> dict:
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
        except Exception:
            pass

    base_url = str(
        os.environ.get("SOFTWARE_VERSIONS_BASE_URL") or cfg.get("base_url", "")
    ).rstrip("/")
    tag = str(os.environ.get("SOFTWARE_VERSIONS_TAG") or cfg.get("tag", "software"))

    if not base_url:
        print_setup(
            "Missing config: base_url",
            [
                "Create config file:",
                f"  {CONFIG_PATH}",
                "  {",
                '    "base_url": "https://changes.example.ts.net",',
                '    "tag": "software",',
                '    "recent_days": 14,',
                '    "update_titles": true,',
                '    "keychain_service": "changedetection.io",',
                '    "keychain_account": "api"',
                "  }",
                "Or set SOFTWARE_VERSIONS_BASE_URL in SwiftBar settings",
            ],
        )

    return {
        "base_url": base_url,
        "tag": tag,
        "recent_days": int(cfg.get("recent_days", 14)),
        "update_titles": bool(cfg.get("update_titles", True)),
        "keychain_service": str(cfg.get("keychain_service", "changedetection.io")),
        "keychain_account": str(cfg.get("keychain_account", "api")),
    }


def get_api_key(cfg: dict) -> str:
    env_key = os.environ.get("SOFTWARE_VERSIONS_API_KEY", "").strip()
    if env_key:
        debug("api key from environment")
        return env_key
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                cfg["keychain_service"],
                "-a",
                cfg["keychain_account"],
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=REQUEST_TIMEOUT,
        )
        key = result.stdout.strip()
        if result.returncode == 0 and key:
            debug("api key from keychain")
            return key
    except (subprocess.TimeoutExpired, OSError):
        pass
    print_setup(
        "API key not found in Keychain",
        [
            "Add it once (key from changedetection.io Settings > API):",
            "  security add-generic-password -U \\",
            f"    -s {cfg['keychain_service']} -a {cfg['keychain_account']} -w '<key>'",
            "Then click Refresh and allow `security` access if asked.",
        ],
    )
    return ""  # unreachable


# ─── changedetection.io API ──────────────────────────────────────────────


class AuthError(Exception):
    pass


class Client:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    def request(self, method: str, path: str, body: dict | None = None):
        """Return (status, text). Raises URLError on network failure."""
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data, method=method
        )
        req.add_header("x-api-key", self.api_key)
        req.add_header("Accept", "application/json, text/plain")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace") if exc.fp else ""
            if exc.code in (401, 403):
                raise AuthError(text.strip() or f"HTTP {exc.code}") from exc
            return exc.code, text

    def list_watches(self, tag: str) -> dict:
        status, text = self.request(
            "GET", f"/api/v1/watch?tag={urllib.parse.quote(tag)}"
        )
        if status != 200:
            raise RuntimeError(f"watch list HTTP {status}")
        return json.loads(text)

    def latest_snapshot(self, uuid: str) -> str | None:
        status, text = self.request("GET", f"/api/v1/watch/{uuid}/history/latest")
        if status == 200:
            return text
        return None

    def set_title(self, uuid: str, title: str) -> bool:
        status, _ = self.request("PUT", f"/api/v1/watch/{uuid}", {"title": title})
        return status == 200


# ─── Version handling ────────────────────────────────────────────────────


def parse_version(text: str) -> tuple[str | None, str]:
    """Return (version, first_line). version is None if nothing matched."""
    first_line = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line in SKIP_LINES:
            continue
        if not first_line:
            first_line = line[:40]
        m = VERSION_RE.search(line)
        if m:
            return m.group(0), first_line
    return None, first_line


def split_title(title: str) -> tuple[str, str | None]:
    """'Name · 1.2.3' -> ('Name', '1.2.3'); 'Name' -> ('Name', None)."""
    if TITLE_SEP in title:
        name, tail = title.rsplit(TITLE_SEP, 1)
        if VERSION_RE.fullmatch(tail.strip()):
            return name.strip(), tail.strip()
    return title.strip(), None


# ─── Fetch ───────────────────────────────────────────────────────────────


def fetch_items(client: Client, cfg: dict) -> tuple[list[dict], str | None, list[str]]:
    watches = client.list_watches(cfg["tag"])
    items: list[dict] = []
    warnings: list[str] = []
    tag_uuid: str | None = None
    puts = 0

    for uuid, w in watches.items():
        title = w.get("title") or w.get("page_title") or w.get("url", uuid)
        name, stored_version = split_title(title)
        tags = w.get("tags") or []
        if tag_uuid is None and tags:
            tag_uuid = tags[0]

        snapshot = client.latest_snapshot(uuid)
        version, first_line = parse_version(snapshot) if snapshot else (None, "")

        if (
            cfg["update_titles"]
            and version is not None
            and version != stored_version
        ):
            new_title = f"{name}{TITLE_SEP}{version}"
            if client.set_title(uuid, new_title):
                puts += 1
                debug(f"title updated: {title!r} -> {new_title!r}")
            else:
                warnings.append(f"Could not update title for {name}")

        items.append(
            {
                "uuid": uuid,
                "name": name,
                "version": version,
                "first_line": first_line,
                "last_changed": int(w.get("last_changed") or 0),
                "last_checked": int(w.get("last_checked") or 0),
                "last_error": w.get("last_error") or False,
                "viewed": bool(w.get("viewed", True)),
                "link": w.get("link") or w.get("url") or "",
            }
        )

    debug(f"{len(items)} watches, {puts} title updates")
    items.sort(key=lambda i: (i["last_changed"] == 0, -i["last_changed"], i["name"].lower()))
    return items, tag_uuid, warnings


# ─── Cache ───────────────────────────────────────────────────────────────


def save_cache(items: list[dict], tag_uuid: str | None) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(
                {"timestamp": time.time(), "tag_uuid": tag_uuid, "items": items}
            )
        )
    except Exception:
        pass


def load_cache() -> dict | None:
    try:
        payload = json.loads(CACHE_FILE.read_text())
        if time.time() - float(payload["timestamp"]) > CACHE_MAX_AGE_HOURS * 3600:
            return None
        return payload
    except Exception:
        return None


# ─── Render ──────────────────────────────────────────────────────────────


def format_ago(ts: int, now: float | None = None) -> str:
    if not ts:
        return "never"
    secs = int((now or time.time()) - ts)
    if secs < 90:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 48:
        return f"{hours}h ago"
    days = hours // 24
    if days < 14:
        return f"{days}d ago"
    return f"{days // 7}w ago"


def esc_tooltip(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace('"', '\\"')
    text = text.replace("|", "\\|")
    text = text.replace("\n", "\\n")
    return text


def esc_label(text: str) -> str:
    return text.replace("|", " ").replace("\n", " ")


def render(
    items: list[dict],
    tag_uuid: str | None,
    cfg: dict,
    warnings: list[str],
    cached_at: float | None = None,
) -> None:
    now = time.time()
    base = cfg["base_url"]
    recent_cutoff = now - cfg["recent_days"] * 86400
    recent = sum(1 for i in items if i["last_changed"] and i["last_changed"] >= recent_cutoff)
    has_error = any(i["last_error"] for i in items) or cached_at is not None

    bar = str(recent) if recent else ""
    if has_error:
        bar = f"{bar} ⚠️".strip()
    print(f"{bar} | {ICON}".strip() if bar else f"| {ICON}")
    print("---")

    overview = f"{base}/?tag={tag_uuid}" if tag_uuid else f"{base}/"
    print(f"Software versions ({cfg['tag']}) | href={overview}")

    if not items:
        print(f"No watches tagged \"{cfg['tag']}\" | color={COLOR_DIM}")

    for i in items:
        name = esc_label(i["name"])
        version = i["version"] or "no snapshot yet"
        marker = "• " if (not i["viewed"] and i["last_changed"]) else ""
        attrs = [f"href={base}/diff/{i['uuid']}"]
        if i["last_error"]:
            attrs.append(f"color={COLOR_ERROR}")
            attrs.append(f'tooltip="{esc_tooltip(str(i["last_error"]))}"')
        elif i["last_changed"] and i["last_changed"] >= recent_cutoff:
            attrs.append(f"color={COLOR_ACCENT}")
        if i["version"] is None and i["first_line"]:
            attrs.append(f'tooltip="{esc_tooltip("Snapshot starts: " + i["first_line"])}"')
        print(f"{marker}{name}  {version} | {' '.join(attrs)}")
        if i["link"]:
            print(f"{marker}{name}  {version} | href={i['link']} alternate=true")
        changed = (
            f"changed {format_ago(i['last_changed'], now)}"
            if i["last_changed"]
            else "not changed yet"
        )
        print(f"--{changed} | color={COLOR_DIM}")
        print(f"--checked {format_ago(i['last_checked'], now)} | color={COLOR_DIM}")
        print(f"--Open diff | href={base}/diff/{i['uuid']}")
        print(f"--Open preview | href={base}/preview/{i['uuid']}")
        if i["link"]:
            print(f"--Open source | href={i['link']}")

    if warnings or cached_at is not None:
        print("---")
        for w in warnings:
            print(f"⚠️ {esc_label(w)} | color={COLOR_DIM}")
        if cached_at is not None:
            host = urllib.parse.urlsplit(base).netloc
            stamp = datetime.datetime.fromtimestamp(cached_at).strftime("%-m/%-d %H:%M")
            print(f"⚠️ Showing cached data from {stamp}: {host} unreachable | color={COLOR_DIM}")

    print("---")
    print(f"Updated {datetime.datetime.now().strftime('%H:%M')} | color={COLOR_DIM}")
    print("Refresh | refresh=true")


# ─── Main ────────────────────────────────────────────────────────────────


def main() -> None:
    cfg = load_config()
    api_key = get_api_key(cfg)
    client = Client(cfg["base_url"], api_key)
    try:
        items, tag_uuid, warnings = fetch_items(client, cfg)
        save_cache(items, tag_uuid)
        render(items, tag_uuid, cfg, warnings)
    except AuthError as exc:
        print(f"⚠️ | {ICON}")
        print("---")
        print(f"changedetection.io rejected the API key: {esc_label(str(exc))} | color={COLOR_ERROR}")
        print(
            f"Keychain item: -s {cfg['keychain_service']} -a {cfg['keychain_account']} | {FONT_MONO} color={COLOR_DIM}"
        )
        print("---")
        print("Refresh | refresh=true")
    except Exception as exc:
        cached = load_cache()
        if cached:
            render(cached["items"], cached.get("tag_uuid"), cfg, [], cached_at=cached["timestamp"])
        else:
            print(f"⚠️ | {ICON}")
            print("---")
            print(f"Error: {esc_label(str(exc))} | color={COLOR_ERROR}")
            print("---")
            print("Refresh | refresh=true")


if __name__ == "__main__":
    main()
