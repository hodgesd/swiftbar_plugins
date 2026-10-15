"""Tests for home_comps_uv.8hr.py against trimmed real Redfin responses in
tests/fixtures/home_comps/.

Run:  uv run --with pytest pytest tests/test_home_comps.py
"""
import importlib.util
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "home_comps"

spec = importlib.util.spec_from_file_location("home_comps", ROOT / "home_comps_uv.8hr.py")
hc = importlib.util.module_from_spec(spec)
sys.modules["home_comps"] = hc
spec.loader.exec_module(hc)

TODAY = date(2026, 10, 14)
HOME = (38.490841, -89.921793)


def read(name):
    return (FIX / name).read_text()


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(hc, "CACHE_DIR", str(tmp_path / "cache"))
    return tmp_path / "cache"


def home(pid, price, dom=None, address=None, **kw):
    base = {"id": pid, "address": address or f"{pid} Test St", "url": f"https://x/{pid}",
            "price": price, "beds": 3.0, "baths": 2.0, "sqft": 2000, "ppsf": 150, "dom": dom,
            "status": "Active", "sold_date": None, "lat": HOME[0], "lon": HOME[1],
            "year_built": 2000, "hoa": None}
    return {**base, **kw}


# --- parsers ----------------------------------------------------------------------------

def test_parse_gis_active():
    homes = hc.parse_gis(read("gis_active.json"))
    assert len(homes) == 4
    h = homes[0]
    assert h["url"].startswith("https://www.redfin.com/IL/Belleville/")
    assert isinstance(h["price"], int) and h["price"] > 0
    assert h["lat"] and h["lon"] and h["id"].isdigit()
    assert all(x["status"] == "Active" for x in homes)


def test_parse_gis_sold_dates():
    homes = hc.parse_gis(read("gis_sold.json"))
    fairway = next(h for h in homes if h["address"] == "2845 Fairway Dr")
    assert fairway["sold_date"] == "2026-08-12"
    assert fairway["price"] == 425000
    assert all(h["status"] == "Closed" for h in homes)


def test_parse_gis_rejects_challenge_and_errors():
    with pytest.raises(hc.FetchError):
        hc.parse_gis("<html>Request blocked</html>")
    with pytest.raises(hc.FetchError, match="bad region"):
        hc.parse_gis('{}&&{"resultCode": 1, "errorMessage": "bad region"}')


def test_parse_home_page():
    info = hc.parse_home_page(read("home_page.html"))
    assert info == {"estimate": 462148, "beds": 4.0, "baths": 3.0, "sqft": 2800,
                    "year_built": 1993, "property_id": "113600430"}


def test_parse_home_page_rejects_challenge():
    with pytest.raises(hc.FetchError):
        hc.parse_home_page("<html><body>Please verify you are human</body></html>")


def test_parse_census():
    body = json.dumps({"result": {"addressMatches": [
        {"matchedAddress": "2616 PRO TOUR DR", "coordinates": {"x": -89.92, "y": 38.49}}]}})
    assert hc.parse_census(body) == (38.49, -89.92)
    with pytest.raises(hc.FetchError):
        hc.parse_census(json.dumps({"result": {"addressMatches": []}}))


def test_match_home():
    homes = [home("1", 1, address="2625 Pro Tour"), home("2", 1, address="2616 Other St"),
             home("3", 1, address="2616 Pro Tour Dr"), home("4", 1, address="26 Pro Tour Dr")]
    assert hc.match_home(homes, "2616 Pro Tour Dr")["id"] == "3"
    assert hc.match_home(homes, "2616 Pro Tour Drive")["id"] == "3"
    assert hc.match_home(homes, "9999 Pro Tour Dr") is None


# --- geometry / links -------------------------------------------------------------------

def test_bbox_poly_closed_and_spans_radius():
    pts = [tuple(map(float, p.split())) for p in hc.bbox_poly(*HOME, 1.5).split(",")]
    assert pts[0] == pts[-1] and len(pts) == 5
    lons, lats = zip(*pts)
    assert min(lats) < HOME[0] < max(lats) and min(lons) < HOME[1] < max(lons)
    assert hc.haversine_miles(HOME[0], HOME[1], max(lats), HOME[1]) == pytest.approx(1.5, rel=0.01)


