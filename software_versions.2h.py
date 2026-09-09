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

   Optional "installed" maps a watch name to where its installed version
   can be read, so rows render as "installed → latest" when behind:

     "compose_file": "/Users/me/nix-config/stacks/homelab/docker-compose.yml",
     "installed": {
       "Thaw appcast (Sparkle releases)": "app:Thaw",
       "macOS 27 betas": "macos",
       "llm CLI": "cmd:/Users/me/.local/bin/llm --version",
       "AdGuard Home": "compose:adguard/adguardhome",
       "NixOS release": {"type": "file", "path": "/Users/me/nix-config/flake.nix",
                         "regex": "nixos-([0-9.]+)"}
     }

   app:<Name> reads /Applications/<Name>.app's CFBundleShortVersionString
   (watches named after an app bundle need no mapping), macos uses sw_vers,
   cmd: takes the last version-looking token of the command's output, and
   compose:<image> reads the "# vX.Y.Z" comment on that image line of
   compose_file. A watch with no source shows only the latest version.

   Optional "notes" maps a watch name to its release-notes page for the
   submenu. Without it, GitHub API watches link to the repo's releases page,
   Homebrew cask API watches to the cask page, and others to the watched URL.

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
import plistlib
import re
import shlex
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
VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.]+)*(?: beta \d+)?\b")
SKIP_LINES = {"[", "]", "{", "}"}
ICON = "sfimage=app.badge"

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
        "installed": cfg.get("installed") or {},
        "compose_file": str(cfg.get("compose_file", "")),
        "notes": cfg.get("notes") or {},
    }


def notes_url(name: str, watch_url: str, cfg: dict) -> str:
    """Release-notes page for a watch: config override, else derived from the
    watch URL (GitHub API -> the repo's releases page, Homebrew cask API -> the
    cask page), else the watched URL itself."""
    override = cfg.get("notes", {}).get(name)
    if override:
        return str(override)
    m = re.match(r"https://api\.github\.com/repos/([^/]+/[^/]+)/", watch_url)
    if m:
        return f"https://github.com/{m.group(1)}/releases"
    m = re.match(r"https://formulae\.brew\.sh/api/cask/([^.]+)\.json", watch_url)
    if m:
        return f"https://formulae.brew.sh/cask/{m.group(1)}"
    return watch_url


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

    def first_seen(self, uuid: str) -> int:
        """Timestamp of the oldest stored snapshot (0 if none)."""
        status, text = self.request("GET", f"/api/v1/watch/{uuid}/history")
        if status != 200:
            return 0
        try:
            keys = [int(k) for k in json.loads(text).keys()]
            return min(keys) if keys else 0
        except (ValueError, AttributeError):
            return 0

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
    """'Name · 1.2.3' -> ('Name', '1.2.3'); 'Name' -> ('Name', None).

    Strips every trailing version segment, so a title that picked up a stale
    duplicate ('Name · 1.2 · 1.2.3') heals back to 'Name' on the next write."""
    name, version = title.strip(), None
    while TITLE_SEP in name:
        head, tail = name.rsplit(TITLE_SEP, 1)
        if not VERSION_RE.fullmatch(tail.strip()):
            break
        if version is None:
            version = tail.strip()
        name = head.strip()
    return name, version


# ─── Installed versions ──────────────────────────────────────────────────


def _app_version(app_name: str) -> str | None:
    path = f"/Applications/{app_name}.app/Contents/Info.plist"
    try:
        with open(path, "rb") as f:
            info = plistlib.load(f)
        v = info.get("CFBundleShortVersionString") or info.get("CFBundleVersion")
        return str(v).strip() if v else None
    except Exception:
        return None


def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=REQUEST_TIMEOUT)
        return (r.stdout or "") + (r.stderr or "")
    except (subprocess.TimeoutExpired, OSError):
        return ""


def read_installed(spec, name: str, cfg: dict) -> tuple[str | None, str]:
    """Resolve a watch's installed version. Returns (version, source label)."""
    if spec is None:
        # Zero-config default: a watch named after an app bundle.
        return _app_version(name), f"app:{name}"
    if isinstance(spec, dict):
        kind = spec.get("type", "")
        if kind == "file":
            try:
                text = Path(os.path.expanduser(spec["path"])).read_text()
                m = re.search(spec["regex"], text)
                return (m.group(1) if m else None), f"file:{Path(spec['path']).name}"
            except Exception:
                return None, f"file:{spec.get('path', '?')}"
        return None, f"unknown type {kind!r}"
    spec = str(spec)
    if spec == "macos":
        ver = _run(["sw_vers", "-productVersion"]).strip()
        build = _run(["sw_vers", "-buildVersion"]).strip()
        return (f"{ver} ({build})" if ver else None), "sw_vers"
    if spec.startswith("app:"):
        return _app_version(spec[4:]), spec
    if spec.startswith("cmd:"):
        out = _run(shlex.split(spec[4:]))
        hits = VERSION_RE.findall(out)
        return (hits[-1] if hits else None), spec
    if spec.startswith("compose:"):
        image = spec[8:]
        try:
            for line in Path(cfg["compose_file"]).read_text().splitlines():
                if "image:" in line and image in line and "#" in line:
                    m = VERSION_RE.search(line.split("#", 1)[1])
                    return (m.group(0) if m else None), spec
        except Exception:
            pass
        return None, spec
    return None, spec


