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
    assert cfg["property_types"] == [1]
    assert cfg["map"] == ""
    assert cfg["comp_days"] == 365 and cfg["sqft"] is None
    assert cfg["subdivision"] == "" and cfg["exclude_subdivisions"] == []
    assert cfg["appreciation"] == 3.0 and cfg["title"] == "comps"
    cfg = hc.load_config(str(tmp_path / "none.json"), {**env, "REDFIN_TITLE": "Redfin",
                                                       "REDFIN_APPRECIATION": "0"})
    assert cfg["title"] == "redfin" and cfg["appreciation"] == 0.0


def test_config_subdivision_lists(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"address": "1 A St", "city": "X", "state": "IL", "zip": "1",
                             "exclude_subdivisions": ["Brookhill", " Plum Hill "], "sqft": "3000"}))
    cfg = hc.load_config(str(p), {"REDFIN_SUBDIVISION": "Orchards"})
    assert cfg["exclude_subdivisions"] == ["Brookhill", "Plum Hill"]
    assert cfg["subdivision"] == "Orchards" and cfg["sqft"] == 3000
    assert hc.parse_list("Brookhill, Spyglass,") == ["Brookhill", "Spyglass"]


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
           "radius": 1.5, "sold_days": 90, "property_types": [1]}
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


def test_render_map_link_only_when_file_exists(capsys, tmp_path):
    results = {k: ([], {"error": None, "fetched": time.time()}) for k in ("active", "pending", "sold")}
    results["home"] = ({"estimate": 462148}, {"error": None, "fetched": time.time()})
    map_file = tmp_path / "my map.html"
    cfg = {"address": "1 A St", "city": "Belleville", "state": "IL", "zip": "62220",
           "radius": 1.5, "sold_days": 90, "property_types": [1], "map": str(map_file)}
    hc.render(cfg, *HOME, "https://www.redfin.com/x", None, results, TODAY)
    assert "Neighborhood map" not in capsys.readouterr().out
    map_file.write_text("<html></html>")
    hc.render(cfg, *HOME, "https://www.redfin.com/x", None, results, TODAY)
    out = capsys.readouterr().out.splitlines()
    redfin = next(i for i, line in enumerate(out) if "View on Redfin" in line)
    assert "Neighborhood map ↗" in out[redfin + 1]
    assert f"href=file://{str(map_file).replace(' ', '%20')} " in out[redfin + 1]


# --- property types ---------------------------------------------------------------------

def test_parse_property_types():
    assert hc.parse_property_types(None) == [1]
    assert hc.parse_property_types(["House", "condo"]) == [1, 2]
    assert hc.parse_property_types("townhouse, bogus") == [3]
    assert hc.parse_property_types("bogus") == [1]


def test_gis_params_types():
    assert hc.gis_params("p", "active", types=[1, 3])["uipt"] == "1,3"
    assert hc.gis_params("p", "sold")["uipt"] == "1,2,3,4,5,6,7,8"  # home-URL discovery


def test_parse_gis_property_type():
    assert all(h["ptype"] in hc.PROPERTY_TYPES.values()
               for h in hc.parse_gis(read("gis_active.json")))


# --- estimate trend ---------------------------------------------------------------------

def test_estimate_trend_needs_history():
    assert hc.estimate_trend({}, TODAY) is None
    hist = {(TODAY - timedelta(days=3)).isoformat(): 450000, TODAY.isoformat(): 455000}
    assert hc.estimate_trend(hist, TODAY) is None


def test_estimate_trend_uses_30_day_base():
    hist = {(TODAY - timedelta(days=45)).isoformat(): 400000,
            (TODAY - timedelta(days=31)).isoformat(): 440000,
            (TODAY - timedelta(days=10)).isoformat(): 450000,
            TODAY.isoformat(): 462000}
    change, pct, since = hc.estimate_trend(hist, TODAY)
    assert change == 22000 and round(pct, 1) == 5.0
    assert since == (TODAY - timedelta(days=31)).isoformat()


def test_estimate_trend_short_history_and_drop():
    hist = {(TODAY - timedelta(days=8)).isoformat(): 470000, TODAY.isoformat(): 462000}
    change, _, since = hc.estimate_trend(hist, TODAY)
    assert change == -8000 and since == (TODAY - timedelta(days=8)).isoformat()


