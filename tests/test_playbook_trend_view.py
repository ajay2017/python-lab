"""PlaybookVsTrend (docs/bug-register.md): the Sector Rotation Playbook beside each
sector's actual trend. Pure module `stock_analyzer.playbook_trend_view` plus an AST pin
that app.py's Playbook block calls it and decides nothing itself. Display only: the
regime map, the regime read and `portfolio_macro_exposure` are pinned unchanged."""
from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from stock_analyzer import playbook_trend_view as pv
from stock_analyzer.macro import RATE_SENSITIVITY, REGIME_FAVORED
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

_APP = Path(__file__).parent.parent / "app.py"


def _trow(symbol, name, sectors, label, vs):
    return {"symbol": symbol, "name": name, "app_sectors": sectors, "label": label,
            "vs_spx_pts": vs}


def _result(*sector_rows):
    return {"as_of": date(2026, 11, 23), "indices": [], "sectors": list(sector_rows),
            "not_checked": []}


def _one(favored_key, sector, label, vs=-5.0):
    """rows() for one sector on the favoured ('overweight') or lags ('underweight') side."""
    from stock_analyzer.portfolio import SECTOR_ETF
    fund = SECTOR_ETF[sector]
    favored = {"overweight": [sector] if favored_key == "favoured" else [],
               "underweight": [sector] if favored_key == "lags" else []}
    res = _result(_trow(fund, sector, [sector], label, vs))
    return pv.rows(favored, res, None)[0]


# ── the clash rule ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("label,expected", [
    ("falling", True), ("weakening", False), ("steady", False),
    ("rising", False), (None, False),
])
def test_favoured_clashes_only_on_falling(label, expected):
    assert _one("favoured", "Energy", label)["clash"] is expected


@pytest.mark.parametrize("label,expected", [
    ("rising", True), ("weakening", False), ("steady", False),
    ("falling", False), (None, False),
])
def test_lags_clashes_only_on_rising(label, expected):
    assert _one("lags", "Semiconductors", label)["clash"] is expected


def test_unknown_label_string_is_not_checked_and_never_clashes():
    r = _one("favoured", "Energy", "garbage")
    assert r["label"] is None and r["clash"] is False


def test_is_clash_direct():
    assert pv.is_clash("favoured", "falling")
    assert pv.is_clash("lags", "rising")
    assert not pv.is_clash("favoured", "rising")
    assert not pv.is_clash("lags", "falling")
    assert not pv.is_clash("favoured", None)
    assert not pv.is_clash("lags", None)


# ── rows ─────────────────────────────────────────────────────────────────────

def test_rows_order_is_overweight_then_underweight_in_map_order():
    fav = REGIME_FAVORED["risk_off"]
    r = pv.rows(fav, None, None)
    assert [x["sector"] for x in r] == fav["overweight"] + fav["underweight"]
    assert [x["side"] for x in r] == (
        ["favoured"] * len(fav["overweight"]) + ["lags"] * len(fav["underweight"]))


def test_no_trend_result_means_nothing_checked_and_nothing_flagged():
    for key in ("risk_off", "rising_rates", "risk_on", "falling_rates"):
        r = pv.rows(REGIME_FAVORED[key], None, {"IGV": {"pct": 30.0}})
        assert r and all(x["label"] is None and x["vs_spx_pts"] is None and not x["clash"]
                         for x in r)


def test_a_fund_missing_from_the_trend_result_reads_not_checked():
    r = pv.rows({"overweight": ["Energy"], "underweight": []},
                _result(_trow("XLV", "Healthcare", ["Healthcare"], "rising", 1.0)), None)
    assert r[0]["fund"] == "XLE" and r[0]["label"] is None and r[0]["vs_spx_pts"] is None


def test_igv_sectors_share_the_fund_and_display_name():
    res = _result(_trow("IGV", "Software", ["AI & Cloud", "AI & Data", "Enterprise Tech"],
                        "falling", -8.8))
    r = pv.rows(REGIME_FAVORED["risk_off"], res, {"IGV": {"pct": 33.0}})
    igv = [x for x in r if x["sector"] in ("AI & Cloud", "AI & Data")]
    assert len(igv) == 2
    assert {x["fund"] for x in igv} == {"IGV"}
    assert {x["fund_display"] for x in igv} == {"Software, IGV"}
    assert all(x["weight_pct"] == 33.0 and x["label"] == "falling" for x in igv)


def test_single_sector_fund_display_is_just_the_symbol():
    r = _one("favoured", "Energy", "steady")
    assert r["fund"] == "XLE" and r["fund_display"] == "XLE"


