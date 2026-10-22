#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

# <swiftbar.title>Neighborhood Real Estate Monitor</swiftbar.title>
# <swiftbar.version>v2.3</swiftbar.version>
# <swiftbar.author>Derrick</swiftbar.author>
# <swiftbar.author.github>hdgs</swiftbar.author.github>
# <swiftbar.desc>Active, pending and sold homes around your address plus your Redfin Estimate, with NEW and price-drop tracking. Configure via ~/.config/swiftbar-plugins/home_comps_uv.json</swiftbar.desc>
# <swiftbar.dependencies>uv</swiftbar.dependencies>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>false</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
# <swiftbar.environment>[REDFIN_ADDRESS:, REDFIN_CITY:, REDFIN_STATE:, REDFIN_ZIP:, REDFIN_LAT:, REDFIN_LON:, REDFIN_RADIUS:3, REDFIN_SOLD_DAYS:90, REDFIN_URL:, REDFIN_PROPERTY_TYPES:house, REDFIN_MAP:, REDFIN_COMP_DAYS:365, REDFIN_SUBDIVISION:, REDFIN_EXCLUDE_SUBDIVISIONS:, REDFIN_APPRECIATION:3, REDFIN_TITLE:comps]</swiftbar.environment>

"""
Neighborhood Real Estate Monitor — SwiftBar Plugin
===================================================
Data sources (all public, no key):
  * Redfin map search JSON (stingray/api/gis) queried with a bounding polygon
    around your home, then trimmed to a true circle of `radius` miles.
      active  = status 9   (active + coming soon)
      pending = status 130 (active under contract + pending)
      sold    = status 9 + sold_within_days
    `mpt=99` is required or Redfin ignores the polygon.
  * Redfin's AVM JSON (stingray/api/home/details/avm) for the Redfin Estimate,
    keyed by the property id in your home's page URL. The URL (and year built)
    is auto-discovered from the map search; set `redfin_url` to override. The
    HTML home page sits behind an AWS WAF challenge, and the autocomplete /
    initialInfo APIs return 403. Redfin requests are spaced REDFIN_GAP apart.
  * US Census geocoder, only when lat/lon are not configured.

Config: ~/.config/swiftbar-plugins/home_comps_uv.json
    {
      "address":   "123 Main St",      required
      "city":      "Springfield",      required
      "state":     "IL",               required
      "zip":       "62704",            required
      "lat":       39.7817,            optional, geocoded when absent
      "lon":       -89.6501,           optional
      "radius":    1.5,                optional, miles (default 3)
      "sold_days": 90,                 optional (default 90)
      "redfin_url": "https://www.redfin.com/IL/.../home/123"   optional
      "beds":      5,                  optional; overrides Redfin's bedroom count
                                       for the comps only (not the Redfin Estimate)
      "property_types": ["house"]      optional; any of house, condo, townhouse,
                                       multi-family, land, other, manufactured, co-op
      "map":       "~/Documents/Maps/area.html"   optional; local neighborhood map,
                                       linked beside "View on Redfin" when the file exists
      "sqft":      3000,               optional; overrides Redfin's square footage for
                                       the comps only
      "comp_days": 365,                optional; sold window for the comps value (default
                                       365); the Sold list keeps sold_days
      "subdivision": "Orchards",       optional; sales in this subdivision rank as
                                       closer comps
      "exclude_subdivisions": ["Brookhill", "Plum Hill"]   optional; never used as comps
      "appreciation": 3.5,             optional; %/yr comps are adjusted up for time
                                       since sale (default 3)
      "title":     "comps",            optional; menu bar value: "comps" (default,
                                       falls back to Redfin) or "redfin"
    }
Subdivision names are matched case-insensitively as substrings of Redfin's
MLS subdivision field. When Redfin has none (or just the city), the St. Clair
County, IL parcel layer is asked by coordinates; answers are cached for good.
REDFIN_* environment variables work as a fallback for every key.

Caches live in ~/.cache/swiftbar-plugins/home_comps_uv/. A failed fetch falls
back to the last good copy and says so in the menu. listings_state.json keeps
first-seen dates and prices per property for the NEW / price-change badges,
plus a daily history of your Redfin Estimate for the trend line. Sold homes
that were tracked while listed get sale-to-list and days-to-sell figures.

Comps value = median price of the most similar sales in the last comp_days
(size, beds, baths, age, distance, favored subdivision; within COMP_SIZE_RANGE
of your size), each adjusted SIZE_ADJ per sqft toward your size and
`appreciation` %/yr for time since sale, plus BED_ADJ per bedroom you have
beyond the comps' median. The comps are listed under the value; the closest
few that sold within sold_days are starred in the Sold list.

CLI:
  ./home_comps_uv.8hr.py            render the menu
  ./home_comps_uv.8hr.py --force    mark caches stale (used by "Force Refresh")
  ./home_comps_uv.8hr.py --check    fetch everything, print counts, exit 1 on failure
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

# ─── Paths and tunables ──────────────────────────────────────────────────

SCRIPT_PATH = os.path.abspath(__file__)
PLUGIN_NAME = os.path.basename(__file__).split(".")[0]
CONFIG_PATH = os.path.expanduser(f"~/.config/swiftbar-plugins/{PLUGIN_NAME}.json")
CACHE_DIR = os.path.expanduser(f"~/.cache/swiftbar-plugins/{PLUGIN_NAME}")
STATE_FILE = "listings_state.json"
LOCATION_FILE = "location.json"
SUBDIV_FILE = "subdivisions.json"  # property id -> county subdivision, kept for good

LIST_TTL = 2 * 3600        # listings: manual refreshes within 2h reuse the cache
HOME_TTL = 12 * 3600       # Redfin Estimate moves at most daily
URL_RETRY_SEC = 24 * 3600  # retry home-URL discovery at most daily
REDFIN_GAP = 1.5           # seconds between Redfin requests; never burst them
NEW_DAYS = 7               # NEW badge: first listed within this many days
CHANGE_DAYS = 30           # show a price change made within this many days
STATE_KEEP_DAYS = 120      # forget properties not seen for this long
MAX_ROWS = 15
ADDR_W = 20               # addresses print without the street type
SUBDIV_W = 18             # comps submenu subdivision column (abbreviated names)
COMP_COUNT = 5             # sales behind the comps value
BED_ADJ = 10_000           # comps value adjustment per bedroom vs the comps' median
COMP_SIZE_RANGE = (0.75, 1.33)  # comps' sqft vs yours; $/sf doesn't carry across sizes
SIZE_ADJ = 50              # $ per sqft of size difference when adjusting a comp's price
SUBDIV_BONUS = 0.4         # similarity credit for a sale in the favored subdivision
STAR_COUNT = 3             # closest sales starred and listed first
TREND_DAYS = 30            # estimate trend window
TREND_MIN_DAYS = 7         # show a shorter trend once there's this much history
ESTIMATE_KEEP_DAYS = 400

# Redfin's uipt search filter; results carry the same code as uiPropertyType.
PROPERTY_TYPES = {"house": 1, "condo": 2, "townhouse": 3, "multi-family": 4, "land": 5,
                  "other": 6, "manufactured": 7, "co-op": 8}
DEFAULT_PROPERTY_TYPES = ["house"]

GIS_URL = "https://www.redfin.com/stingray/api/gis"
AVM_URL = "https://www.redfin.com/stingray/api/home/details/avm"
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
PARCEL_URL = ("https://services3.arcgis.com/zlwh2vfgpx3iW3YR/arcgis/rest/services/"
              "Parcel_for_parcel_viewer/FeatureServer/0/query")  # St. Clair County, IL
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)

# ─── Styling ─────────────────────────────────────────────────────────────

MONO_F = "font=SFMono-Regular"
MONO = f"{MONO_F} size=12"
# Table rows need a real monospace face; SFMono isn't installed system-wide, so
# "SFMono-Regular" silently falls back to the proportional system font.
TABLE = "font=Menlo size=11"
TABLE_BOLD = "font=Menlo-Bold size=11"
ROW_INDENT = "     "
HEADER_FONT = "font=SF Pro Display size=13"
TITLE_FONT = "font=SF Pro Display size=14"
# SwiftBar takes color=<light mode>,<dark mode>. The light-mode halves are
# Apple's darker accessible variants, readable on the pale menu background.
DIMMED = "color=#6E6E73,#8E8E93"
ACCENT_GREEN = "color=#1F7A35,#34C759"
ACCENT_RED = "color=#C4000F,#FF3B30"
ACCENT_BLUE = "color=#0040DD,#0A84FF"
ACCENT_ORANGE = "color=#B33000,#FF9500"
ACCENT_TEAL = "color=#00707F,#30B0C7"
LABEL_COLOR = "color=#3A3A3C,#AEAEB2"
WHITE = "color=#000000,#FFFFFF"


# ─── Config ──────────────────────────────────────────────────────────────

class ConfigError(Exception):
    def __init__(self, missing):
        super().__init__(", ".join(missing))
        self.missing = missing


def load_config(path=CONFIG_PATH, env=os.environ):
    cfg = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

    def get(key, env_key, default=""):
        val = cfg.get(key)
        if val in (None, ""):
            val = env.get(env_key, default)
        return val

    out = {
        "address": str(get("address", "REDFIN_ADDRESS")).strip(),
        "city": str(get("city", "REDFIN_CITY")).strip(),
        "state": str(get("state", "REDFIN_STATE")).strip(),
        "zip": str(get("zip", "REDFIN_ZIP")).strip(),
        "lat": _float(get("lat", "REDFIN_LAT")),
        "lon": _float(get("lon", "REDFIN_LON")),
        "radius": _float(get("radius", "REDFIN_RADIUS", "3")) or 3.0,
        "sold_days": _int(get("sold_days", "REDFIN_SOLD_DAYS", "90")) or 90,
        "redfin_url": str(get("redfin_url", "REDFIN_URL")).strip(),
        "beds": _float(get("beds", "REDFIN_BEDS")),
        "property_types": parse_property_types(get("property_types", "REDFIN_PROPERTY_TYPES")),
        "map": os.path.expanduser(str(get("map", "REDFIN_MAP")).strip()),
        "sqft": _int(get("sqft", "REDFIN_SQFT")),
        "comp_days": _int(get("comp_days", "REDFIN_COMP_DAYS", "365")) or 365,
        "subdivision": str(get("subdivision", "REDFIN_SUBDIVISION")).strip(),
        "exclude_subdivisions": parse_list(get("exclude_subdivisions",
                                               "REDFIN_EXCLUDE_SUBDIVISIONS")),
        "appreciation": _float(get("appreciation", "REDFIN_APPRECIATION", "3")) or 0.0,
        "title": "redfin" if str(get("title", "REDFIN_TITLE")).strip().lower() == "redfin"
                 else "comps",
    }
    missing = [k for k in ("address", "city", "state", "zip") if not out[k]]
    if missing:
        raise ConfigError(missing)
    return out


def parse_property_types(val):
    """List or comma string of PROPERTY_TYPES names → sorted uipt codes."""
    names = val if isinstance(val, list) else str(val or "").split(",")
    codes = {PROPERTY_TYPES.get(str(n).strip().lower()) for n in names} - {None}
    return sorted(codes or {PROPERTY_TYPES[n] for n in DEFAULT_PROPERTY_TYPES})


# ─── Small helpers ───────────────────────────────────────────────────────

def parse_list(val):
    if isinstance(val, str):
        val = val.split(",")
    return [str(v).strip() for v in (val or []) if str(v).strip()]


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _val(x):
    """Redfin wraps most fields as {"value": ..., "level": ...}."""
    return x.get("value") if isinstance(x, dict) else x


def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return r * 2 * math.asin(math.sqrt(a))


def bbox_poly(lat, lon, radius_mi):
    """Closed square polygon around a point, in Redfin's 'lon lat,lon lat,...' form."""
    dlat = radius_mi / 69.0
    dlon = radius_mi / (69.0 * math.cos(math.radians(lat)))
    pts = [(lon - dlon, lat - dlat), (lon + dlon, lat - dlat),
           (lon + dlon, lat + dlat), (lon - dlon, lat + dlat), (lon - dlon, lat - dlat)]
    return ",".join(f"{x:.5f} {y:.5f}" for x, y in pts)