def test_record_estimate_prunes_and_skips_none():
    state = {"estimates": {"2020-01-01": 1}}
    hc.record_estimate(state, None, TODAY)
    assert state["estimates"] == {}
    hc.record_estimate(state, 462148, TODAY)
    assert state["estimates"] == {TODAY.isoformat(): 462148}


# --- comps ------------------------------------------------------------------------------

SUBJECT = {"beds": 4.0, "baths": 3.0, "sqft": 2800, "year_built": 1993}


def test_closest_comps_prefers_similar_homes():
    sold = [home("tiny", 235000, sqft=864, ppsf=272, beds=3.0, baths=2.5, dist=0.2),
            home("big", 580000, sqft=4272, ppsf=136, beds=4.0, baths=3.0, dist=0.5),
            home("twin", 429000, sqft=2750, ppsf=156, beds=4.0, baths=3.0, year_built=1994,
                 dist=0.4),
            home("nosf", 300000, sqft=None, ppsf=None, dist=0.1)]
    comps = hc.closest_comps(sold, SUBJECT, 1.5)
    assert [h["id"] for h in comps] == ["twin"]  # tiny and big are outside 75-133% of size
    assert hc.closest_comps(sold, {"sqft": None}, 1.5) == []
    assert hc.closest_comps(sold, None, 1.5) == []


def test_closest_comps_favors_and_excludes_subdivisions():
    sold = [home("near", 400000, sqft=2800, ppsf=143, beds=4.0, baths=3.0, year_built=1993,
                 dist=0.3, subdiv="Orchard Lakes Ph 1"),
            home("orch", 420000, sqft=2600, ppsf=162, beds=4.0, baths=3.0, year_built=1993,
                 dist=0.6, subdiv="The Orchards 8th Add"),
            home("spy", 380000, sqft=2800, ppsf=136, beds=4.0, baths=3.0, year_built=1993,
                 dist=0.1, subdiv="Spyglass Vlg/Orchards"),
            home("brook", 280000, sqft=2800, ppsf=100, beds=4.0, baths=3.0, year_built=1993,
                 dist=0.1, subdiv="Brookhill Estates")]
    plain = hc.closest_comps(sold, SUBJECT, 1.5)
    assert plain.index(next(h for h in plain if h["id"] == "near")) < \
        plain.index(next(h for h in plain if h["id"] == "orch"))
    comps = hc.closest_comps(sold, SUBJECT, 1.5, "Orchards", ["brookhill", "SPYGLASS"])
    assert [h["id"] for h in comps] == ["orch", "near"]  # Orchard Lakes isn't "Orchards"


def test_parse_parcel_subdiv():
    assert hc.parse_parcel_subdiv('{"features":[{"attributes":{"SUBDIV":" MAPLE HILL "}}]}') \
        == "MAPLE HILL"
    assert hc.parse_parcel_subdiv('{"features":[]}') == ""


def test_fill_subdivisions_uses_county_for_missing_and_caches(monkeypatch):
    calls = []

    def fake(lat, lon):
        calls.append((lat, lon))
        return "SPYGLASS VILLAGE AT THE ORCHARDS"
    monkeypatch.setattr(hc, "fetch_parcel_subdiv", fake)
    homes = [home("a", 1, subdiv=None), home("b", 1, subdiv="Belleville"),
             home("c", 1, subdiv="Brookhill Estates")]
    hc.fill_subdivisions(homes, "Belleville")
    assert [h["subdiv"] for h in homes] == ["SPYGLASS VILLAGE AT THE ORCHARDS"] * 2 + \
        ["Brookhill Estates"]
    again = [home("a", 1, subdiv=None)]
    hc.fill_subdivisions(again, "Belleville")
    assert len(calls) == 2 and again[0]["subdiv"].startswith("SPYGLASS")

    def boom(lat, lon):
        raise hc.FetchError("down")
    monkeypatch.setattr(hc, "fetch_parcel_subdiv", boom)
    fresh = [home("z", 1, subdiv=None)]
    hc.fill_subdivisions(fresh, "Belleville")
    assert fresh[0]["subdiv"] is None and "z" not in hc.read_json(hc.SUBDIV_FILE)