def test_weight_is_none_when_not_held_unknown_or_zero():
    res = _result(_trow("XLE", "Energy", ["Energy"], "steady", 0.0))
    fav = {"overweight": ["Energy"], "underweight": []}
    assert pv.rows(fav, res, None)[0]["weight_pct"] is None
    assert pv.rows(fav, res, {})[0]["weight_pct"] is None
    assert pv.rows(fav, res, {"XLV": {"pct": 10.0}})[0]["weight_pct"] is None
    assert pv.rows(fav, res, {"XLE": {"pct": 0.0}})[0]["weight_pct"] is None
    assert pv.rows(fav, res, {"XLE": {"pct": 12.5}})[0]["weight_pct"] == 12.5


def test_nan_and_garbage_gap_become_none_not_zero():
    for bad in (float("nan"), float("inf"), "x", None):
        res = _result(_trow("XLE", "Energy", ["Energy"], "falling", bad))
        r = pv.rows({"overweight": ["Energy"], "underweight": []}, res, None)[0]
        assert r["vs_spx_pts"] is None


def test_rate_sensitivity_carried_only_when_given():
    fav = {"overweight": ["Energy"], "underweight": []}
    assert pv.rows(fav, None, None)[0]["rate_sensitivity"] is None
    assert pv.rows(fav, None, None, RATE_SENSITIVITY)[0]["rate_sensitivity"] == RATE_SENSITIVITY["Energy"]
    assert pv.rows(fav, None, None, {})[0]["rate_sensitivity"] is None


def test_neutral_regime_has_no_rows():
    assert pv.rows(REGIME_FAVORED["neutral"], _result(), None) == []


def test_rows_do_not_mutate_inputs():
    fav = {"overweight": ["Energy"], "underweight": ["Semiconductors"], "reason": "r"}
    snap = {k: (list(v) if isinstance(v, list) else v) for k, v in fav.items()}
    pv.rows(fav, None, None)
    assert fav == snap


# ── chart points ─────────────────────────────────────────────────────────────

def _risk_off_rows():
    res = _result(
        _trow("XLV", "Healthcare", ["Healthcare"], "rising", 3.0),
        _trow("XLE", "Energy", ["Energy"], "falling", -8.3),
        _trow("SOXX", "Semiconductors", ["Semiconductors"], "falling", -8.8),
        _trow("IGV", "Software", ["AI & Cloud", "AI & Data", "Enterprise Tech"], "falling", -8.8),
        _trow("XLY", "Consumer Tech", ["Consumer Tech"], "rising", 2.0),
    )  # ITA (Defense) deliberately absent: not checked
    w = {"IGV": {"pct": 33.0}, "XLY": {"pct": 35.0}}
    return pv.rows(REGIME_FAVORED["risk_off"], res, w)


def test_chart_merges_igv_sectors_into_one_dot():
    pts, _ = pv.chart_points(_risk_off_rows())
    igv = [p for p in pts if p["fund"] == "IGV"]
    assert len(igv) == 1
    assert igv[0]["sectors"] == ["AI & Cloud", "AI & Data"]
    assert igv[0]["side"] == "lags"
    assert "AI & Cloud / AI & Data" in pv.point_label(igv[0])
    assert "you 33%" in pv.point_label(igv[0])


def test_chart_leaves_off_rows_without_a_gap_and_names_them():
    pts, left_out = pv.chart_points(_risk_off_rows())
    assert left_out == ["Defense"]
    assert "ITA" not in {p["fund"] for p in pts}
    assert len(pts) == 5  # Healthcare, Energy, SOXX, IGV (two sectors merged), XLY


def test_chart_with_no_trend_has_no_points_and_names_every_sector():
    rows_ = pv.rows(REGIME_FAVORED["risk_off"], None, None)
    pts, left_out = pv.chart_points(rows_)
    assert pts == []
    assert left_out == [r["sector"] for r in rows_]


def test_chart_x_sides_and_deterministic_spread():
    rows_ = _risk_off_rows()
    a, _ = pv.chart_points(rows_)
    b, _ = pv.chart_points(list(rows_))
    assert [(p["fund"], p["x"]) for p in a] == [(p["fund"], p["x"]) for p in b]
    fav_x = [p["x"] for p in a if p["side"] == "favoured"]
    lag_x = [p["x"] for p in a if p["side"] == "lags"]
    assert all(0 < x < 1 for x in fav_x) and all(-1 < x < 0 for x in lag_x)
    assert len(set(fav_x)) == len(fav_x) and len(set(lag_x)) == len(lag_x)


