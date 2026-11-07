"""Tests for software_versions.2h.py's pure functions.

Run:  uv run --with pytest pytest tests/test_software_versions.py
"""
import contextlib
import importlib.util
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("sv", ROOT / "software_versions.2h.py")
sv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sv)


@pytest.mark.parametrize(
    "installed, latest, latest_full, expected",
    [
        ("1.2.3", "1.2.4", "", "behind"),
        ("7.3", "7.3.0", "", "current"),
        ("1.2.3-rc1", "1.2.3", "", "behind"),
        ("1.2.3", "1.2.3-beta.4", "", "current"),
        # Two prereleases of one version are ordered by their numbers.
        ("1.2.3-rc1", "1.2.3-rc2", "", "behind"),
        ("v0.108.0-b.90", "v0.108.0-b.91", "", "behind"),
        ("v0.108.0-b.91", "v0.108.0-b.90", "", "ahead"),
        # macOS: the public version wins; build ids only break a tie.
        ("26.1 (25B78)", "26.2 beta 1", "macOS 26.2 beta 1 (25C5031e)", "behind"),
        ("26.2 (25C5031e)", "26.2 beta 2", "macOS 26.2 beta 2 (25C5042)", "behind"),
        ("26.2 (25C5042)", "26.2 beta 2", "macOS 26.2 beta 2 (25C5042)", "current"),
        ("26.3 (25D10)", "26.2 beta 5", "macOS 26.2 beta 5 (25C5055)", "ahead"),
    ],
)
def test_compare_versions(installed, latest, latest_full, expected):
    assert sv.compare_versions(installed, latest, latest_full) == expected


def test_recent_days_is_gone():
    assert "recent_days" not in Path(sv.__file__).read_text()


def test_alert_macro_tests_each_word():
    # The Jinja macro must find a version that is not at the start of its line.
    assert "t.split(' ')" in sv._FIRST_LINE_MACRO


def render_row(item, cfg=None):
    cfg = cfg or {"base_url": "https://x", "tag": "software", "summaries": False,
                  "summary_repo": {}, "notes": {}}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        sv.render([item], None, cfg, [])
    return next(line for line in buf.getvalue().splitlines() if item["name"] in line and "|" in line)


def test_unknown_row_without_version_has_one_tooltip():
    item = {
        "uuid": "u", "name": "Foo", "version": None, "versions": [], "first_line": "hello world",
        "last_changed": 0, "since": 0, "installed": None, "source": "app:Foo", "status": "unknown",
        "last_checked": 0, "last_error": False, "viewed": True, "link": "", "notes": "",
    }
    line = render_row(item)
    assert line.count("tooltip=") == 1
    assert "Installed version unknown" in line and "Snapshot starts: hello world" in line