def test_render_comps_window_submenu_and_sqft_override(capsys):
    old = home("old", 430000, sqft=2900, ppsf=148, beds=4.0, baths=3.0, ptype=1, status="Closed",
               sold_date="2026-03-01", address="1 Old Ct", subdiv="The Orchards")
    recent = home("new", 300000, sqft=2900, ppsf=103, beds=4.0, baths=3.0, ptype=1,
                  status="Closed", sold_date="2026-09-01", address="2 New Ct",
                  subdiv="Brookhill Estates")
    ok = {"error": None, "fetched": 1}
    results = {"active": ([], ok), "pending": ([], ok), "sold": ([recent], ok),
               "comps": ([old, recent], ok), "home": ({**SUBJECT, "estimate": 462000}, ok)}
    cfg = {"address": "2616 Pro Tour Dr", "city": "Belleville", "state": "IL", "zip": "62220",
           "radius": 1.5, "sold_days": 90, "property_types": [1], "comp_days": 365,
           "sqft": 3000, "subdivision": "Orchards", "exclude_subdivisions": ["Brookhill"]}
    hc.render(cfg, *HOME, "https://www.redfin.com/x", None, results, TODAY)
    out = capsys.readouterr().out.splitlines()
    assert any("Comps value  ≈ $435K  ($145/sf · 1 closest sale)" in line for line in out)
    sub = [line for line in out if line.startswith("--1 Old Ct")]
    assert sub and "The Orchards" in sub[0] and "Mar 1 '26" in sub[0] and "href=https://x/old" in sub[0]
    assert any("3,000sf (Redfin 2,800)" in line for line in out)
    assert not any(line.startswith("--2 New Ct") for line in out)
    assert not any("★" in line for line in out if "2 New Ct" in line)  # excluded, not a comp


def _title_render(capsys, **cfg_kw):
    comp = home("c", 440000, sqft=2800, ppsf=157, beds=4.0, baths=3.0, ptype=1, status="Closed",
                sold_date=TODAY.isoformat(), address="1 Comp Ct")
    ok = {"error": None, "fetched": 1}
    results = {"active": ([], ok), "pending": ([], ok), "sold": ([comp], ok),
               "home": ({**SUBJECT, "estimate": 462000}, ok)}
    cfg = {"address": "1 A St", "city": "Belleville", "state": "IL", "zip": "62220",
           "radius": 1.5, "sold_days": 90, "property_types": [1], **cfg_kw}
    hc.render(cfg, *HOME, "https://www.redfin.com/x", None, results, TODAY)
    return capsys.readouterr().out.splitlines()


def test_title_shows_comps_value_first_by_default(capsys):
    out = _title_render(capsys)
    assert out[0].startswith("⌂ $440K")
    comps_i = next(i for i, line in enumerate(out) if "Comps value" in line)
    redfin_i = next(i for i, line in enumerate(out) if "Redfin Estimate" in line)
    assert comps_i < redfin_i and "color=#34C759 size=13" in out[comps_i]
    assert "size=12" in out[redfin_i]


def test_title_redfin_setting_and_fallback(capsys):
    out = _title_render(capsys, title="redfin")
    assert out[0].startswith("⌂ $462K")
    assert out.index(next(line for line in out if "Redfin Estimate" in line)) < \
        out.index(next(line for line in out if "Comps value" in line))
    out = _title_render(capsys, sqft=9000)  # no comps within the size range -> Redfin
    assert out[0].startswith("⌂ $462K") and not any("Comps value" in line for line in out)


def test_comps_value_size_adjusts_prices():
    comps = [home("a", 440000, sqft=2800, beds=4.0), home("b", 425000, sqft=2676, beds=4.0),
             home("c", 465000, sqft=3330, beds=4.0)]
    # adjusted: 440,000 · 431,200 · 438,500 -> median 438,500
    assert hc.comps_value(comps, SUBJECT) == (438000, 157, 3, 0)  # round() is half-to-even
    assert hc.comps_value(comps, {**SUBJECT, "sqft": 3000}) == (448000, 150, 3, 0)
    assert hc.comps_value([], SUBJECT) is None