def _version_key(v: str):
    """(numeric tuple, is_prerelease) for ordering comparisons."""
    v = v.strip().lower()
    v = re.sub(r"^(version\s+|v)", "", v)
    m = re.match(r"(\d+(?:\.\d+)*)", v)
    nums = tuple(int(x) for x in m.group(1).split(".")) if m else ()
    rest = v[m.end():] if m else v
    prerelease = bool(re.search(r"(alpha|beta|rc|preview|dev)", rest))
    return nums, prerelease


def compare_versions(installed: str | None, latest: str | None, latest_full: str = "") -> str:
    """'behind', 'current', 'ahead', or 'unknown'."""
    if not installed or not latest:
        return "unknown"
    b_i = re.search(r"\(([0-9A-Za-z]+)\)", installed)
    b_l = re.search(r"\(([0-9A-Za-z]+)\)", latest_full or latest)
    if b_i and b_l:  # macOS-style build ids are the reliable comparison
        return "current" if b_i.group(1) == b_l.group(1) else "behind"
    ni, pi = _version_key(installed)
    nl, pl = _version_key(latest)
    if not ni or not nl:
        return "current" if installed.strip().lstrip("v") == latest.strip().lstrip("v") else "unknown"
    # Pad so 7.3 and 7.3.0 compare equal.
    width = max(len(ni), len(nl))
    ni += (0,) * (width - len(ni))
    nl += (0,) * (width - len(nl))
    if ni < nl:
        return "behind"
    if ni > nl:
        return "ahead"
    if pi and not pl:
        return "behind"
    return "current"


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
        last_changed = int(w.get("last_changed") or 0)
        # When the current version was first observed: the last detected change,
        # or, for a watch that has never changed, its first snapshot.
        since = last_changed or (client.first_seen(uuid) if snapshot else 0)
        installed, source = read_installed(cfg["installed"].get(name), name, cfg)
        status = compare_versions(installed, version, first_line)

        new_title = f"{name}{TITLE_SEP}{version}" if version is not None else None
        if cfg["update_titles"] and new_title is not None and new_title != title.strip():
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
                "last_changed": last_changed,
                "since": since,
                "installed": installed,
                "source": source,
                "status": status,
                "last_checked": int(w.get("last_checked") or 0),
                "last_error": w.get("last_error") or False,
                "viewed": bool(w.get("viewed", True)),
                "link": w.get("link") or w.get("url") or "",
                "notes": notes_url(name, w.get("url") or "", cfg),
            }
        )

    debug(f"{len(items)} watches, {puts} title updates")
    # Apps that are behind first, then everything else, newest change first.
    items.sort(key=lambda i: (i["status"] != "behind", i["since"] == 0, -i["since"], i["name"].lower()))
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


def format_date(ts: int) -> str:
    """mm-dd-yy, the date the current version was first seen."""
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d-%y")


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
    behind = sum(1 for i in items if i.get("status") == "behind")
    has_error = any(i["last_error"] for i in items) or cached_at is not None

    bar = str(behind) if behind else ""
    if has_error:
        bar = f"{bar} ⚠️".strip()
    print(f"{bar} | {ICON}".strip() if bar else f"| {ICON}")
    print("---")

    overview = f"{base}/?tag={tag_uuid}" if tag_uuid else f"{base}/"
    summary = f"{behind} behind" if behind else "all current"
    print(f"Software versions ({cfg['tag']}): {summary} | href={overview}")

    if not items:
        print(f"No watches tagged \"{cfg['tag']}\" | color={COLOR_DIM}")

    for i in items:
        name = esc_label(i["name"])
        version = i["version"] or "no snapshot yet"
        status = i.get("status", "unknown")
        installed = i.get("installed")
        if status == "behind":
            version = f"{installed} → {version}"
        marker = "• " if (not i["viewed"] and i["last_changed"]) else ""
        attrs = [f"href={base}/diff/{i['uuid']}"]
        if i["last_error"]:
            attrs.append(f"color={COLOR_ERROR}")
            attrs.append(f'tooltip="{esc_tooltip(str(i["last_error"]))}"')
        elif status == "behind":
            attrs.append(f"color={COLOR_ACCENT}")
        elif status == "unknown":
            attrs.append(f'tooltip="{esc_tooltip("Installed version unknown (source: " + str(i.get("source")) + ")")}"')
        if i["version"] is None and i["first_line"]:
            attrs.append(f'tooltip="{esc_tooltip("Snapshot starts: " + i["first_line"])}"')
        since = i.get("since") or 0
        date = f"[{format_date(since)}] " if since else ""
        print(f"{date}{marker}{name}  {version} | {' '.join(attrs)}")
        if i["link"]:
            print(f"{date}{marker}{name}  {version} | href={i['link']} alternate=true")
        changed = (
            f"changed {format_ago(i['last_changed'], now)}"
            if i["last_changed"]
            else "not changed yet"
        )
        if i.get("notes"):
            print(f"--Release notes | href={i['notes']}")
        inst = f"installed {installed}" if installed else "installed version unknown"
        print(f"--{inst} ({status}, via {esc_label(str(i.get('source')))}) | color={COLOR_DIM}")
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