def test_within_radius_trims_box_corners():
    near = home("near", 1, lat=HOME[0] + 0.01, lon=HOME[1])     # ~0.7 mi
    corner = home("corner", 1, lat=HOME[0] + 0.02, lon=HOME[1] + 0.025)  # ~1.9 mi
    nowhere = home("nowhere", 1, lat=None, lon=None)
    out = hc.within_radius([near, corner, nowhere], *HOME, 1.5)
    assert [h["id"] for h in out] == ["near"] and 0.6 < out[0]["dist"] < 0.8


@pytest.mark.parametrize("days,token", [(7, "sold-1wk"), (30, "sold-1mo"), (90, "sold-3mo"),
                                        (120, "sold-6mo"), (365, "sold-1yr"), (2000, "sold-5yr")])
def test_sold_filter_token(days, token):
    assert hc.sold_filter_token(days) == token


def test_gis_params_status_codes():
    assert hc.gis_params("p", "active")["status"] == 9
    assert hc.gis_params("p", "pending")["status"] == 130
    sold = hc.gis_params("p", "sold", 60)
    assert sold["status"] == 9 and sold["sold_within_days"] == 60
    assert hc.gis_params("p", "active")["mpt"] == 99  # Redfin ignores poly without it


# --- NEW / price-change tracking -------------------------------------------------------

def test_first_sight_backdates_with_dom():
    state = hc.update_state({}, [home("a", 300000, dom=3), home("b", 400000, dom=40),
                                 home("c", 500000, dom=None)], TODAY)
    ann = {pid: hc.annotate(home(pid, p), state, TODAY)
           for pid, p in (("a", 300000), ("b", 400000), ("c", 500000))}
    assert state["listings"]["a"]["first_seen"] == "2026-10-11"
    assert ann["a"]["is_new"] and not ann["b"]["is_new"] and ann["c"]["is_new"]


def test_price_cut_then_expires():
    state = hc.update_state({}, [home("a", 300000, dom=40)], TODAY)
    later = TODAY + timedelta(days=5)
    hc.update_state(state, [home("a", 289900, dom=45)], later)
    h = hc.annotate(home("a", 289900), state, later)
    assert h["change"] == -10100 and h["orig_price"] == 300000
    assert hc.note_for(h) == "▼$10K"

    much_later = later + timedelta(days=hc.CHANGE_DAYS + 1)
    h = hc.annotate(home("a", 289900), state, much_later)
    assert h["change"] is None and h["orig_price"] == 300000 and hc.note_for(h) == ""


def test_second_cut_reports_latest_step():
    state = hc.update_state({}, [home("a", 300000, dom=40)], TODAY)
    hc.update_state(state, [home("a", 290000)], TODAY + timedelta(days=2))
    hc.update_state(state, [home("a", 280000)], TODAY + timedelta(days=4))
    h = hc.annotate(home("a", 280000), state, TODAY + timedelta(days=4))
    assert h["change"] == -10000 and h["orig_price"] == 300000


def test_missing_price_is_not_a_change():
    state = hc.update_state({}, [home("a", 300000, dom=40)], TODAY)
    hc.update_state(state, [home("a", None)], TODAY + timedelta(days=1))
    assert state["listings"]["a"]["price"] == 300000
    assert state["listings"]["a"]["price_changed"] is None


def test_state_prunes_old_listings():
    state = hc.update_state({}, [home("old", 1, dom=0)], TODAY)
    hc.update_state(state, [home("new", 1, dom=0)], TODAY + timedelta(days=hc.STATE_KEEP_DAYS + 1))
    assert set(state["listings"]) == {"new"}


# --- cache ------------------------------------------------------------------------------

def test_cache_fresh_hit_skips_fetch():
    calls = []
    hc.cached("x.json", 3600, lambda: calls.append(1) or [1])
    obj, info = hc.cached("x.json", 3600, lambda: calls.append(1) or [2])
    assert obj == [1] and len(calls) == 1 and info["error"] is None


