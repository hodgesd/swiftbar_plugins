#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = [
#   "aiohttp>=3.8.0",
#   "beautifulsoup4>=4.9.0",
# ]
# ///
"""
Lottery Value — SwiftBar plugin
Compares Powerball and Mega Millions jackpot value per dollar and shows
WAIT/BUY against a conservative rare-purchase trigger.

Data sources: the official powerball.com homepage (HTML) and the official
Mega Millions GetLatestDrawData JSON endpoint. If a fetch or parse fails,
the last successful values are shown and marked stale. Parsed values are
sanity-checked (cash must be 30-75% of the advertised annuity) so a markup
change cannot silently produce plausible garbage.

Rename to control the refresh cadence (SwiftBar filename convention), e.g.
lottery_value.12h.py or lottery_value.1d.py, and chmod +x it.

This is entertainment budgeting software, not investment advice.
"""
# <bitbar.title>Lottery Value</bitbar.title>
# <bitbar.author>hodgesd</bitbar.author>
# <bitbar.desc>Powerball vs Mega Millions jackpot value with a rare-buy trigger</bitbar.desc>
# <bitbar.dependencies>python,aiohttp</bitbar.dependencies>
# <bitbar.version>2.0</bitbar.version>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>false</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
# <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

from __future__ import annotations

import asyncio
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup

# --- CONFIGURATION ---

APP_NAME = "Lottery Value"
CONFIG_DIR = Path.home() / "Library" / "Application Support" / "LotteryValueMenu"
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_FILE = CONFIG_DIR / "cache.json"
STATE_FILE = CONFIG_DIR / "state.json"

POWERBALL_URL = "https://www.powerball.com/"
MEGA_URL = "https://www.megamillions.com/"
MEGA_API_URL = (
    "https://www.megamillions.com/cmspages/utilservice.asmx/GetLatestDrawData"
)

FETCH_TIMEOUT_SECONDS = 20
COLOR_BUY = "#33aa55"
COLOR_WARN = "#e0a020"
COLOR_ERROR = "#cc4444"
COLOR_MUTED = "#888888"
FETCH_RETRY_COUNT = 1

DEFAULT_CONFIG = {
    "buy_trigger_score": 0.70,
    "minimum_advertised_jackpot_millions": 900,
    "daily_budget_cap": 10,
    "notify_when_triggered": True,
}

# Real cash options historically run ~45-52% of the advertised annuity.
# Anything outside this band almost certainly means the parser grabbed the
# wrong number, so we reject it rather than display it.
CASH_RATIO_MIN = 0.30
CASH_RATIO_MAX = 0.75

# Non-jackpot prize tables as (base prize, odds "1 in N"), taken from the
# official prize charts (powerball.com/powerball-prize-chart and
# megamillions.com/How-to-Play, September 2026). Powerball's base $2 ticket
# has no multiplier (Power Play is a paid add-on and is ignored). Mega
# Millions' $5 ticket applies a random multiplier to every non-jackpot
# prize, so its lower-tier EV is the base EV times the expected multiplier.
POWERBALL_LOWER_TIERS = [
    (1_000_000, 11_688_053.52),
    (50_000, 913_129.18),
    (100, 36_525.17),
    (100, 14_494.11),
    (7, 579.76),
    (7, 701.33),
    (4, 91.98),
    (4, 38.32),
]
MEGA_LOWER_TIERS = [
    (1_000_000, 12_629_232),
    (10_000, 893_761),
    (500, 38_859),
    (200, 13_965),
    (10, 607),
    (10, 665),
    (7, 86),
    (5, 35),
]
# multiplier -> odds "1 in N" (the published odds sum to ~1.0008, so they are
# normalized when the expectation is taken).
MEGA_MULTIPLIER_ODDS = {2: 2.13, 3: 3.2, 4: 8, 5: 16, 10: 32}

# Drawing schedule (weekday numbers, Monday=0) and local draw time in the
# Eastern time zone, used only to show the next drawing in the dropdown.
DRAW_TZ = ZoneInfo("America/New_York")


def expected_multiplier(odds: dict[int, float]) -> float:
    weights = {m: 1 / o for m, o in odds.items()}
    return sum(m * w for m, w in weights.items()) / sum(weights.values())


def lower_tier_ev(tiers: list[tuple[float, float]], multiplier: float = 1.0) -> float:
    """Pre-tax expected value of all non-jackpot prizes for one play."""
    return multiplier * sum(prize / odds for prize, odds in tiers)