def sold_filter_token(days):
    """Redfin's sold-window URL token for the 'View all' link."""
    for limit, token in ((7, "1wk"), (30, "1mo"), (90, "3mo"), (180, "6mo"),
                         (365, "1yr"), (730, "2yr"), (1095, "3yr")):
        if days <= limit:
            return f"sold-{token}"
    return "sold-5yr"


# ─── HTTP ────────────────────────────────────────────────────────────────

class FetchError(Exception):
    pass


_redfin_lock = threading.Lock()
_redfin_last = 0.0


def _pace(url):
    """Space Redfin requests REDFIN_GAP apart, even from parallel jobs."""
    global _redfin_last
    if urllib.parse.urlsplit(url).hostname != "www.redfin.com":
        return
    with _redfin_lock:
        wait = _redfin_last + REDFIN_GAP - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _redfin_last = time.monotonic()


def http_get(url, params=None, timeout=25):
    _pace(url)
    if params:
        url = url + "?" + urllib.parse.urlencode(params, safe=",", quote_via=urllib.parse.quote)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json,text/html,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.redfin.com/",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:  # Redfin's bot challenge answers 202
                raise FetchError(f"HTTP {resp.status} (bot challenge)")
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise FetchError(f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise FetchError(f"network: {e.reason}") from None
    except TimeoutError:
        raise FetchError("timed out") from None


def strip_xssi(body):
    return body[4:] if body.startswith("{}&&") else body


# ─── Parsers (pure) ──────────────────────────────────────────────────────

def normalize_home(h):
    ll = _val(h.get("latLong")) or {}
    sold = h.get("soldDate")
    url = h.get("url") or ""
    return {
        "id": str(h.get("propertyId") or h.get("listingId") or url),
        "address": (_val(h.get("streetLine")) or "").strip() or "—",
        "url": f"https://www.redfin.com{url}" if url else "",
        "price": _int(_val(h.get("price"))),
        "beds": _float(h.get("beds")),
        "baths": _float(h.get("baths")),
        "sqft": _int(_val(h.get("sqFt"))),
        "ppsf": _int(_val(h.get("pricePerSqFt"))),
        "dom": _int(_val(h.get("dom"))),
        "status": h.get("mlsStatus") or "",
        "sold_date": (datetime.fromtimestamp(sold / 1000, tz=timezone.utc).date().isoformat()
                      if isinstance(sold, (int, float)) else None),
        "lat": _float(ll.get("latitude")),
        "lon": _float(ll.get("longitude")),
        "year_built": _int(_val(h.get("yearBuilt"))),
        "hoa": _int(_val(h.get("hoa"))),
        "ptype": _int(h.get("uiPropertyType")),
        "subdiv": (_val(h.get("location")) or "").strip() or None,  # MLS subdivision
    }


def parse_gis(body):
    try:
        data = json.loads(strip_xssi(body))
    except json.JSONDecodeError:
        raise FetchError("Redfin returned non-JSON (bot challenge?)") from None
    if data.get("resultCode") != 0:
        raise FetchError(f"Redfin: {data.get('errorMessage') or 'error'}")
    return [normalize_home(h) for h in (data.get("payload") or {}).get("homes") or []]


def parse_avm(body):
    """Redfin Estimate and facts for the subject property from the AVM JSON."""
    try:
        data = json.loads(strip_xssi(body))
    except json.JSONDecodeError:
        raise FetchError("Redfin returned non-JSON (bot challenge?)") from None
    if data.get("resultCode") != 0:
        raise FetchError(f"Redfin: {data.get('errorMessage') or 'error'}")
    p = data.get("payload") or {}
    est = _float(p.get("predictedValue"))
    if est is None:
        raise FetchError("Redfin has no estimate for this home")
    return {
        "estimate": _int(round(est)),
        "beds": _float(p.get("numBeds")),
        "baths": _float(p.get("numBaths")),
        "sqft": _int(_val(p.get("sqFt"))),
        "property_id": str(p["propertyId"]) if p.get("propertyId") else None,
    }


def property_id(url):
    m = re.search(r"/home/(\d+)", url or "")
    return m.group(1) if m else None


STREET_SUFFIXES = {"st", "street", "dr", "drive", "rd", "road", "ave", "avenue", "ln", "lane",
                   "ct", "court", "blvd", "pl", "place", "way", "cir", "circle", "ter", "pkwy"}


def match_home(homes, address):
    """Find the configured address among search results (number + street name)."""
    parts = re.sub(r"[.,#]", " ", address.lower()).split()
    if not parts:
        return None
    num = parts[0]
    names = [w for w in parts[1:] if w not in STREET_SUFFIXES and not w.isdigit()]
    key = names[0] if names else ""
    for h in homes:
        words = re.sub(r"[.,#]", " ", h["address"].lower()).split()
        if words and words[0] == num and key in words and h.get("url"):
            return h
    return None


def parse_census(body):
    matches = json.loads(body).get("result", {}).get("addressMatches") or []
    if not matches:
        raise FetchError("Census geocoder found no match for the address")
    c = matches[0]["coordinates"]
    return float(c["y"]), float(c["x"])


def within_radius(homes, lat, lon, radius):
    out = []
    for h in homes:
        if h["lat"] is None or h["lon"] is None:
            continue
        d = haversine_miles(lat, lon, h["lat"], h["lon"])
        if d <= radius:
            out.append({**h, "dist": round(d, 2)})
    return out


# ─── NEW / price-change tracking (pure) ──────────────────────────────────

def update_state(state, homes, today):
    """Record first-seen date and price history for every on-market home.

    First sight uses Redfin's days-on-market to back-date first_seen, so the
    very first run already knows which listings are new.
    """
    listings = state.setdefault("listings", {})
    t = today.isoformat()
    for h in homes:
        pid, price = h["id"], h["price"]
        rec = listings.get(pid)
        if rec is None:
            first = today - timedelta(days=h["dom"]) if h["dom"] is not None else today
            listings[pid] = {"address": h["address"], "first_seen": first.isoformat(),
                             "first_price": price, "price": price, "prev_price": None,
                             "price_changed": None, "last_seen": t}
            continue
        if price and rec.get("price") and price != rec["price"]:
            rec["prev_price"], rec["price_changed"] = rec["price"], t
        if price:
            rec["price"] = price
            rec["first_price"] = rec.get("first_price") or price
        rec["last_seen"] = t
    cutoff = (today - timedelta(days=STATE_KEEP_DAYS)).isoformat()
    for pid in [p for p, r in listings.items() if r.get("last_seen", "") < cutoff]:
        del listings[pid]
    return state


def annotate(h, state, today):
    """Attach is_new / change / orig_price to a listing from tracked state."""
    rec = (state.get("listings") or {}).get(h["id"]) or {}
    first = rec.get("first_seen")
    h["is_new"] = bool(first) and (today - date.fromisoformat(first)).days <= NEW_DAYS
    h["listed_on"] = first
    h["change"], h["orig_price"] = None, None
    changed = rec.get("price_changed")
    if changed and rec.get("prev_price") and h["price"] and \
            (today - date.fromisoformat(changed)).days <= CHANGE_DAYS:
        h["change"] = h["price"] - rec["prev_price"]
        h["changed_on"] = changed
    if rec.get("first_price") and h["price"] and rec["first_price"] != h["price"]:
        h["orig_price"] = rec["first_price"]
    return h


# ─── Estimate trend, comps, market pace (pure) ───────────────────────────

def record_estimate(state, estimate, today):
    hist = state.setdefault("estimates", {})
    if estimate:
        hist[today.isoformat()] = estimate
    cutoff = (today - timedelta(days=ESTIMATE_KEEP_DAYS)).isoformat()
    for d in [d for d in hist if d < cutoff]:
        del hist[d]
    return state


def estimate_trend(hist, today):
    """(change, pct, since_date) against the estimate TREND_DAYS ago, or against
    the oldest one once there's TREND_MIN_DAYS of history; None before that."""
    if not hist:
        return None
    days = sorted(hist)
    current = hist[days[-1]]
    window = (today - timedelta(days=TREND_DAYS)).isoformat()
    older = [d for d in days if d <= window]
    base = older[-1] if older else days[0]
    if (today - date.fromisoformat(base)).days < TREND_MIN_DAYS or not hist[base]:
        return None
    change = current - hist[base]
    return change, change / hist[base] * 100, base


def subdiv_matches(name, patterns):
    name = (name or "").lower()
    return bool(name) and any(p.lower() in name for p in patterns if p)


def similarity(h, subject, radius, favor=""):
    """Lower is closer. Size dominates; beds, baths, age, distance and the
    favored subdivision refine."""
    score = abs(math.log(h["sqft"] / subject["sqft"])) * 3
    if subject.get("beds") and h.get("beds"):
        score += abs(h["beds"] - subject["beds"]) * 0.25
    if subject.get("baths") and h.get("baths") is not None:
        score += abs(h["baths"] - subject["baths"]) * 0.15
    if subject.get("year_built") and h.get("year_built"):
        score += min(abs(h["year_built"] - subject["year_built"]), 40) / 40 * 0.5
    score += h.get("dist", 0) / radius * 0.5
    if favor and subdiv_matches(h.get("subdiv"), [favor]):
        score -= SUBDIV_BONUS
    return score


def closest_comps(sold, subject, radius, favor="", exclude=()):
    """Similar-sized sold homes ranked by similarity; empty without the subject's sqft."""
    if not (subject and subject.get("sqft")):
        return []
    lo, hi = (subject["sqft"] * f for f in COMP_SIZE_RANGE)
    usable = [h for h in sold if h.get("ppsf") and h.get("price")
              and h.get("sqft") and lo <= h["sqft"] <= hi
              and not subdiv_matches(h.get("subdiv"), exclude)]
    return sorted(usable, key=lambda h: similarity(h, subject, radius, favor))


def time_adjusted(h, today, rate):
    """Sale price grown at `rate` %/yr (simple) from the sale date to today."""
    if not (rate and today and h.get("sold_date")):
        return h["price"]
    days = max((today - date.fromisoformat(h["sold_date"])).days, 0)
    return h["price"] * (1 + rate / 100 * days / 365)


def comps_value(comps, subject, today=None, rate=0.0):
    """Median of the comps' prices, each grown `rate` %/yr since its sale and
    adjusted by SIZE_ADJ per sqft of size difference (bigger houses sell for
    less per sqft, so scaling by $/sf would undervalue a smaller home), plus
    BED_ADJ per extra bedroom.
    Returns (value, implied $/sf, comps used, bedroom adjustment)."""
    top = comps[:COMP_COUNT]
    if not top:
        return None
    base = statistics.median(time_adjusted(h, today, rate) + (subject["sqft"] - h["sqft"]) * SIZE_ADJ
                             for h in top)
    comp_beds = [h["beds"] for h in top if h.get("beds")]
    bed_adj = 0
    if subject.get("beds") and comp_beds:
        bed_adj = round((subject["beds"] - statistics.median(comp_beds)) * BED_ADJ)
    return round(base + bed_adj, -3), round(base / subject["sqft"]), len(top), bed_adj


def sale_history(h, state):
    """(list price, days to sell) for a sold home tracked while it was listed."""
    rec = (state.get("listings") or {}).get(h["id"]) or {}
    lp, first = rec.get("price"), rec.get("first_seen")
    if not (lp and h["price"] and first and h.get("sold_date")):
        return None
    ratio = h["price"] / lp
    if not 0.5 <= ratio <= 1.5:  # relisted years later, or a bad record
        return None
    return lp, (date.fromisoformat(h["sold_date"]) - date.fromisoformat(first)).days


def market_line(active, pending):
    bits = []
    if pending:
        bits.append(f"{len(pending)}/{len(active) + len(pending)} under contract")
    for name, homes in (("pending", pending), ("active", active)):
        prices = [h["price"] for h in homes if h["price"]]
        if prices:
            bits.append(f"{name} median {fmt_short(int(statistics.median(prices)))}")
    doms = [h["dom"] for h in active if h["dom"] is not None]
    if doms:
        bits.append(f"{int(statistics.median(doms))}d on market")
    return " · ".join(bits) or None


def sold_pace_line(sold, state):
    known = [(h["price"] / lp, days) for h in sold if (x := sale_history(h, state))
             for lp, days in [x]]
    if not known:
        return None
    days = [d for _, d in known if d >= 0]
    s = f"{statistics.median(r for r, _ in known) * 100:.0f}% of list"
    if days:
        s += f" · ~{int(statistics.median(days))}d to sell"
    return s + f" ({len(known)}/{len(sold)} tracked)"


# ─── Cache with stale fallback ───────────────────────────────────────────

def _cache_path(name):
    return os.path.join(CACHE_DIR, name)


def read_json(name, default=None):
    try:
        with open(_cache_path(name)) as f:
            return json.load(f)
    except Exception:
        return default


def write_json(name, obj):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = _cache_path(name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, _cache_path(name))


def cached(name, ttl, fetcher):
    """Return (obj, info). Fresh cache wins; on fetch failure fall back to the
    last good copy with info['stale']=True. Failures are never cached.

    The file holds {"fetched": ts, "data": obj}. Freshness uses the file mtime
    (which --force zeroes); the stored timestamp is what the menu displays."""
    path = _cache_path(name)
    entry = read_json(name)
    if not (isinstance(entry, dict) and "data" in entry):
        entry = None
    old = entry["data"] if entry else None
    fetched = entry.get("fetched") if entry else None
    if entry and time.time() - os.path.getmtime(path) < ttl:
        return old, {"stale": False, "fetched": fetched, "error": None}
    try:
        obj = fetcher()
    except Exception as e:  # noqa: BLE001 — any failure degrades to cache
        msg = str(e) or e.__class__.__name__
        return old, {"stale": True, "fetched": fetched, "error": msg}
    now = time.time()
    write_json(name, {"fetched": now, "data": obj})
    return obj, {"stale": False, "fetched": now, "error": None}


def force_stale():
    """Age the fetch caches so the next run refetches but keeps fallbacks."""
    if not os.path.isdir(CACHE_DIR):
        return
    for name in os.listdir(CACHE_DIR):
        if name.endswith(".json") and name not in (STATE_FILE, LOCATION_FILE, SUBDIV_FILE):
            os.utime(_cache_path(name), (0, 0))


# ─── Fetchers ────────────────────────────────────────────────────────────

ALL_TYPES = sorted(PROPERTY_TYPES.values())


def gis_params(poly, kind, sold_days=90, types=ALL_TYPES):
    p = {"al": 1, "mpt": 99, "num_homes": 350, "ord": "redfin-recommended-asc",
         "page_number": 1, "start": 0, "poly": poly, "uipt": ",".join(map(str, types)), "v": 8}
    if kind == "active":
        p["status"] = 9
    elif kind == "pending":
        p["status"] = 130
    elif kind == "sold":
        p.update(status=9, sold_within_days=sold_days)
    else:
        raise ValueError(kind)
    return p


def fetch_gis(poly, kind, sold_days=90, types=ALL_TYPES):
    return parse_gis(http_get(GIS_URL, gis_params(poly, kind, sold_days, types)))


def geocode(cfg):
    q = f"{cfg['address']}, {cfg['city']}, {cfg['state']} {cfg['zip']}"
    return parse_census(http_get(CENSUS_URL, {"address": q, "benchmark": "Public_AR_Current",
                                              "format": "json"}))


def find_home(lat, lon, address):
    """Off-market homes only appear in long sold-history searches, so search a
    tiny box around the house over 100 years of sales and match the address."""
    homes = fetch_gis(bbox_poly(lat, lon, 0.15), "sold", sold_days=36500)
    return match_home(homes, address)


def fetch_home(url, year_built):
    pid = property_id(url)
    if not pid:
        raise FetchError("no property id in redfin_url")
    return {**parse_avm(http_get(AVM_URL, {"propertyId": pid, "accessLevel": 1})),
            "year_built": year_built}


def resolve_location(cfg):
    """Return (lat, lon, redfin_url, year_built, note). Geocoding and the home
    lookup (URL + year built, which the AVM lacks) are cached in location.json,
    keyed by the address."""
    key = f"{cfg['address']}, {cfg['city']}, {cfg['state']} {cfg['zip']}".lower()
    loc = read_json(LOCATION_FILE, {}) or {}
    if loc.get("key") != key:
        loc = {"key": key}

    lat, lon = cfg["lat"], cfg["lon"]
    if lat is None or lon is None:
        if loc.get("lat") is None:
            loc["lat"], loc["lon"] = geocode(cfg)  # fatal if it fails: no search center
            write_json(LOCATION_FILE, loc)
        lat, lon = loc["lat"], loc["lon"]

    note = None
    url = cfg["redfin_url"] or loc.get("redfin_url")
    if ((not url or "year_built" not in loc)
            and time.time() - loc.get("url_tried", 0) > URL_RETRY_SEC):
        loc["url_tried"] = time.time()
        try:
            hit = find_home(lat, lon, cfg["address"])
            if hit:
                loc["redfin_url"] = hit["url"]
                url = url or hit["url"]
            loc["year_built"] = hit.get("year_built") if hit else None
        except FetchError as e:
            if not url:
                note = f"Home lookup failed: {e}"
        write_json(LOCATION_FILE, loc)
    if not url and not note:
        note = 'Home not found on Redfin. Set "redfin_url" in config.'
    return lat, lon, url, loc.get("year_built"), note


def parse_parcel_subdiv(body):
    feats = json.loads(body).get("features") or []
    return ((feats[0].get("attributes") or {}).get("SUBDIV") or "").strip() if feats else ""


def fetch_parcel_subdiv(lat, lon):
    """County subdivision of the parcel under a point ("" when none)."""
    return parse_parcel_subdiv(http_get(PARCEL_URL, {
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "outFields": "SUBDIV",
        "returnGeometry": "false", "f": "json"}))


def fill_subdivisions(homes, city):
    """Fill missing or city-only MLS subdivisions from the county parcel layer.
    Lookups are cached by property id; failures are retried next run."""
    known = read_json(SUBDIV_FILE, {}) or {}
    todo = [h for h in homes if (h.get("subdiv") or "").lower() in ("", city.lower())
            and h.get("lat") is not None]
    missing = {h["id"]: h for h in todo if h["id"] not in known}
    if missing:
        def look(h):
            try:
                return h["id"], fetch_parcel_subdiv(h["lat"], h["lon"])
            except Exception:  # noqa: BLE001 — unknown subdivision is harmless
                return h["id"], None
        with ThreadPoolExecutor(max_workers=6) as pool:
            for pid, sub in pool.map(look, missing.values()):
                if sub is not None:
                    known[pid] = sub
        write_json(SUBDIV_FILE, known)
    for h in todo:
        if known.get(h["id"]):
            h["subdiv"] = known[h["id"]]


def gather(cfg, lat, lon, url, year_built=None):
    poly = bbox_poly(lat, lon, cfg["radius"])
    types = cfg["property_types"]
    jobs = {
        "active": ("active.json", LIST_TTL, lambda: fetch_gis(poly, "active", types=types)),
        "pending": ("pending.json", LIST_TTL, lambda: fetch_gis(poly, "pending", types=types)),
        "sold": ("sold.json", LIST_TTL,
                 lambda: fetch_gis(poly, "sold", cfg["sold_days"], types)),
    }
    if cfg["comp_days"] > cfg["sold_days"]:
        jobs["comps"] = ("comps.json", LIST_TTL,
                         lambda: fetch_gis(poly, "sold", cfg["comp_days"], types))
    if url:
        jobs["home"] = ("home.json", HOME_TTL, lambda: fetch_home(url, year_built))
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {k: pool.submit(cached, *v) for k, v in jobs.items()}
        results = {k: f.result() for k, f in futures.items()}
    if cfg["subdivision"] or cfg["exclude_subdivisions"]:
        pool_homes = (results.get("comps") or results["sold"])[0] or []
        near_ids = {h["id"] for h in within_radius(pool_homes, lat, lon, cfg["radius"])}
        fill_subdivisions([h for h in pool_homes if h["id"] in near_ids], cfg["city"])  # in place
    return results


# ─── Formatting ──────────────────────────────────────────────────────────

def fmt_price(v):
    if v is None:
        return "—"
    return f"${v / 1_000_000:.2f}M" if v >= 1_000_000 else f"${v:,}"


def fmt_short(v):
    if v is None:
        return "—"
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if abs(v) >= 1000:
        return f"${round(v / 1000):,}K"
    return f"${v:,}"


def fmt_bb_col(beds, baths):
    """Compact table cell: 5/2.5 (the column header says BD/BA)."""
    if not beds and baths is None:  # 0 beds = land / commercial
        return "—"
    return f"{f'{beds:g}' if beds else '—'}/{f'{baths:g}' if baths is not None else '—'}"


def fmt_bb(beds, baths):
    parts = []
    if beds:  # 0 beds = land / commercial
        parts.append(f"{beds:g}bd")
    if baths is not None:
        parts.append(f"{baths:g}ba")
    return "/".join(parts) or "—"


def fmt_num(v, prefix=""):
    return f"{prefix}{v:,}" if v is not None else "—"


def fmt_age(ts):
    return datetime.fromtimestamp(ts).strftime("%m/%d/%y %-I:%M %p") if ts else "never"


def fmt_date(iso):
    """Every date in the menu reads mm/dd/yy."""
    return date.fromisoformat(iso).strftime("%m/%d/%y") if iso else "—"


def short_address(addr):
    """Drop the street type for display: "2832 Fairway Dr" -> "2832 Fairway"."""
    words = addr.split()
    if len(words) > 2 and words[-1].lower().rstrip(".") in STREET_SUFFIXES:
        words = words[:-1]
    return " ".join(words)


def clean(s):
    """Keep SwiftBar's '|' parameter separator and quotes out of text."""
    return str(s).replace("|", "/").replace('"', "'")


SUBDIV_DROP = re.compile(r"\b(the|add|addition|sub|subdivision|plat|final)\b", re.I)
SUBDIV_ABBR = [(re.compile(r"\bestates?\b", re.I), "Est"),
               (re.compile(r"\b(village|vlg)\b", re.I), "Vlg")]


def short_subdiv(name):
    """Compact MLS / county subdivision name for display: "The Orchards 8th Add"
    -> "Orchards 8th", "ESTATES AT PLUM HILL PLAT 2" -> "Est/Plum Hill 2"."""
    if not name:
        return "—"
    s = name
    if s.isupper():
        s = " ".join(w[:1] + w[1:].lower() for w in s.split())
    s = re.sub(r"(?i)estatesplat", "Estates Plat", s).replace("-", " ")
    s = re.sub(r"\s+at\s+(the\s+)?", "/", s, flags=re.I)
    s = SUBDIV_DROP.sub("", s)
    for pat, rep in SUBDIV_ABBR:
        s = pat.sub(rep, s)
    s = re.sub(r"\b0+(\d)", r"\1", s)  # Ph 01 -> Ph 1
    s = re.sub(r"\s*/\s*", "/", s)
    return " ".join(s.split()) or name


def clamp(s, width):
    return s if len(s) <= width else s[:width - 1] + "…"


def note_for(h):
    if h.get("change"):
        arrow = "▼" if h["change"] < 0 else "▲"
        return f"{arrow}{fmt_short(abs(h['change']))}"
    if h.get("is_new"):
        return "NEW"
    if h.get("status") == "Coming Soon":
        return "SOON"
    return ""


def tooltip_for(h, kind):
    bits = [h["address"], f"{h['dist']:.1f} mi away"]
    if h.get("year_built"):
        bits.append(f"built {h['year_built']}")
    if h.get("hoa"):
        bits.append(f"HOA ${h['hoa']}/mo")
    if kind == "active":
        if h.get("listed_on"):
            bits.append(f"first seen {fmt_date(h['listed_on'])}")
        if h.get("change"):
            bits.append(f"price {'cut' if h['change'] < 0 else 'raised'} {fmt_short(abs(h['change']))} "
                        f"on {fmt_date(h['changed_on'])}")
        if h.get("orig_price"):
            bits.append(f"originally {fmt_price(h['orig_price'])}")
    if kind == "sold":
        if h.get("close"):
            bits.append("★ one of the closest comps to your home")
        if h.get("list_price"):
            bits.append(f"listed at {fmt_price(h['list_price'])} "
                        f"({h['price'] / h['list_price'] * 100:.0f}% of list)")
        if h.get("days_to_sell") is not None:
            bits.append(f"{h['days_to_sell']}d to sell")
    return clean(" · ".join(bits))


def stats_line(homes):
    prices = [h["price"] for h in homes if h["price"]]
    ppsf = [h["ppsf"] for h in homes if h["ppsf"]]
    if not prices:
        return None
    s = (f"Median {fmt_short(int(statistics.median(prices)))} · "
         f"{fmt_short(min(prices))}–{fmt_short(max(prices))}")
    if ppsf:
        s += f" · ${int(statistics.median(ppsf))}/sf"
    return s


def listing_row(h, last_col, kind, last_w=6):
    cols = f"{clamp(short_address(h['address']), ADDR_W):<{ADDR_W}} "
    if kind != "sold":
        cols += f"{'PEND' if kind == 'pending' else '':<4} "
    cols += (f"{fmt_short(h['price']):>7} {fmt_bb_col(h['beds'], h['baths']):>7} "
             f"{fmt_num(h['sqft']):>6} {fmt_num(h['ppsf'], '$'):>5} {last_col:>{last_w}}")
    params = [TABLE, f'tooltip="{tooltip_for(h, kind)}"']
    if h.get("url"):
        params.append(f"href={h['url']}")
    if kind == "pending":
        params.append(ACCENT_TEAL)
    if kind == "active":
        note = note_for(h)
        cols += f"  {note}" if note else ""
        if h.get("change") and h["change"] < 0:
            params.append(ACCENT_ORANGE)
        elif h.get("is_new"):
            params.append(ACCENT_GREEN)
    if kind == "sold" and h.get("close"):
        cols += "  ★"
    return f"{ROW_INDENT}{clean(cols)} | {' '.join(params)}"


def table_title(label, last_col, status, href, last_w=6):
    """Section title that doubles as the column header: the label fills the
    address (and status) columns so PRICE … line up with the rows below."""
    width = len(ROW_INDENT) + ADDR_W + 1 + (5 if status else 0)
    cols = (f"{clamp(label, width - 1):<{width}}{'PRICE':>7} {'BD/BA':>7} {'SQFT':>6} "
            f"{'$/SF':>5} {last_col:>{last_w}}")
    return f"{clean(cols)} | {TABLE_BOLD} {WHITE} href={href} tooltip=\"View all on Redfin\""


def stale_line(info):
    if not info or not info.get("error"):
        return None
    if info.get("fetched"):
        return (f"     ⚠ {clean(info['error'])} · showing data from {fmt_age(info['fetched'])} "
                f"| {MONO_F} {ACCENT_ORANGE} size=10")
    return f"     ⚠ Couldn't load: {clean(info['error'])} | {MONO_F} {ACCENT_RED} size=10"


# ─── Rendering ───────────────────────────────────────────────────────────

def render_setup(missing):
    print("⌂ Setup | font=SF Pro Display size=14")
    print("---")
    print(f"Missing config: {', '.join(missing)} | {MONO} {ACCENT_RED}")
    print(f"Create {CONFIG_PATH} | {MONO_F} {LABEL_COLOR} size=11")
    example = {"address": "123 Main St", "city": "Springfield", "state": "IL",
               "zip": "62704", "radius": 1.5, "sold_days": 90}
    for line in json.dumps(example, indent=2).splitlines():
        print(f"  {line} | {MONO_F} {DIMMED} size=11")
    print("Optional: lat, lon, redfin_url (auto-detected when absent) "
          f"| {MONO_F} {DIMMED} size=11")
    print("Or set REDFIN_* env vars in SwiftBar settings | "
          f"{MONO_F} {DIMMED} size=11")
    print(f"Refresh | refresh=true {MONO_F} size=11")


def render_fatal(msg):
    print("⌂ ⚠ | font=SF Pro Display size=14")
    print("---")
    print(f"{clean(msg)} | {MONO} {ACCENT_RED}")
    print(f"Refresh | refresh=true {MONO_F} size=11")


def render(cfg, lat, lon, url, url_note, results, today):
    radius = cfg["radius"]
    active_raw, active_info = results["active"]
    pending_raw, pending_info = results["pending"]
    sold_raw, sold_info = results["sold"]
    home, home_info = results.get("home", (None, None))

    types = set(cfg["property_types"])

    def nearby(homes):  # caches written before the type filter may hold other types
        return [h for h in within_radius(homes or [], lat, lon, radius)
                if h.get("ptype") is None or h["ptype"] in types]

    active = nearby(active_raw)
    pending = nearby(pending_raw)
    cutoff = (today - timedelta(days=cfg["sold_days"])).isoformat()
    sold = [h for h in nearby(sold_raw) if h["sold_date"] and h["sold_date"] >= cutoff]
    comp_days = cfg.get("comp_days", cfg["sold_days"])
    comp_raw = (results.get("comps") or (None,))[0]
    comp_cutoff = (today - timedelta(days=max(comp_days, cfg["sold_days"]))).isoformat()
    comp_pool = [h for h in nearby(comp_raw if comp_raw is not None else sold_raw)
                 if h["sold_date"] and h["sold_date"] >= comp_cutoff]

    state = read_json(STATE_FILE, {}) or {}
    if active_raw is not None or pending_raw is not None:
        update_state(state, active + pending, today)
    if home and not (home_info or {}).get("error"):
        record_estimate(state, home.get("estimate"), today)
    write_json(STATE_FILE, state)
    for h in active:
        annotate(h, state, today)
    for h in sold:
        hist = sale_history(h, state)
        h["list_price"], h["days_to_sell"] = hist if hist else (None, None)

    # New listings first, then price cuts, so neither falls past MAX_ROWS.
    active.sort(key=lambda h: (not h["is_new"], (h.get("change") or 0) >= 0, -(h["price"] or 0)))
    pending.sort(key=lambda h: -(h["price"] or 0))
    sold.sort(key=lambda h: h["sold_date"], reverse=True)
    # Redfin's record can lag the house (e.g. a basement bedroom); the comps use
    # the configured beds / sqft, the Redfin Estimate stays Redfin's.
    subject = home
    if home:
        overrides = {k: cfg[k] for k in ("beds", "sqft") if cfg.get(k)}
        subject = {**home, **overrides}
    comps = closest_comps(comp_pool, subject, radius, cfg.get("subdivision", ""),
                          cfg.get("exclude_subdivisions", ()))
    rank = {h["id"]: i for i, h in enumerate(comps[:COMP_COUNT])}
    starred = sorted((h for h in sold if h["id"] in rank), key=lambda h: rank[h["id"]])[:STAR_COUNT]
    for h in sold:
        h["close"] = h in starred
    # Closest comps first (in similarity order), then the rest newest first.
    sold = starred + [h for h in sold if not h["close"]]

    # ── Menu bar title ──
    estimate = (home or {}).get("estimate")
    cv = comps_value(comps, subject, today, cfg.get("appreciation", 0.0)) if home else None
    use_comps = bool(cv) and cfg.get("title", "comps") == "comps"
    headline = cv[0] if use_comps else estimate
    title = f"⌂ {fmt_short(headline)}" if headline else f"⌂ {len(active)} for sale"
    if any(r[0] is None for r in (results["active"], results["sold"])):
        title += " ⚠"
    print(f"{title} | {TITLE_FONT}")
    print("---")

    map_link = None
    if cfg.get("map") and os.path.isfile(cfg["map"]):
        map_uri = "file://" + urllib.parse.quote(os.path.abspath(cfg["map"]))
        map_link = f"Neighborhood map ↗ | href={map_uri} {MONO_F} {ACCENT_BLUE} size=11"

    def print_comps(primary):
        value, ppsf, n, bed_adj = cv
        names = ", ".join(h["address"] for h in comps[:n])
        tip = f"From {names}"
        if cfg.get("appreciation"):
            tip += f" · +{cfg['appreciation']:g}%/yr since each sale"
        if bed_adj:
            tip += f" · {'+' if bed_adj > 0 else '−'}{fmt_short(abs(bed_adj))} bedroom adjustment"
        style = f"{ACCENT_GREEN} size=13" if primary else f"{LABEL_COLOR} size=12"
        print(f"     Comps value  ≈ {fmt_short(value)}  (${ppsf}/sf · {n} closest {'sale' if n == 1 else 'sales'}) "
              f"| {MONO_F} {style} tooltip=\"{clean(tip)}\"")
        # Menlo keeps the columns lined up; the first row is their header.
        print(f"--{'ADDRESS':<{ADDR_W}} {'SUBDIVISION':<{SUBDIV_W}} {'SOLD':<8} {'PRICE':>7} {'SQFT':>6} "
              f"{'$/SF':>5} | {TABLE_BOLD} {WHITE}")
        for h in comps[:n]:
            subdiv = clamp(short_subdiv(h.get("subdiv")), SUBDIV_W)
            tip = f' tooltip="{clean(h["subdiv"])}"' if h.get("subdiv") else ""
            print(f"--{clamp(short_address(h['address']), ADDR_W):<{ADDR_W}} {subdiv:<{SUBDIV_W}} "
                  f"{fmt_date(h['sold_date'])} {fmt_short(h['price']):>7} {fmt_num(h['sqft']):>6} "
                  f"{fmt_num(h['ppsf'], '$'):>5} | href={h['url']} {TABLE}{tip}")
        if map_link:
            print("-----")
            print(f"--{map_link}")

    def print_redfin(primary):
        if not estimate:
            print(f"     Redfin isn't showing an estimate for this home | {MONO} {DIMMED}")
            return
        line = f"     Redfin Estimate  {fmt_short(estimate)}"
        if home.get("sqft"):
            line += f"  (${round(estimate / home['sqft'])}/sf)"
        trend = estimate_trend(state.get("estimates"), today)
        if trend:
            change, pct, since = trend
            arrow = "▲" if change >= 0 else "▼"
            line += f"  ·  {arrow}{fmt_short(abs(change))} ({pct:+.1f}%) since {fmt_date(since)}"
        style = f"{ACCENT_GREEN} size=13" if primary else f"{LABEL_COLOR} size=12"
        print(f"{line} | {MONO_F} {style}")

    # ── Your home ──
    # One line for the house itself; the full address and any disagreement with
    # Redfin's record live in the tooltip, and clicking opens the Redfin page.
    facts = [clean(short_address(cfg["address"]))]
    tip = [f"{clean(cfg['address'])}, {clean(cfg['city'])}, {cfg['state']} {cfg['zip']}"]
    if home:
        if subject.get("beds") is not None or home.get("baths") is not None:
            facts.append(fmt_bb(subject.get("beds"), home.get("baths")))
        if subject.get("sqft"):
            facts.append(f"{subject['sqft']:,}sf")
        if home.get("year_built"):
            facts.append(str(home["year_built"]))
        differs = []
        if home.get("beds") is not None and subject.get("beds") != home["beds"]:
            differs.append(f"{home['beds']:g}bd")
        if home.get("sqft") and subject.get("sqft") != home["sqft"]:
            differs.append(f"{home['sqft']:,}sf")
        if differs:
            tip.append(f"Redfin record: {' · '.join(differs)}")
    if url:
        tip.append("Click to open on Redfin")
    link = f" href={url}" if url else ""
    print(f"🏠  {'  ·  '.join(facts)} | {HEADER_FONT} {WHITE}{link} "
          f"tooltip=\"{clean(' · '.join(tip))}\"")
    if home:
        if use_comps:
            print_comps(primary=True)
            print_redfin(primary=False)
        else:
            print_redfin(primary=True)
            if cv:
                print_comps(primary=False)
    if stale_line(home_info):
        print(stale_line(home_info))
    if url_note and not home:
        print(f"     {clean(url_note)} | {MONO_F} {DIMMED} size=11")
    if map_link and not (home and cv):
        print(f"     {map_link}")
    print("---")

    # ── On market: pending first (what buyers took), then active (asking) ──
    label = f"{len(pending)} PENDING · {len(active)} ACTIVE"
    print(table_title(label, "DOM", status=True,
                      href=f"https://www.redfin.com/zipcode/{cfg['zip']}"))
    if stale_line(active_info):
        print(stale_line(active_info))
    elif stale_line(pending_info):
        print(stale_line(pending_info).replace("⚠", "⚠ Pending:", 1))
    market = [(h, "pending") for h in pending] + [(h, "active") for h in active]
    if market:
        if market_line(active, pending):
            print(f"     {market_line(active, pending)} | {MONO_F} {DIMMED} size=11")
        for h, kind in market[:MAX_ROWS]:
            dom = f"{h['dom']}d" if h["dom"] is not None else "—"
            print(listing_row(h, dom, kind))
        if len(market) > MAX_ROWS:
            print(f"     … and {len(market) - MAX_ROWS} more | {MONO_F} {DIMMED} size=10")
    elif active_raw is not None:
        print(f"     No listings within {radius:g} mi | {MONO} {DIMMED}")
    print("---")

    # ── Sold ──
    sold_link = (f"https://www.redfin.com/zipcode/{cfg['zip']}/filter/"
                 f"include={sold_filter_token(cfg['sold_days'])}")
    print(table_title(f"{len(sold)} SOLD · {cfg['sold_days']} DAYS", "SOLD",
                      status=False, href=sold_link, last_w=8))
    if stale_line(sold_info):
        print(stale_line(sold_info))
    if sold:
        summary = " · ".join(filter(None, (stats_line(sold), sold_pace_line(sold, state))))
        if summary:
            print(f"     {summary} | {MONO_F} {DIMMED} size=11")
        for h in sold[:MAX_ROWS]:
            print(listing_row(h, fmt_date(h["sold_date"]), "sold", last_w=8))
        if len(sold) > MAX_ROWS:
            print(f"     … and {len(sold) - MAX_ROWS} more | {MONO_F} {DIMMED} size=10")
    elif sold_raw is not None:
        print(f"     No sales within {radius:g} mi | {MONO} {DIMMED}")
    print("---")

    # ── Footer ──
    fetched = [r[1].get("fetched") for r in results.values() if r[1] and r[1].get("fetched")]
    updated = fmt_age(min(fetched)) if fetched else "never"
    print(f"Data from {updated}  ·  {radius:g} mi around home | {MONO_F} {DIMMED} size=10")
    print(f"Refresh | refresh=true {MONO_F} size=11")
    print(f"Force Refresh (ignore cache) | bash={SCRIPT_PATH} param1=--force terminal=false "
          f"refresh=true {MONO_F} size=11")


# ─── Entry points ────────────────────────────────────────────────────────

def run_check(cfg):
    force_stale()
    ok = True
    try:
        lat, lon, url, year_built, note = resolve_location(cfg)
    except Exception as e:  # noqa: BLE001
        print(f"location   FAIL  {e}")
        return 1
    print(f"location   ok    {lat:.5f}, {lon:.5f}")
    print(f"home url   {'ok   ' if url else 'MISS '} {url or note}")
    results = gather(cfg, lat, lon, url, year_built)
    for kind, (obj, info) in results.items():
        if info["error"]:
            ok = False
            print(f"{kind:<10} FAIL  {info['error']}")
        elif kind == "home":
            print(f"{kind:<10} ok    estimate {fmt_price(obj.get('estimate'))}")
        else:
            near = within_radius(obj, lat, lon, cfg["radius"])
            print(f"{kind:<10} ok    {len(obj)} in box, {len(near)} within {cfg['radius']:g} mi")
    return 0 if ok else 1


def main(argv):
    if "--force" in argv:
        force_stale()
        return 0
    try:
        cfg = load_config()
    except ConfigError as e:
        render_setup(e.missing)
        return 0
    if "--check" in argv:
        return run_check(cfg)
    try:
        lat, lon, url, year_built, note = resolve_location(cfg)
    except Exception as e:  # noqa: BLE001
        render_fatal(f"Couldn't locate home: {e}")
        return 0
    today = date.fromisoformat(os.environ["HOME_COMPS_FAKE_TODAY"]) \
        if os.environ.get("HOME_COMPS_FAKE_TODAY") else date.today()
    render(cfg, lat, lon, url, note, gather(cfg, lat, lon, url, year_built), today)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