def test_chart_point_carries_clash_label_and_gap():
    pts, _ = pv.chart_points(_risk_off_rows())
    by = {p["fund"]: p for p in pts}
    assert by["XLE"]["clash"] is True and by["XLE"]["label"] == "falling" and by["XLE"]["y"] == -8.3
    assert by["XLY"]["clash"] is True and by["XLY"]["weight_pct"] == 35.0
    assert by["XLV"]["clash"] is False
    assert by["SOXX"]["clash"] is False


def test_zero_gap_is_charted_not_left_out():
    res = _result(_trow("XLE", "Energy", ["Energy"], "steady", 0.0))
    pts, left_out = pv.chart_points(pv.rows({"overweight": ["Energy"], "underweight": []}, res, None))
    assert len(pts) == 1 and left_out == []


def test_y_bound_and_dot_size():
    pts, _ = pv.chart_points(_risk_off_rows())
    assert pv.y_bound(pts) >= max(abs(p["y"]) for p in pts)
    assert pv.y_bound([]) > 0
    assert pv.y_bound([{"y": 0.0}]) > 0
    assert pv.dot_size(None) == pv.dot_size(0) == pv.dot_size(-3) == pv.dot_size(float("nan"))
    assert pv.dot_size(35.0) > pv.dot_size(5.0) > pv.dot_size(None)
    assert pv.dot_size(10_000.0) <= 100  # capped


# ── text helpers ─────────────────────────────────────────────────────────────

def test_chip_text_every_label():
    assert pv.chip_text("rising") == "↗ Rising"
    assert pv.chip_text("steady") == "→ Steady"
    assert pv.chip_text("weakening") == "↘ Weakening"
    assert pv.chip_text("falling") == "⇊ Falling"
    assert pv.chip_text(None) == "trend not checked"
    assert pv.chip_text("garbage") == "trend not checked"


def test_pts_text():
    assert pv.pts_text(-8.3) == "−8.3 pts"
    assert pv.pts_text(4.24) == "+4.2 pts"
    assert pv.pts_text(0.0) == "0.0 pts"
    assert pv.pts_text(-0.04) == "0.0 pts"
    assert pv.pts_text(None) == "n/a"
    assert pv.pts_text(float("nan")) == "n/a"


def test_weight_text():
    assert pv.weight_text(35.4) == "35%"
    assert pv.weight_text(0.3) == "<1%"
    assert pv.weight_text(None) is None
    assert pv.weight_text(0) is None


def test_caption_strings():
    c = pv.caption("Mon Nov 23")
    assert c == (
        "A fixed map of what tends to do well or badly in this kind of market, not a read "
        "of current prices. Each sector's actual trend (📈 Market and sector trend, as of "
        "the Mon Nov 23 close) is shown beside it; where the two disagree, the trend is "
        "what has actually happened.")
    assert "couldn't be read" in pv.caption(None) and "as of" not in pv.caption(None)
    assert pv.HEADING == "Sector Rotation Playbook: what usually leads in this regime"
    assert pv.chart_title("Mon Nov 23") == (
        "Playbook vs what actually happened (4 weeks to the Mon Nov 23 close)")
    assert pv.NO_TREND_CAPTION == "Trend not checked, so the map can't be compared with what happened."
    assert pv.ALIGNMENT_CAPTION == "Tailwind / headwind = the regime map above, not each sector's trend."
    assert pv.CLASH_TAG == "⚠ trend disagrees"
    assert pv.Y_AXIS_TITLE == "4-week vs S&P 500 (pts)"
    assert pv.left_out_caption(["Defense", "Energy"]).endswith("Defense, Energy.")
    assert pv.none_flagged_text("favoured") == "🟢 Usually favoured: none flagged"
    assert pv.none_flagged_text("lags") == "🔴 Usually lags: none flagged"
    assert pv.ZONE_TEXT == {
        "top_left": "map says lags · beating the S&P 500",
        "top_right": "map says favoured · beating the S&P 500",
        "bottom_left": "map says lags · trailing the S&P 500",
        "bottom_right": "map says favoured · trailing the S&P 500",
    }
    assert not any(g in t for t in pv.ZONE_TEXT.values() for g in ("⚠", "✓"))
    assert ("Zones show only whether the fund beat or trailed the S&P 500 over the 4 weeks; "
            "the amber ring is the trend-label check.") in pv.CHART_LEGEND_CAPTION
    assert ("your share of your holdings' value in that fund's sector(s), at 🏠 Home's last "
            "build (small = not held, or your weight couldn't be read)") in pv.CHART_LEGEND_CAPTION