# lower_tier_ev feeds the total score, which both ranks the games and drives
# the BUY trigger, so Mega Millions' $5 price (multiplier included) is judged
# on the same per-dollar basis as Powerball's $2.
GAME_RULES = {
    "Powerball": {
        "ticket_price": 2.0,
        "jackpot_odds": 292_201_338,
        "lower_tier_ev": lower_tier_ev(POWERBALL_LOWER_TIERS),
        "draw_days": (0, 2, 5),  # Mon, Wed, Sat
        "draw_time": (22, 59),
        "url": POWERBALL_URL,
    },
    "Mega Millions": {
        "ticket_price": 5.0,
        "jackpot_odds": 290_472_336,
        "lower_tier_ev": lower_tier_ev(
            MEGA_LOWER_TIERS, expected_multiplier(MEGA_MULTIPLIER_ODDS)
        ),
        "draw_days": (1, 4),  # Tue, Fri
        "draw_time": (23, 0),
        "url": MEGA_URL,
        # Official JSON endpoint; the homepage renders its cash value with
        # JavaScript so HTML parsing cannot see it.
        "json_url": MEGA_API_URL,
    },
}


@dataclass
class GameData:
    name: str
    advertised_millions: float
    cash_millions: float
    ticket_price: float
    jackpot_odds: int
    fetched_at: str
    source_url: str
    lower_tier_ev: float = 0.0
    stale: bool = False
    fetch_error: Optional[str] = None

    @property
    def cash_ratio(self) -> float:
        if self.advertised_millions <= 0:
            return 0.0
        return self.cash_millions / self.advertised_millions

    @property
    def jackpot_ev_dollars(self) -> float:
        """Pre-tax jackpot-only expected value, ignoring split risk."""
        return self.cash_millions * 1_000_000 / self.jackpot_odds

    @property
    def score(self) -> float:
        """Jackpot-only value score, shown for reference. 1.00 means the
        pre-tax jackpot EV equals the ticket cost."""
        return self.jackpot_ev_dollars / self.ticket_price

    @property
    def total_score(self) -> float:
        """Jackpot + lower-tier EV per dollar of ticket price. Drives the
        BUY trigger and the cross-game ranking."""
        return (self.jackpot_ev_dollars + self.lower_tier_ev) / self.ticket_price


# --- PERSISTENCE HELPERS ---


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, value) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))


def cached_games() -> dict[str, GameData]:
    raw = load_json(CACHE_FILE, {})
    games = {}
    for name, item in raw.items():
        try:
            item["stale"] = True
            item.pop("fetch_error", None)
            games[name] = GameData(**item)
        except (TypeError, KeyError):
            pass
    return games


def save_games(games: dict[str, GameData]) -> None:
    save_json(
        CACHE_FILE,
        {
            name: {k: v for k, v in asdict(g).items() if k != "fetch_error"}
            for name, g in games.items()
        },
    )


# --- PARSING ---


def money_millions(value: float) -> str:
    if value >= 1000:
        return f"${value / 1000:.2f}B"
    return f"${value:,.0f}M"


def parse_money_millions(text: str) -> Optional[float]:
    """Accepts "$707 Million", "$1.05 Billion", "$1,050 Million", "$307.7 MILLION"."""
    clean = text.replace(",", "")  # "$1,050 Million" -> "$1050 Million"
    match = re.search(
        r"\$\s*([0-9]+(?:\.[0-9]+)?)\s*(BILLION|MILLION|B|M)\b",
        clean,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).upper()
    return amount * 1000 if unit in {"BILLION", "B"} else amount


def validate_jackpot_pair(advertised: float, cash: float) -> None:
    """Reject parses that cannot be a real annuity/cash pair."""
    if cash >= advertised:
        raise ValueError(
            f"Cash ({cash}M) is not below advertised jackpot ({advertised}M)"
        )
    ratio = cash / advertised
    if not (CASH_RATIO_MIN <= ratio <= CASH_RATIO_MAX):
        raise ValueError(
            f"Cash/annuity ratio {ratio:.2f} outside plausible range; "
            f"likely a parsing error"
        )