def test_comps_value_time_adjusts_for_appreciation():
    sold = home("a", 400000, sqft=2800, beds=4.0, sold_date=(TODAY - timedelta(days=365)).isoformat())
    assert hc.time_adjusted(sold, TODAY, 3.5) == pytest.approx(414000)
    assert hc.time_adjusted({**sold, "sold_date": None}, TODAY, 3.5) == 400000
    assert hc.time_adjusted(sold, TODAY, 0) == 400000
    assert hc.comps_value([sold], SUBJECT, TODAY, 3.5) == (414000, 148, 1, 0)
    assert hc.comps_value([sold], SUBJECT) == (400000, 143, 1, 0)


def test_comps_value_adjusts_for_bedrooms():
    comps = [home(str(i), 448000, sqft=2800, beds=4.0) for i in range(3)]
    assert hc.comps_value(comps, {**SUBJECT, "beds": 5.0}) == (458000, 160, 3, 10000)
    assert hc.comps_value(comps, {**SUBJECT, "beds": None}) == (448000, 160, 3, 0)


# --- market pace ------------------------------------------------------------------------

def test_pace_line():
    active = [home("a", 1, dom=10), home("b", 1, dom=30), home("c", 1, dom=None)]
    assert hc.pace_line(active, [home("p", 1)]) == \
        "Median 20d on market  ·  1 of 4 under contract"
    assert hc.pace_line([], []) is None


def test_sale_history_and_sold_pace():
    state = {"listings": {
        "s1": {"price": 400000, "first_seen": "2026-08-01"},
        "s2": {"price": 300000, "first_seen": "2026-09-01"},
        "relist": {"price": 100000, "first_seen": "2026-09-01"}}}
    sold = [home("s1", 392000, sold_date="2026-09-10"),
            home("s2", 300000, sold_date="2026-09-21"),
            home("relist", 400000, sold_date="2026-09-21"),
            home("untracked", 350000, sold_date="2026-09-21")]
    assert hc.sale_history(sold[0], state) == (400000, 40)
    assert hc.sale_history(sold[2], state) is None
    assert hc.sold_pace_line(sold, state) == "Sold at 99% of list  ·  ~30d to sell  (2 of 4 tracked)"
    assert hc.sold_pace_line(sold[3:], state) is None


def test_render_filters_types_stars_comps_and_trend(capsys):
    hc.write_json(hc.STATE_FILE, {"estimates": {(TODAY - timedelta(days=30)).isoformat(): 450000}})
    land = home("lot", 45000, sqft=None, ppsf=None, beds=0.0, ptype=5, address="9 Lot Ct")
    sold = [home("twin", 429000, sqft=2750, ppsf=156, beds=4.0, baths=3.0, ptype=1,
                 status="Closed", sold_date="2026-09-01", address="1 Twin Ct"),
            home("tiny", 235000, sqft=864, ppsf=272, ptype=1, status="Closed",
                 sold_date="2026-10-01", address="2 Tiny Ct")]
    results = {"active": ([land, home("h", 400000, dom=5, ptype=1)], {"error": None, "fetched": 1}),
               "pending": ([], {"error": None, "fetched": 1}),
               "sold": (sold, {"error": None, "fetched": 1}),
               "home": ({**SUBJECT, "estimate": 462000}, {"error": None, "fetched": 1})}
    cfg = {"address": "2616 Pro Tour Dr", "city": "Belleville", "state": "IL", "zip": "62220",
           "radius": 1.5, "sold_days": 90, "property_types": [1]}
    hc.render(cfg, *HOME, "https://www.redfin.com/x", None, results, TODAY)
    out = capsys.readouterr().out.splitlines()
    assert not any("9 Lot Ct" in line for line in out)
    assert any(line.startswith("     ▲ $12,000  (+2.7%)  since 9/14") for line in out)
    assert any("Comps value  ≈" in line for line in out)
    rows = [line for line in out if "href=https://x/" in line and line.startswith("     ")]
    sold_rows = [line for line in rows if "Twin Ct" in line or "Tiny Ct" in line]
    assert "1 Twin Ct" in sold_rows[0] and "★" in sold_rows[0] and "★" not in sold_rows[1]
    assert any("$154/sf · 1 closest sale)" in line for line in out)  # 429,000 + 50sf × $50
    assert hc.read_json(hc.STATE_FILE)["estimates"][TODAY.isoformat()] == 462000
