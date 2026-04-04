#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

# <swiftbar.title>Neighborhood Real Estate Monitor</swiftbar.title>
# <swiftbar.version>v1.0</swiftbar.version>
# <swiftbar.author>Derrick</swiftbar.author>
# <swiftbar.author.github>hdgs</swiftbar.author.github>
# <swiftbar.desc>Monitors neighborhood real estate activity via Redfin. Configure via ~/.config/swiftbar-plugins/home_comps_uv.json</swiftbar.desc>
# <swiftbar.dependencies>uv</swiftbar.dependencies>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>false</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>
# <swiftbar.environment>[REDFIN_ADDRESS:, REDFIN_CITY:, REDFIN_STATE:, REDFIN_ZIP:, REDFIN_LAT:, REDFIN_LON:, REDFIN_RADIUS:3, REDFIN_SOLD_DAYS:90]</swiftbar.environment>

"""
Neighborhood Real Estate Monitor — SwiftBar Plugin
===================================================
Pulls active (for sale) and recently sold listings from Redfin's
Stingray CSV endpoint, plus an AVM estimate for your home address.

Refresh: every 8 hours (filename convention: home_comps_uv.8hr.py)

SETUP — pick ONE method:

  Method 1: Config file (recommended, keeps secrets out of the repo)
  ──────────────────────────────────────────────────────────────────
  All SwiftBar plugins in this repo read from:
    ~/.config/swiftbar-plugins/<plugin-name>.json

  For this plugin, create:
    ~/.config/swiftbar-plugins/home_comps_uv.json

    {
      "address":  "123 Main St",
      "city":     "Springfield",
      "state":    "IL",
      "zip":      "62704",
      "lat":      39.7817,
      "lon":      -89.6501,
      "radius":   3,
      "sold_days": 90
    }

  Method 2: Environment variables (SwiftBar settings UI)
  ──────────────────────────────────────────────────────
  Set these in SwiftBar's plugin environment panel:
    REDFIN_ADDRESS   = 123 Main St
    REDFIN_CITY      = Springfield
    REDFIN_STATE     = IL
    REDFIN_ZIP       = 62704
    REDFIN_LAT       = 39.7817
    REDFIN_LON       = -89.6501
    REDFIN_RADIUS    = 3        (optional, default 3)
    REDFIN_SOLD_DAYS = 90       (optional, default 90)
"""

import csv
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime

# ─── Configuration (loaded from config file or env vars) ─────────────────

_PLUGIN_NAME = os.path.splitext(os.path.splitext(os.path.basename(__file__))[0])[0]
CONFIG_DIR = os.path.expanduser("~/.config/swiftbar-plugins")
CONFIG_PATH = os.path.join(CONFIG_DIR, f"{_PLUGIN_NAME}.json")


def load_config():
    """Load config from JSON file, falling back to environment variables."""
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
        except Exception:
            pass

    address = cfg.get("address", os.environ.get("REDFIN_ADDRESS", ""))
    city = cfg.get("city", os.environ.get("REDFIN_CITY", ""))
    state = cfg.get("state", os.environ.get("REDFIN_STATE", ""))
    zipcode = cfg.get("zip", os.environ.get("REDFIN_ZIP", ""))
    lat = cfg.get("lat", os.environ.get("REDFIN_LAT", ""))
    lon = cfg.get("lon", os.environ.get("REDFIN_LON", ""))
    radius = cfg.get("radius", os.environ.get("REDFIN_RADIUS", "3"))
    sold_days = cfg.get("sold_days", os.environ.get("REDFIN_SOLD_DAYS", "90"))

    missing = []
    if not address:
        missing.append("address")
    if not city:
        missing.append("city")
    if not state:
        missing.append("state")
    if not zipcode:
        missing.append("zip")
    if not lat:
        missing.append("lat")
    if not lon:
        missing.append("lon")

    if missing:
        print("⌂ Setup | font=SF Pro Display size=14")
        print("---")
        print(
            f"Missing config: {', '.join(missing)} | font=SFMono-Regular size=12 color=#FF3B30"
        )
        print("---")
        print("Create config file: | font=SFMono-Regular size=12 color=#AEAEB2")
        print(f"  {CONFIG_PATH} | font=SFMono-Regular size=11 color=#8E8E93")
        print("---")
        print("  { | font=SFMono-Regular size=11 color=#8E8E93")
        print(
            '    "address": "123 Main St", | font=SFMono-Regular size=11 color=#8E8E93'
        )
        print(
            '    "city":    "Springfield", | font=SFMono-Regular size=11 color=#8E8E93'
        )
        print('    "state":   "IL", | font=SFMono-Regular size=11 color=#8E8E93')
        print('    "zip":     "62704", | font=SFMono-Regular size=11 color=#8E8E93')
        print('    "lat":     39.7817, | font=SFMono-Regular size=11 color=#8E8E93')
        print('    "lon":     -89.6501, | font=SFMono-Regular size=11 color=#8E8E93')
        print('    "radius":  3, | font=SFMono-Regular size=11 color=#8E8E93')
        print('    "sold_days": 90 | font=SFMono-Regular size=11 color=#8E8E93')
        print("  } | font=SFMono-Regular size=11 color=#8E8E93")
        print("---")
        print(
            "Or set REDFIN_* env vars in SwiftBar settings | font=SFMono-Regular size=11 color=#8E8E93"
        )
        print("Refresh | refresh=true font=SFMono-Regular size=11")
        sys.exit(0)

    return {
        "address": str(address),
        "city": str(city),
        "state": str(state),
        "zip": str(zipcode),
        "lat": float(lat),
        "lon": float(lon),
        "radius": float(radius),
        "sold_days": int(sold_days),
    }