def test_hover_text_has_every_field_and_escapes():
    pts, _ = pv.chart_points(_risk_off_rows())
    igv = next(p for p in pts if p["fund"] == "IGV")
    h = pv.hover_text(igv)
    assert "AI &amp; Cloud / AI &amp; Data" in h
    assert "Software, IGV" in h and "Falling" in h and "−8.8 pts" in h and "33%" in h
    xle = next(p for p in pts if p["fund"] == "XLE")
    assert "not held" in pv.hover_text(xle)


def test_rate_tip():
    assert pv.rate_tip(0.25) == "Rate sensitivity +0.25"
    assert pv.rate_tip(-0.65) == "Rate sensitivity -0.65"
    assert pv.rate_tip(None) is None


# ── the cards ────────────────────────────────────────────────────────────────

def test_cards_split_by_side_and_flag_clashes():
    rows_ = _risk_off_rows()
    fav_html = pv.cards_html(rows_, "favoured")
    lag_html = pv.cards_html(rows_, "lags")
    assert "🟢 Usually favoured" in fav_html and "🔴 Usually lags" in lag_html
    assert "Energy" in fav_html and "Semiconductors" not in fav_html
    assert "Consumer Tech" in lag_html and "Healthcare" not in lag_html
    # two clashes in this fixture: Energy (favoured + Falling), Consumer Tech (lags + Rising)
    assert fav_html.count("⚠ trend disagrees") == 1
    assert lag_html.count("⚠ trend disagrees") == 1
    assert "⇊ Falling" in fav_html and "trend not checked" in fav_html  # Defense not checked
    assert "Software, IGV" in lag_html
    assert "**" not in fav_html + lag_html


def test_cards_unavailable_trend_flags_nothing():
    rows_ = pv.rows(REGIME_FAVORED["risk_off"], None, None, RATE_SENSITIVITY)
    for side in ("favoured", "lags"):
        h = pv.cards_html(rows_, side)
        assert "⚠" not in h
        assert h.count("trend not checked") == sum(1 for r in rows_ if r["side"] == side)


def test_cards_rate_sensitivity_is_in_the_hover_tip_not_inline():
    rows_ = pv.rows({"overweight": ["Energy"], "underweight": []}, None, None, RATE_SENSITIVITY)
    h = pv.cards_html(rows_, "favoured")
    assert 'title="Rate sensitivity +0.25"' in h
    assert h.count("Rate sensitivity") == 1  # only in the title attribute


def test_empty_side_reads_none_flagged_and_neutral_regime_has_no_cards():
    rows_ = pv.rows(REGIME_FAVORED["falling_rates"], None, None)
    assert "none flagged" not in pv.cards_html(rows_, "favoured")
    rows_ = pv.rows({"overweight": ["Energy"], "underweight": []}, None, None)
    assert "🔴 Usually lags: none flagged" in pv.cards_html(rows_, "lags")
    neutral = pv.rows(REGIME_FAVORED["neutral"], None, None)
    assert "🟢 Usually favoured: none flagged" in pv.cards_html(neutral, "favoured")
    assert "🔴 Usually lags: none flagged" in pv.cards_html(neutral, "lags")


def test_cards_escape_every_interpolated_value():
    evil = '<script>alert(1)</script>"&'
    rows_ = [{"sector": evil, "side": "favoured", "fund": "XLE", "fund_display": evil,
              "label": "falling", "vs_spx_pts": -1.0, "weight_pct": None, "clash": True,
              "rate_sensitivity": 0.25}]
    h = pv.cards_html(rows_, "favoured")
    assert "<script>" not in h and "&lt;script&gt;" in h
    hv = pv.hover_text({"sectors": [evil], "fund_display": evil, "label": "falling", "y": -1.0,
                        "weight_pct": None})
    assert "<script>" not in hv


# ── the map and the regime read are untouched ───────────────────────────────

def test_map_still_has_the_five_regimes_with_both_lists():
    assert set(REGIME_FAVORED) == {"rising_rates", "falling_rates", "risk_off", "risk_on",
                                   "neutral"}
    for entry in REGIME_FAVORED.values():
        assert "overweight" in entry and "underweight" in entry and "reason" in entry


def test_module_compares_no_constants_value():
    """No threshold lives in this module: it imports nothing from constants."""
    tree = ast.parse(Path(pv.__file__).read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            assert n.module != "stock_analyzer.constants"


# ── app.py wiring (AST; app.py is never imported) ───────────────────────────

_TREE = parse_file_cached(_APP)


def _module_func(name: str) -> ast.FunctionDef:
    for n in _TREE.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} is not a module-level function in app.py")