def extract_jackpots(html: str) -> tuple[float, float]:
    """Parse advertised + cash jackpots from visible page text, then validate
    that the pair is plausible before returning it."""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    normalized = re.sub(r"\s+", " ", text)

    advertised_patterns = [
        r"JACKPOT\s+IS\s+CURRENTLY(?:\s+JACKPOT)?\s+(\$[\d.,]+\s*(?:MILLION|BILLION|M|B))",
        r"NEXT\s+JACKPOT[^$]{0,80}(\$[\d.,]+\s*(?:MILLION|BILLION|M|B))",
        r"ESTIMATED\s+JACKPOT[^$]{0,80}(\$[\d.,]+\s*(?:MILLION|BILLION|M|B))",
    ]
    cash_patterns = [
        r"CASH\s+(?:VALUE|OPTION|JACKPOT)[^$]{0,80}(\$[\d.,]+\s*(?:MILLION|BILLION|M|B))",
        r"JACKPOT\s*/\s*CASH\s+VALUE[^$]{0,120}\$[\d.,]+\s*(?:MILLION|BILLION|M|B)\s*/\s*(\$[\d.,]+\s*(?:MILLION|BILLION|M|B))",
    ]

    advertised = None
    for pattern in advertised_patterns:
        m = re.search(pattern, normalized, flags=re.IGNORECASE)
        if m:
            advertised = parse_money_millions(m.group(1))
            if advertised:
                break

    cash = None
    for pattern in cash_patterns:
        m = re.search(pattern, normalized, flags=re.IGNORECASE)
        if m:
            cash = parse_money_millions(m.group(1))
            if cash:
                break

    # Flexible fallback: locate all dollar amounts and choose plausible values.
    amounts = [parse_money_millions(m.group(0)) for m in re.finditer(
        r"\$[\d.,]+\s*(?:MILLION|BILLION|M|B)\b", normalized, flags=re.IGNORECASE
    )]
    amounts = [x for x in amounts if x is not None and x >= 10]

    if advertised is None and amounts:
        advertised = max(amounts)
    if cash is None and advertised is not None:
        # Cash must sit inside the plausible ratio band; take the largest
        # candidate that does. This prevents grabbing a historical jackpot
        # or another game's figure from elsewhere on the page.
        candidates = [
            x for x in amounts
            if x < advertised
            and CASH_RATIO_MIN <= (x / advertised) <= CASH_RATIO_MAX
        ]
        if candidates:
            cash = max(candidates)

    if advertised is None or cash is None:
        raise ValueError("Could not identify both advertised and cash jackpot values")

    validate_jackpot_pair(advertised, cash)
    return advertised, cash


def extract_jackpots_megamillions(payload: str) -> tuple[float, float]:
    """Parse the official Mega Millions GetLatestDrawData JSON. The ASMX
    endpoint wraps its JSON in {"d": "<json string>"}. Next* fields are the
    upcoming drawing's estimate; fall back to Current* if absent."""
    outer = json.loads(payload)
    inner = json.loads(outer["d"]) if isinstance(outer.get("d"), str) else outer
    jackpot = inner["Jackpot"]
    advertised_dollars = jackpot.get("NextPrizePool") or jackpot["CurrentPrizePool"]
    cash_dollars = jackpot.get("NextCashValue") or jackpot["CurrentCashValue"]
    advertised = float(advertised_dollars) / 1_000_000
    cash = float(cash_dollars) / 1_000_000
    validate_jackpot_pair(advertised, cash)
    return advertised, cash


# --- FETCHING ---