_cfg = load_config()
HOME_ADDRESS = _cfg["address"]
HOME_CITY = _cfg["city"]
HOME_STATE = _cfg["state"]
HOME_ZIP = _cfg["zip"]
SEARCH_ZIP = _cfg["zip"]
RADIUS_MILES = _cfg["radius"]
HOME_LAT = _cfg["lat"]
HOME_LON = _cfg["lon"]
SOLD_DAYS = _cfg["sold_days"]

CACHE_DIR = os.path.expanduser(f"~/.cache/swiftbar-plugins/{_PLUGIN_NAME}")
CACHE_TTL_SEC = 3500  # slightly under 1h to stay fresh

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)

# ─── Formatting helpers ─────────────────────────────────────────────────

ANSI_MONOSPACE = "font=SFMono-Regular size=12"
SECTION_FONT = "font=SFMono-Bold size=12"
HEADER_FONT = "font=SF Pro Display size=13"
TITLE_FONT = "font=SF Pro Display size=14"
DIMMED = "color=#8E8E93"
ACCENT_GREEN = "color=#34C759"
ACCENT_RED = "color=#FF3B30"
ACCENT_BLUE = "color=#007AFF"
ACCENT_ORANGE = "color=#FF9500"
LABEL_COLOR = "color=#AEAEB2"
WHITE = "color=#FFFFFF"


def fmt_price(val):
    """Format a price integer into $XXX,XXX or $X.XXM."""
    try:
        p = int(float(val))
        if p >= 1_000_000:
            return f"${p / 1_000_000:.2f}M"
        return f"${p:,}"
    except (ValueError, TypeError):
        return "N/A"


def fmt_sqft(val):
    try:
        return f"{int(float(val)):,}"
    except (ValueError, TypeError):
        return "—"


def fmt_ppsf(val):
    try:
        return f"${int(float(val)):,}"
    except (ValueError, TypeError):
        return "—"


def fmt_dom(val):
    try:
        d = int(float(val))
        return f"{d}d"
    except (ValueError, TypeError):
        return "—"


def fmt_beds_baths(beds, baths):
    parts = []
    try:
        parts.append(f"{int(float(beds))}bd")
    except (ValueError, TypeError):
        pass
    try:
        b = float(baths)
        parts.append(f"{b:g}ba")
    except (ValueError, TypeError):
        pass
    return "/".join(parts) if parts else "—"


def short_addr(full_addr):
    """Trim address to street number + name only."""
    return full_addr.strip()[:28] if full_addr else "—"


def haversine_miles(lat1, lon1, lat2, lon2):
    """Quick haversine for filtering by radius."""
    import math

    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(
        math.radians(lat2)
    ) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# ─── Redfin HTTP helpers ────────────────────────────────────────────────


def redfin_get(url, params=None):
    """GET with Redfin-compatible headers. Returns str body."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/csv,application/json,text/html,*/*",
            "Referer": "https://www.redfin.com/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print("⌂ —", flush=True)
        print("---", flush=True)
        print(f"Redfin fetch error | {ANSI_MONOSPACE} {ACCENT_RED}", flush=True)
        print(f"{e} | {ANSI_MONOSPACE} {DIMMED} size=10", flush=True)
        sys.exit(0)


def get_cached(key, fetcher):
    """Simple file-cache wrapper."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, key)
    if os.path.exists(path):
        age = datetime.now().timestamp() - os.path.getmtime(path)
        if age < CACHE_TTL_SEC:
            with open(path, "r") as f:
                return f.read()
    data = fetcher()
    if data:
        with open(path, "w") as f:
            f.write(data)
    return data