def test_playbook_renderer_calls_the_module_and_decides_nothing():
    fn = _module_func("_render_playbook_vs_trend")
    src_calls = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            src_calls.add(n.func.attr)
    for needed in ("rows", "chart_points", "cards_html", "hover_text", "y_bound", "caption"):
        assert needed in src_calls, needed
    # no clash decision of its own: no comparison against a trend label string, and no
    # is_clash re-derivation
    labels = {"falling", "weakening", "rising", "steady"}
    for n in ast.walk(fn):
        if isinstance(n, ast.Compare):
            for c in [n.left, *n.comparators]:
                assert not (isinstance(c, ast.Constant) and c.value in labels), ast.dump(n)
    assert "clash" not in {a.attr for a in ast.walk(fn) if isinstance(a, ast.Attribute)}
    # the cards go through st.html (the module escapes), never a dynamic unsafe_allow_html
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            assert not any(k.arg == "unsafe_allow_html" for k in n.keywords)


def test_playbook_renderer_does_not_fetch():
    fn = _module_func("_render_playbook_vs_trend")
    names = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            f = n.func
            names.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    assert not names & {"_cached_market_trend", "_trend_result_ok", "download", "_retry"}


def test_macro_signals_tab_uses_the_same_cached_trend_and_the_renderer():
    src = _APP.read_text(encoding="utf-8")
    start = src.index('elif page == "🌐 Macro":')
    block = src[start:start + 40_000]
    # one fetch (the 📈 tab's); the 📡 tab reuses its result and weights, never a second call
    assert block.count("_cached_market_trend(") == 1
    assert block.count("market_trend.held_weights(") == 1
    assert "_render_playbook_vs_trend(_fav, _trend_res, _trend_w)" in block
    assert "playbook_trend_view.ALIGNMENT_CAPTION" in block
    # the old directive headings are gone from the Macro block
    assert "Overweight**" not in block and "Underweight / Reduce" not in block
    assert "Sector Rotation Playbook for this Regime" not in block


# ── unknown weights, merged-dot fund label ───────────────────────────────────

def test_rows_tell_unknown_weights_from_not_held():
    res = _result(_trow("XLE", "Energy", ["Energy"], "steady", 1.0))
    fav = {"overweight": ["Energy"], "underweight": []}
    assert pv.rows(fav, res, None)[0]["weight_known"] is False      # book unreadable
    assert pv.rows(fav, res, {})[0]["weight_known"] is True         # readable, fund absent
    assert pv.rows(fav, res, {"XLE": {"pct": 5.0}})[0]["weight_known"] is True
    assert pv.rows(fav, None, None)[0]["weight_known"] is False


def test_hover_weight_unknown_vs_not_held_vs_held():
    res = _result(_trow("XLE", "Energy", ["Energy"], "steady", 1.0))
    fav = {"overweight": ["Energy"], "underweight": []}
    def hover(weights):
        pts, _ = pv.chart_points(pv.rows(fav, res, weights))
        return pv.hover_text(pts[0])
    assert "Your weight: unknown (open 🏠 Home first)" in hover(None)
    assert "Your weight: not held" in hover({})
    assert "Your weight: 5%" in hover({"XLE": {"pct": 5.0}})
    pts, _ = pv.chart_points(pv.rows(fav, res, None))
    assert pv.dot_size(pts[0]["weight_pct"]) == pv.dot_size(None)   # default small dot


def test_point_label_names_the_fund_only_when_it_covers_more_sectors():
    pts, _ = pv.chart_points(_risk_off_rows())
    by = {p["fund"]: p for p in pts}
    assert pv.point_label(by["IGV"]) == "AI & Cloud / AI & Data (Software, IGV) −8.8 · you 33%"
    assert pv.point_label(by["XLE"]) == "Energy −8.3"
    assert pv.point_label(by["XLY"]) == "Consumer Tech +2.0 · you 35%"
    # one IGV sector on the map (rising rates): the fund is still named
    res = _result(_trow("IGV", "Software", ["AI & Cloud", "AI & Data", "Enterprise Tech"],
                        "falling", -4.0))
    pts, _ = pv.chart_points(pv.rows({"overweight": [], "underweight": ["AI & Cloud"]}, res, None))
    assert pv.point_label(pts[0]) == "AI & Cloud (Software, IGV) −4.0"
    # a fund with no label still gets the fund in brackets
    res2 = _result(_trow("IGV", "Software", ["AI & Cloud", "AI & Data", "Enterprise Tech"], None, -4.0))
    pts, _ = pv.chart_points(pv.rows(REGIME_FAVORED["risk_off"], res2, None))
    assert any(pv.point_label(p).startswith("AI & Cloud / AI & Data (Software, IGV)") for p in pts)