async def fetch_html(
    session: aiohttp.ClientSession,
    url: str,
    method: str = "GET",
    json_body: Optional[dict] = None,
) -> tuple[str, Optional[str]]:
    """Fetch a URL with retry logic. Returns (body, error_message)."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 LotteryValueMenu/2.0"
        )
    }
    for attempt in range(FETCH_RETRY_COUNT + 1):
        try:
            async with session.request(
                method,
                url,
                headers=headers,
                json=json_body,
                timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SECONDS),
            ) as response:
                if response.status == 200:
                    return await response.text(), None
                error_msg = f"HTTP {response.status}"
                if attempt < FETCH_RETRY_COUNT:
                    backoff = 2.0 if response.status == 429 else 0.5 * (attempt + 1)
                    await asyncio.sleep(backoff)
                    continue
                return "", error_msg
        except asyncio.TimeoutError:
            if attempt < FETCH_RETRY_COUNT:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            return "", "Timeout"
        except Exception as e:
            if attempt < FETCH_RETRY_COUNT:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            return "", type(e).__name__
    return "", "Unknown error"


async def fetch_game(session: aiohttp.ClientSession, name: str) -> GameData:
    rules = GAME_RULES[name]
    if "json_url" in rules:
        payload, error = await fetch_html(
            session, rules["json_url"], method="POST", json_body={}
        )
        if error:
            raise ValueError(error)
        advertised, cash = extract_jackpots_megamillions(payload)
    else:
        html, error = await fetch_html(session, rules["url"])
        if error:
            raise ValueError(error)
        advertised, cash = extract_jackpots(html)
    return GameData(
        name=name,
        advertised_millions=advertised,
        cash_millions=cash,
        ticket_price=rules["ticket_price"],
        jackpot_odds=rules["jackpot_odds"],
        lower_tier_ev=rules["lower_tier_ev"],
        fetched_at=datetime.now(timezone.utc).isoformat(),
        source_url=rules["url"],
    )


async def fetch_all_games() -> dict[str, GameData]:
    """Fetch both games in parallel; fall back to cache (marked stale) on failure."""
    old = cached_games()
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *(fetch_game(session, name) for name in GAME_RULES),
            return_exceptions=True,
        )
    games: dict[str, GameData] = {}
    for name, result in zip(GAME_RULES, results):
        if isinstance(result, GameData):
            games[name] = result
        else:
            error = str(result)
            if name in old:
                games[name] = old[name]
                games[name].fetch_error = error
            else:
                games[name] = GameData(
                    name=name,
                    advertised_millions=0,
                    cash_millions=0,
                    ticket_price=GAME_RULES[name]["ticket_price"],
                    jackpot_odds=GAME_RULES[name]["jackpot_odds"],
                    lower_tier_ev=GAME_RULES[name]["lower_tier_ev"],
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    source_url=GAME_RULES[name]["url"],
                    stale=True,
                    fetch_error=error,
                )
    if any(not g.stale for g in games.values()):
        save_games({n: g for n, g in games.items() if g.advertised_millions > 0})
    return games


def next_draw(name: str, now: Optional[datetime] = None) -> datetime:
    """Next scheduled drawing for a game, in Eastern time."""
    rules = GAME_RULES[name]
    now_et = (now or datetime.now(timezone.utc)).astimezone(DRAW_TZ)
    hour, minute = rules["draw_time"]
    for offset in range(8):
        candidate = (now_et + timedelta(days=offset)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate.weekday() in rules["draw_days"] and candidate > now_et:
            return candidate
    raise RuntimeError(f"No drawing scheduled for {name}")


# --- RECOMMENDATION ---


def recommendation(game: GameData, config: dict) -> tuple[bool, str]:
    score_ok = game.total_score >= float(config["buy_trigger_score"])
    jackpot_ok = game.advertised_millions >= float(
        config["minimum_advertised_jackpot_millions"]
    )
    if score_ok and jackpot_ok:
        return True, "Rare-buy trigger met"
    reasons = []
    if not jackpot_ok:
        reasons.append(
            f"below {money_millions(config['minimum_advertised_jackpot_millions'])}"
        )
    if not score_ok:
        reasons.append(f"value score below {config['buy_trigger_score']:.0%}")
    text = "; ".join(reasons)
    return False, text[0].upper() + text[1:]  # capitalize first letter only


def maybe_notify(games: dict[str, GameData], config: dict) -> None:
    """Send a macOS notification when the trigger is newly met. The signature
    is persisted so repeated SwiftBar runs don't re-alert for the same
    jackpot."""
    if not config.get("notify_when_triggered", True):
        return
    triggered = [g for g in games.values() if recommendation(g, config)[0]]
    if not triggered:
        return
    signature = sorted(
        [g.name, g.advertised_millions, g.cash_millions] for g in triggered
    )
    state = load_json(STATE_FILE, {})
    if signature == state.get("last_notification_signature"):
        return
    state["last_notification_signature"] = signature
    save_json(STATE_FILE, state)
    best = max(triggered, key=lambda g: g.total_score)
    message = (
        f"{money_millions(best.advertised_millions)} advertised; "
        f"{money_millions(best.cash_millions)} cash; "
        f"{best.total_score:.0%} total value score."
    )
    script = (
        f"display notification {json.dumps(message)} "
        f"with title {json.dumps(APP_NAME)} "
        f"subtitle {json.dumps(best.name + ' rare-buy trigger met')} "
        f'sound name "Glass"'
    )
    subprocess.run(["osascript", "-e", script], check=False)


# --- DISPLAY ---


def generate_swiftbar_menu(games: dict[str, GameData], config: dict) -> None:
    valid = [g for g in games.values() if g.advertised_millions > 0]
    failed = [g for g in games.values() if g.advertised_millions <= 0]
    any_error = any(g.fetch_error for g in games.values())

    # -- MENU BAR LINE -- (SF Symbol only; color carries the state)
    if not valid:
        print(f"| sfimage=ticket sfcolor={COLOR_ERROR}")
        print("---")
        print("No jackpot data | color=#888888")
        for g in games.values():
            if g.fetch_error:
                print(f"⚠️ {g.name}: {g.fetch_error} | color=#cc4444 size=11")
        print("Refresh now | refresh=true")
        return

    # Rank by TOTAL value (jackpot + estimated lower-tier EV) per dollar so
    # the comparison is fair to both games' price structures.
    ordered = sorted(valid, key=lambda g: g.total_score, reverse=True)
    best = ordered[0]
    best_buy, _ = recommendation(best, config)
    if best_buy:
        print(f"| sfimage=ticket.fill sfcolor={COLOR_BUY}")
    elif any_error:
        print(f"| sfimage=ticket sfcolor={COLOR_WARN}")
    else:
        print("| sfimage=ticket")

    print("---")
    now = datetime.now(timezone.utc)

    for game in ordered:
        triggered, reason = recommendation(game, config)
        try:
            age = now - datetime.fromisoformat(game.fetched_at)
        except ValueError:
            age = timedelta(0)
        is_stale = game.stale or age > timedelta(hours=30)
        stale_note = " • stale" if is_stale else ""
        error_icon = "⚠️ " if game.fetch_error else ""

        verdict = "BUY ✅" if triggered else "WAIT"
        header = (
            f"{error_icon}{verdict} — {game.name}: "
            f"{money_millions(game.advertised_millions)}{stale_note}"
        )
        tooltip_parts = [reason]
        if game.fetch_error:
            tooltip_parts.append(f"Error: {game.fetch_error}")
        tooltip = " | ".join(tooltip_parts).replace('"', "'")
        color = f" color={COLOR_BUY}" if triggered else ""
        print(
            f'{header} | href={game.source_url} font=Menlo-Bold '
            f'tooltip="{tooltip}"{color}'
        )
        draw = next_draw(game.name, now)
        print(f"--Next draw: {draw:%a %b} {draw.day}, "
              f"{draw:%-I:%M %p} ET | font=Menlo size=12")
        print(f"--Cash value: {money_millions(game.cash_millions)} "
              f"({game.cash_ratio:.0%} of annuity) | font=Menlo size=12")
        print(f"--Jackpot EV: ${game.jackpot_ev_dollars:.2f} per "
              f"${game.ticket_price:.0f} ticket | font=Menlo size=12")
        print(f"--Jackpot-only score: {game.score:.0%} of ticket price | "
              f"font=Menlo size=12")
        print(f"--Total value score: {game.total_score:.0%} "
              f"(incl. est. small prizes) | font=Menlo size=12")
        print(f"--{reason} | size=12 color={COLOR_BUY if triggered else COLOR_MUTED}")
        if game.fetch_error:
            print(f"--⚠️ {game.fetch_error} (showing cached values) | "
                  f"size=11 color=#cc4444")
        print(f"--Open official page | href={game.source_url}")

    for game in failed:
        print(f"⚠️ {game.name}: no data ({game.fetch_error}) | "
              f"href={game.source_url} font=Menlo-Bold color={COLOR_ERROR}")

    # -- FOOTER --
    print("---")
    print(
        f"Trigger: ≥ {money_millions(config['minimum_advertised_jackpot_millions'])} "
        f"and ≥ {config['buy_trigger_score']:.0%} total value score | "
        f"size=11 color=#888888"
    )
    print(
        f"Budget ceiling (advisory): ${config['daily_budget_cap']:.0f} "
        f"per drawing day | size=11 color=#888888"
    )
    print("Rationale | size=11 color=#888888")
    for line in (
        "The total value score is (cash jackpot ÷ odds + small-prize EV)",
        "÷ ticket price, with small-prize EV computed from the official",
        "prize charts (MM's $5 includes a multiplier on lower tiers).",
        "It is pre-tax and ignores split risk, so a 70% trigger is a",
        "relative signal — never a claim the ticket is a good investment.",
        "The jackpot-only score is shown for reference. The $900M floor",
        "preserves the near-$1B habit. The budget cap is the most",
        "important rule: entertainment only, never chase losses.",
    ):
        print(f"--{line} | size=12 color=#888888")
    print("---")
    print("Refresh now | refresh=true")
    print(
        f'Edit settings | bash=/usr/bin/open param0=-t '
        f'param1="{CONFIG_FILE}" terminal=false'
    )


# --- MAIN ---


def main() -> None:
    config = {**DEFAULT_CONFIG, **load_json(CONFIG_FILE, {})}
    save_json(CONFIG_FILE, config)
    games = asyncio.run(fetch_all_games())
    maybe_notify(games, config)
    generate_swiftbar_menu(games, config)


if __name__ == "__main__":
    main()