# ─── Fetch AVM estimate for home ────────────────────────────────────────


def fetch_home_estimate():
    """Use Redfin autocomplete -> initialInfo -> avm to get home value."""
    # Step 1: autocomplete to get the Redfin URL path
    search_q = f"{HOME_ADDRESS}, {HOME_CITY}, {HOME_STATE} {HOME_ZIP}"
    ac_url = "https://www.redfin.com/stingray/do/location-autocomplete"
    ac_params = {"location": search_q, "v": "2", "al": "1"}
    body = redfin_get(ac_url, ac_params)

    if body and body.startswith("{}&&"):
        body = body[4:]

    try:
        ac_data = json.loads(body)
        rows = ac_data.get("payload", {}).get("sections", [])
        url_path = None
        for section in rows:
            for row in section.get("rows", []):
                if row.get("type") == "1":
                    url_path = row.get("url", "")
                    break
            if url_path:
                break
        if not url_path:
            for section in rows:
                for row in section.get("rows", []):
                    url_path = row.get("url", "")
                    if url_path:
                        break
                if url_path:
                    break
    except Exception:
        return None

    if not url_path:
        return None

    # Step 2: initialInfo to get propertyId
    ii_url = "https://www.redfin.com/stingray/api/home/details/initialInfo"
    ii_params = {"path": url_path}
    ii_body = redfin_get(ii_url, ii_params)
    if ii_body and ii_body.startswith("{}&&"):
        ii_body = ii_body[4:]

    try:
        ii_data = json.loads(ii_body)
        prop_id = ii_data["payload"]["propertyId"]
        listing_id = ii_data["payload"].get("listingId", "")
    except Exception:
        return None

    # Step 3: aboveTheFold for AVM / price data
    atf_url = "https://www.redfin.com/stingray/api/home/details/aboveTheFold"
    atf_params = {"propertyId": prop_id, "accessLevel": "1"}
    if listing_id:
        atf_params["listingId"] = listing_id

    atf_body = redfin_get(atf_url, atf_params)
    if atf_body and atf_body.startswith("{}&&"):
        atf_body = atf_body[4:]

    try:
        atf_data = json.loads(atf_body)
        payload = atf_data.get("payload", {})

        avm_info = payload.get("avmInfo", {})
        estimate = avm_info.get("predictedValue", None)
        low = avm_info.get("avmRangeLow", None)
        high = avm_info.get("avmRangeHigh", None)

        basic_info = payload.get("addressSectionInfo", {})
        beds = basic_info.get("beds", "")
        baths = basic_info.get("baths", "")
        sqft = (
            basic_info.get("sqFt", {}).get("value", "")
            if isinstance(basic_info.get("sqFt"), dict)
            else basic_info.get("sqFt", "")
        )
        year_built = basic_info.get("yearBuilt", "")
        redfin_url = f"https://www.redfin.com{url_path}"

        return {
            "estimate": estimate,
            "low": low,
            "high": high,
            "beds": beds,
            "baths": baths,
            "sqft": sqft,
            "year_built": year_built,
            "url": redfin_url,
            "property_id": prop_id,
        }
    except Exception:
        return None


# ─── Main output ─────────────────────────────────────────────────────────