def test_cache_failure_falls_back_and_is_not_cached():
    hc.cached("x.json", 3600, lambda: [1])
    hc.force_stale()

    def boom():
        raise hc.FetchError("HTTP 403")

    obj, info = hc.cached("x.json", 3600, boom)
    assert obj == [1] and info["stale"] and info["error"] == "HTTP 403"
    assert info["fetched"] > time.time() - 60  # real fetch time survives --force
    obj, info = hc.cached("x.json", 3600, lambda: [2])  # still stale, so refetches
    assert obj == [2] and info["error"] is None


def test_cache_failure_without_fallback():
    obj, info = hc.cached("y.json", 3600, lambda: (_ for _ in ()).throw(hc.FetchError("down")))
    assert obj is None and info["fetched"] is None and info["error"] == "down"


def test_force_stale_keeps_state_and_location(tmp_cache):
    hc.write_json(hc.STATE_FILE, {})
    hc.write_json(hc.LOCATION_FILE, {})
    hc.cached("active.json", 3600, lambda: [])
    hc.force_stale()
    assert os.path.getmtime(tmp_cache / "active.json") == 0
    assert os.path.getmtime(tmp_cache / hc.STATE_FILE) > 0
    assert os.path.getmtime(tmp_cache / hc.LOCATION_FILE) > 0


# --- config -----------------------------------------------------------------------------

def test_config_env_fallback_and_defaults(tmp_path):
    env = {"REDFIN_ADDRESS": "1 A St", "REDFIN_CITY": "X", "REDFIN_STATE": "IL",
           "REDFIN_ZIP": "62220"}
    cfg = hc.load_config(str(tmp_path / "none.json"), env)
    assert cfg["radius"] == 3.0 and cfg["sold_days"] == 90
    assert cfg["lat"] is None and cfg["redfin_url"] == ""


def test_config_missing_fields(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"address": "1 A St", "lat": 1, "lon": 2}))
    with pytest.raises(hc.ConfigError) as e:
        hc.load_config(str(p), {})
    assert e.value.missing == ["city", "state", "zip"]


# --- render smoke test ------------------------------------------------------------------

def test_render_badges_and_title(capsys):
    hc.write_json(hc.STATE_FILE, hc.update_state(
        {}, [home("cut", 300000, dom=60)], TODAY - timedelta(days=3)))
    active = [home("cut", 289900, dom=63, address="1 Cut Ct"),
              home("fresh", 450000, dom=2, address="2 Fresh Ct")]
    sold = [home("s", 400000, status="Closed", sold_date="2026-09-30")]
    results = {"active": (active, {"error": None, "fetched": time.time()}),
               "pending": ([], {"error": None, "fetched": time.time()}),
               "sold": (sold, {"error": None, "fetched": time.time()}),
               "home": ({"estimate": 462148, "beds": 4.0, "baths": 3.0, "sqft": 2800,
                         "year_built": 1993}, {"error": None, "fetched": time.time()})}
    cfg = {"address": "2616 Pro Tour Dr", "city": "Belleville", "state": "IL", "zip": "62220",
           "radius": 1.5, "sold_days": 90}
    hc.render(cfg, *HOME, "https://www.redfin.com/x", None, results, TODAY)
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "⌂ $462K | font=SF Pro Display size=14"
    assert any("FOR SALE" in line and "1 new · 1 price cut" in line for line in out)
    fresh = next(line for line in out if "2 Fresh Ct" in line)
    cut = next(line for line in out if "1 Cut Ct" in line)
    assert "NEW" in fresh and "color=#34C759" in fresh
    assert "▼$10K" in cut and "color=#FF9500" in cut and "originally $300,000" in cut
    assert out.index(next(line for line in out if "2 Fresh Ct" in line)) < out.index(cut)
    assert any("Sep 30" in line for line in out)
    assert all(line.count("size=") <= 1 for line in out if "|" in line)