def main():
    now = datetime.now()

    # --- Fetch data (with caching) ---
    home_raw = get_cached(
        "home_estimate.json", lambda: json.dumps(fetch_home_estimate())
    )
    try:
        home = json.loads(home_raw) if home_raw else None
    except Exception:
        home = None

    active_raw = get_cached(
        "active.csv",
        lambda: redfin_get(
            "https://www.redfin.com/stingray/api/gis-csv",
            {
                "al": "1",
                "num_homes": "350",
                "page_number": "1",
                "region_id": SEARCH_ZIP,
                "region_type": "2",
                "sf": "1,2,3,5,6,7",
                "sp": "true",
                "status": "9",
                "uipt": "1,2,3,4,5,6,7,8",
                "v": "8",
            },
        ),
    )

    sold_raw = get_cached(
        "sold.csv",
        lambda: redfin_get(
            "https://www.redfin.com/stingray/api/gis-csv",
            {
                "al": "1",
                "num_homes": "350",
                "page_number": "1",
                "region_id": SEARCH_ZIP,
                "region_type": "2",
                "sf": "1,2,3,5,6,7",
                "sp": "true",
                "status": "130",
                "uipt": "1,2,3,4,5,6,7,8",
                "v": "8",
                "sold_within_days": str(SOLD_DAYS),
            },
        ),
    )

    # Parse CSVs and filter by radius
    def parse_and_filter(raw, status_filter=None):
        if not raw:
            return []
        rows = []
        try:
            reader = csv.DictReader(io.StringIO(raw))
            for row in reader:
                try:
                    lat = float(row.get("LATITUDE", 0))
                    lon = float(row.get("LONGITUDE", 0))
                except (ValueError, TypeError):
                    continue
                dist = haversine_miles(HOME_LAT, HOME_LON, lat, lon)
                if dist <= RADIUS_MILES:
                    row["_distance"] = dist
                    if status_filter:
                        s = row.get("STATUS", "").strip().lower()
                        if s not in status_filter:
                            continue
                    rows.append(row)
        except Exception:
            pass
        return rows

    active_listings = parse_and_filter(
        active_raw, {"active", "active (hot home)", "contingent"}
    )
    pending_listings = parse_and_filter(active_raw, {"pending"})
    sold_listings = parse_and_filter(sold_raw)

    # Sort: active by price desc, sold by sold date desc
    active_listings.sort(key=lambda r: float(r.get("PRICE", 0) or 0), reverse=True)
    sold_listings.sort(key=lambda r: r.get("SOLD DATE", ""), reverse=True)

    # ─── MENU BAR HEADER ────────────────────────────────────────────
    if home and home.get("estimate"):
        est = fmt_price(home["estimate"])
        print(f"⌂ {est} | {TITLE_FONT}")
    else:
        count = len(active_listings)
        print(f"⌂ {count} Active | {TITLE_FONT}")

    print("---")

    # ─── YOUR HOME SECTION ──────────────────────────────────────────
    print(f"🏠  YOUR HOME | {HEADER_FONT} {WHITE}")
    print(f"     {HOME_ADDRESS} | {ANSI_MONOSPACE} {DIMMED} size=11")
    print(f"     {HOME_CITY}, {HOME_STATE} {HOME_ZIP} | {ANSI_MONOSPACE} {DIMMED} size=11")

    if home:
        if home.get("estimate"):
            est_str = fmt_price(home["estimate"])
            low_str = fmt_price(home.get("low")) if home.get("low") else "—"
            high_str = fmt_price(home.get("high")) if home.get("high") else "—"
            print(f"     Redfin Estimate  {est_str} | {ANSI_MONOSPACE} {ACCENT_GREEN} size=13")
            print(f"     Range  {low_str} – {high_str} | {ANSI_MONOSPACE} {DIMMED} size=11")
        else:
            print(f"     Estimate unavailable | {ANSI_MONOSPACE} {DIMMED}")

        details = []
        if home.get("beds"):
            details.append(f"{home['beds']}bd")
        if home.get("baths"):
            details.append(f"{home['baths']}ba")
        if home.get("sqft"):
            details.append(f"{fmt_sqft(home['sqft'])}sf")
        if home.get("year_built"):
            details.append(f"Built {home['year_built']}")
        if details:
            print(f"     {'  ·  '.join(details)} | {ANSI_MONOSPACE} {DIMMED} size=11")

        if home.get("url"):
            print(
                f"     View on Redfin ↗ | href={home['url']} {ANSI_MONOSPACE} {ACCENT_BLUE} size=11"
            )
    else:
        print(f"     Could not retrieve estimate | {ANSI_MONOSPACE} {DIMMED}")

    print("---")

    # ─── FOR SALE SECTION ────────────────────────────────────────────
    active_count = len(active_listings)
    pending_count = len(pending_listings)
    status_parts = [f"{active_count} Active"]
    if pending_count:
        status_parts.append(f"{pending_count} Pending")

    print(f"🏷️  FOR SALE  ({' · '.join(status_parts)}) | {HEADER_FONT} {WHITE}")

    if active_listings:
        prices = [float(r["PRICE"]) for r in active_listings if r.get("PRICE")]
        if prices:
            med = sorted(prices)[len(prices) // 2]
            print(
                f"     Median {fmt_price(med)}  ·  Range {fmt_price(min(prices))}–{fmt_price(max(prices))} | {ANSI_MONOSPACE} {DIMMED} size=11"
            )

        print(
            f"     {'ADDRESS':<24} {'PRICE':>10} {'BD/BA':>8} {'SQFT':>7} {'$/SF':>6} {'DOM':>5} | {ANSI_MONOSPACE} {LABEL_COLOR} size=10"
        )

        for row in active_listings[:15]:
            addr = short_addr(row.get("ADDRESS", ""))
            price = fmt_price(row.get("PRICE"))
            bb = fmt_beds_baths(row.get("BEDS"), row.get("BATHS"))
            sqft = fmt_sqft(row.get("SQUARE FEET"))
            ppsf = fmt_ppsf(row.get("$/SQUARE FEET"))
            dom = fmt_dom(row.get("DAYS ON MARKET"))
            url = ""
            for k in row:
                if k.startswith("URL"):
                    url = row[k]
                    break
            href = f"href={url}" if url else ""
            print(
                f"     {addr:<24} {price:>10} {bb:>8} {sqft:>7} {ppsf:>6} {dom:>5} | {ANSI_MONOSPACE} size=11 {href}"
            )
    else:
        print(
            f"     No active listings within {RADIUS_MILES}mi | {ANSI_MONOSPACE} {DIMMED}"
        )

    if active_count > 15:
        print(f"     … and {active_count - 15} more | {ANSI_MONOSPACE} {DIMMED} size=10")

    redfin_search = f"https://www.redfin.com/zipcode/{SEARCH_ZIP}"
    print(
        f"     View all on Redfin ↗ | href={redfin_search} {ANSI_MONOSPACE} {ACCENT_BLUE} size=11"
    )

    print("---")

    # ─── RECENTLY SOLD SECTION ──────────────────────────────────────
    sold_count = len(sold_listings)
    print(f"✅  SOLD – LAST {SOLD_DAYS} DAYS  ({sold_count}) | {HEADER_FONT} {WHITE}")

    if sold_listings:
        sold_prices = [float(r["PRICE"]) for r in sold_listings if r.get("PRICE")]
        if sold_prices:
            med_sold = sorted(sold_prices)[len(sold_prices) // 2]
            print(
                f"     Median {fmt_price(med_sold)}  ·  Range {fmt_price(min(sold_prices))}–{fmt_price(max(sold_prices))} | {ANSI_MONOSPACE} {DIMMED} size=11"
            )

        print(
            f"     {'ADDRESS':<24} {'PRICE':>10} {'BD/BA':>8} {'SQFT':>7} {'$/SF':>6} {'SOLD':>10} | {ANSI_MONOSPACE} {LABEL_COLOR} size=10"
        )

        for row in sold_listings[:15]:
            addr = short_addr(row.get("ADDRESS", ""))
            price = fmt_price(row.get("PRICE"))
            bb = fmt_beds_baths(row.get("BEDS"), row.get("BATHS"))
            sqft = fmt_sqft(row.get("SQUARE FEET"))
            ppsf = fmt_ppsf(row.get("$/SQUARE FEET"))
            sold_dt = row.get("SOLD DATE", "")[:10] if row.get("SOLD DATE") else "—"
            url = ""
            for k in row:
                if k.startswith("URL"):
                    url = row[k]
                    break
            href = f"href={url}" if url else ""
            print(
                f"     {addr:<24} {price:>10} {bb:>8} {sqft:>7} {ppsf:>6} {sold_dt:>10} | {ANSI_MONOSPACE} size=11 {href}"
            )
    else:
        print(
            f"     No sold listings within {RADIUS_MILES}mi | {ANSI_MONOSPACE} {DIMMED}"
        )

    if sold_count > 15:
        print(f"     … and {sold_count - 15} more | {ANSI_MONOSPACE} {DIMMED} size=10")

    redfin_sold = (
        f"https://www.redfin.com/zipcode/{SEARCH_ZIP}/filter/include=sold-{SOLD_DAYS}days"
    )
    print(
        f"     View all on Redfin ↗ | href={redfin_sold} {ANSI_MONOSPACE} {ACCENT_BLUE} size=11"
    )

    print("---")

    # ─── FOOTER ──────────────────────────────────────────────────────
    updated = now.strftime("%-m/%-d  %-I:%M %p")
    print(
        f"Updated {updated}  ·  {SEARCH_ZIP}  ·  {RADIUS_MILES}mi radius | {ANSI_MONOSPACE} {DIMMED} size=10"
    )
    print(f"Refresh | refresh=true {ANSI_MONOSPACE} size=11")
    print(
        f"Clear Cache | bash=/bin/rm param0=-rf param1={CACHE_DIR} refresh=true terminal=false {ANSI_MONOSPACE} size=11"
    )


if __name__ == "__main__":
    main()
