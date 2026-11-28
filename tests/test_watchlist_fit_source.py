"""entry-fail-closed C2b (docs/plans/entry-fail-closed-c2b-spec.md): where the
📋 Watchlist's sector / beta fit frame comes from, the page banners, and the
structural pins for the app.py wiring no test imports.

app.py is render-only wiring; every decision tested here lives in
stock_analyzer/watchlist_fit_source.py. Every "held" test has a firing control.
"""
import ast
import math
import pathlib
import re
import sys

import pytest

from stock_analyzer import buy_check_state, sector_unpriced, watchlist_fit_source as wfs
from stock_analyzer.constants import (
    PORTFOLIO_BETA_CEILING, SECTOR_CEILING, TICKER_BETA_CRITICAL,
)
from stock_analyzer.watchlist_advisor import build_watchlist_recommendation

pytestmark = pytest.mark.fast

_ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = (_ROOT / "app.py").read_text(encoding="utf-8")

_READY = {"state": "ready", "unpriced_sectors": set(), "unpriced": {}}
_FLAT = {"state": "flat", "unpriced_sectors": None}
_UNKNOWN = {"state": "unknown", "unpriced_sectors": None}


# ═════════════════════════ decide_source ═══════════════════════════════════════
def _src(**ov):
    kw = dict(holdings_empty=False, home_frame_state="ready", home_frame_empty=False,
              home_risk_state="ready", home_risk_fresh=True, home_stale=False)
    kw.update(ov)
    return wfs.decide_source(**kw)


def test_home_only_when_every_leg_holds():
    assert _src() == wfs.SOURCE_HOME == "home"


def test_empty_holdings_is_flat_whatever_home_says():
    assert _src(holdings_empty=True) == "flat"
    assert _src(holdings_empty=True, home_frame_state="missing",
                home_risk_state="offline", home_risk_fresh=False,
                home_stale=True) == "flat"


def test_unknown_holdings_never_trust_homes_frame():
    assert _src(holdings_empty=None) == "self_build"      # control: home legs all fine


@pytest.mark.parametrize("leg", [
    dict(home_frame_state="missing"),
    dict(home_frame_state="offline"),
    dict(home_frame_empty=True),
    dict(home_risk_state="missing"),
    dict(home_risk_state="offline"),
    dict(home_risk_fresh=False),     # STALE or ABSENT at the current epoch (post-trade)
    dict(home_stale=True),
])
def test_each_failing_home_leg_alone_self_builds(leg):
    assert _src() == "home"                               # control: nothing failing
    assert _src(**leg) == "self_build"


def test_a_risk_cache_stamped_before_a_trade_self_builds_even_with_a_synced_frame():
    # `_refresh_portfolio_cache_after_trade` republishes the frame + signature and
    # bumps the epoch, so home_stale is False, both caches read "ready", but the
    # risk cache is STALE at the new epoch: weights are post-trade, beta pre-trade.
    from stock_analyzer import coord_freshness as cf
    key = "_port_risk_cache"
    for stamps, expect in (({key: 3}, "home"),        # FRESH: stamped at the epoch
                           ({key: 2}, "self_build"),  # STALE: stamped before the trade
                           ({}, "self_build"),        # ABSENT: never stamped
                           (None, "self_build")):
        fresh = cf.classify(stamps, 3, key) == cf.FRESH
        assert _src(home_risk_fresh=fresh, home_stale=False) == expect, stamps


@pytest.mark.parametrize("odd", [None, 1, 0, "yes"])
def test_odd_inputs_self_build_not_home(odd):
    assert _src(home_frame_empty=odd) == "self_build"
    assert _src(home_stale=odd) == "self_build"
    assert _src(home_risk_fresh=odd) == "self_build"


# ═════════════════════════ banner ══════════════════════════════════════════════
def _bn(source="self_build", fit=_READY, at="9:41 AM ET", n=7, priced=None, unpriced=()):
    """`unpriced` is a ticker tuple (mapped to a known sector, "Technology") or a
    {ticker: sector} dict or None (= couldn't be read), as `fit_state["unpriced"]`."""
    if isinstance(unpriced, dict):
        umap = unpriced
    elif unpriced is None:
        umap = None
    else:
        umap = {t: "Technology" for t in unpriced}
    if priced is None and isinstance(n, int) and umap is not None:
        priced = n - len(umap)
    return wfs.banner(source=source, fit_state=fit, built_at_et=at, n_holdings=n,
                      n_priced=priced, unpriced=umap)


def _texts():
    out = [_bn(source=s, fit=f, n=n, unpriced=u)
           for s in ("home", "self_build", "flat")
           for f in (_READY, _FLAT, _UNKNOWN, None, "x")
           for n in (0, 1, 7, None)
           for u in ((), ("AAA",), ("AAA", "BBB"), None)]
    out.append(("w", wfs.beta_note(fit_state=_READY, portfolio_beta=None)))
    return [t[1] for t in out if t is not None and t[1] is not None]


def test_unknown_fit_is_a_warning_that_says_held_and_never_fails_open():
    for src in ("home", "self_build", "flat"):
        tier, text = _bn(source=src, fit=_UNKNOWN, at=None, n=3)
        assert tier == "warning"
        assert "held" in text and "fails OPEN" not in text and "fail open" not in text.lower()
        assert "reason to pause, not a green light" in text


@pytest.mark.parametrize("bad", [None, "ready", 5, {}, {"state": "weird"}])
def test_unreadable_fit_state_reads_held(bad):
    tier, text = _bn(fit=bad, n=2)
    assert tier == "warning" and "held" in text


def test_self_build_all_priced_is_an_info_with_time_and_plural_holdings():
    tier, text = _bn(n=7)
    assert tier == "info"
    assert "at 9:41 AM ET" in text and "your 7 holdings" in text
    assert "refreshed every 30 minutes" in text and "not from 🏠 Home's live prices" in text
    assert "couldn't be priced" not in text


def test_self_build_ready_singular_and_missing_time_and_count():
    _t, one = _bn(n=1)
    assert "your 1 holding at" in one and "holdings" not in one
    _t, no_time = _bn(at=None, n=2)
    assert " at None" not in no_time and "on this page from your 2 holdings" in no_time
    _t, no_n = _bn(n=None, priced=None)
    assert "from your holdings" in no_n


def test_one_unpriced_holding_is_a_warning_naming_it_singular():
    tier, text = _bn(n=5, unpriced=["AAA"])
    assert tier == "warning"
    assert "from 4 of your 5 holdings" in text and "at 9:41 AM ET" in text
    assert "AAA couldn't be priced, so it is missing from your Technology weight" in text
    assert "Enter Now is held for Technology names until it can be priced" in text
    assert "reads low until a later load" not in text
    assert "they" not in text


def test_two_unpriced_holdings_are_a_warning_naming_both_plural():
    tier, text = _bn(n=5, unpriced=["AAA", "BBB"])
    assert tier == "warning"
    assert "from 3 of your 5 holdings" in text
    assert "AAA and BBB couldn't be priced, so they are missing from your Technology weight" in text
    assert "until they can be priced" in text
    assert "reads low until a later load" not in text


def test_unpriced_without_counts_still_names_them():
    tier, text = _bn(n=None, priced=None, unpriced=["AAA"])
    assert tier == "warning" and "from the holdings that could be priced" in text
    assert "AAA couldn't be priced" in text


def test_unreadable_unpriced_list_says_so_in_a_warning():
    tier, text = _bn(n=5, priced=5, unpriced=None)
    assert tier == "warning"
    assert "Whether every holding was priced couldn't be read" in text
    assert "from your 5 holdings" not in text


def test_unpriced_names_an_unknown_sector_holding_for_every_sector():
    tier, text = _bn(n=5, unpriced={"VTI": "__ALL__"})
    assert tier == "warning"
    assert "VTI couldn't be priced and its sector isn't in the app's sector map" in text
    assert "every name the sector cap covers" in text and "from 4 of your 5 holdings" in text


def test_a_partly_priced_home_frame_carries_the_page_line_not_the_self_build_text():
    tier, text = _bn(source="home", unpriced=["AAA"])
    assert tier == "warning"
    assert text == sector_unpriced.page_line({"AAA": "Technology"})
    assert "built on this page" not in text
    # control: a fully priced Home frame has no banner
    assert _bn(source="home", unpriced=()) is None


def test_unpriced_is_ignored_off_the_home_and_self_build_sources():
    assert _bn(source="flat", fit=_FLAT, n=0, unpriced=["AAA"]) is None
    assert _bn(source="flat", fit=_READY, n=0, unpriced=["AAA"]) is None


def test_the_unpriced_input_is_the_map_not_the_old_ticker_list():
    # a bare list (the retired `unpriced_tickers` shape) reads as "couldn't be read"
    tier, text = wfs.banner(source="self_build", fit_state=_READY, built_at_et=None,
                            n_holdings=3, n_priced=2, unpriced=["AAA"])
    assert tier == "warning" and "Whether every holding was priced couldn't be read" in text
    assert not hasattr(wfs, "unpriced_tickers")


def test_home_and_flat_have_no_banner_when_the_fit_is_known():
    assert _bn(source="home", at=None, n=3) is None
    assert _bn(source="flat", fit=_FLAT, at=None, n=0) is None
    assert _bn(source="self_build", fit=_FLAT, at=None, n=0) is None


def test_banner_texts_have_no_dollar_sign_and_bold_only_as_pairs():
    texts = _texts()
    assert texts
    for t in texts:
        assert "$" not in t
        assert t.count("**") % 2 == 0


# ═════════════════════════ beta_note ═══════════════════════════════════════════
@pytest.mark.parametrize("beta", [None, float("nan"), math.inf, "x"])
def test_beta_note_when_ready_and_beta_unread(beta):
    note = wfs.beta_note(fit_state=_READY, portfolio_beta=beta)
    assert note is not None and "Portfolio beta couldn't be computed" in note
    assert "$" not in note
    # true when BOTH betas are unread too (beta_leg_could_bind holds that name)
    assert "above the limit or couldn't be read is held" in note
    assert buy_check_state.beta_leg_could_bind(None, None) is True


def test_beta_note_none_when_beta_read_or_fit_not_ready():
    assert wfs.beta_note(fit_state=_READY, portfolio_beta=1.2) is None
    assert wfs.beta_note(fit_state=_READY, portfolio_beta=0.0) is None
    assert wfs.beta_note(fit_state=_UNKNOWN, portfolio_beta=None) is None
    assert wfs.beta_note(fit_state=_FLAT, portfolio_beta=None) is None
    assert wfs.beta_note(fit_state=None, portfolio_beta=None) is None


def test_the_module_is_a_registered_gate_file():
    sys.path.insert(0, str(_ROOT / ".claude" / "hooks"))
    try:
        import pre_tool_checks as p
    finally:
        sys.path.pop(0)
    assert "stock_analyzer/watchlist_fit_source.py" in p._GATE_FILES


# ═════════════════════ advisor end-to-end with the app-shaped ctx ══════════════
def _data(**ov):
    d = {
        "total": 70.0, "rec": {"label": "Buy"}, "current_price": 100.0,
        "entry_lo": 95.0, "entry_hi": 100.0, "stop": 90.0,
        "targets": {"base": 130.0}, "earnings": None,
        "risk_metrics": {"beta": 1.0},
    }
    d.update(ov)
    return d


def _pctx(fit, **ov):
    """The same keys the app's Watchlist builds per ticker (app.py `_wl_pctx`)."""
    c = {
        "sector_of_ticker": "Technology", "sector_weight_pct": 10.0,
        "sector_classified": True, "sector_label_source": "curated",
        "sector_provider_label": None, "portfolio_beta": 1.0,
        "active_high_risk_alerts": [], "grow_today_sectors": set(),
        "grow_today_sector_tickers": {}, "fit_state": fit,
    }
    c.update(ov)
    return c


def _rec(fit, tb=1.0, **ov):
    return build_watchlist_recommendation(
        "XYZ", _data(risk_metrics={"beta": tb}), portfolio_ctx=_pctx(fit, **ov))


def test_control_ready_fit_with_everything_read_is_enter_now():
    rec = _rec(_READY)
    assert rec["action"] == "ENTER_NOW"


def test_fit_unknown_holds_a_name_that_would_otherwise_enter():
    rec = _rec(_UNKNOWN)
    assert rec["action"] == "NEAR_ENTRY" and rec["suppression_kind"] == "fit_unknown"


def test_ready_with_portfolio_beta_unread_and_hot_ticker_is_beta_unknown():
    over = TICKER_BETA_CRITICAL + 0.01
    rec = _rec(_READY, tb=over, portfolio_beta=None)
    assert rec["action"] == "NEAR_ENTRY" and rec["suppression_kind"] == "beta_unknown"


def test_ready_with_portfolio_beta_unread_and_ticker_at_the_limit_passes():
    rec = _rec(_READY, tb=TICKER_BETA_CRITICAL, portfolio_beta=None)
    assert rec["action"] == "ENTER_NOW"


def test_g05_at_the_ceiling_still_binds_before_fit_unknown():
    rec = _rec(_UNKNOWN, sector_weight_pct=SECTOR_CEILING)
    assert rec["action"] == "NEAR_ENTRY" and rec["suppression_kind"] == "sector"


def test_a_real_beta_breach_still_binds_before_beta_unknown():
    rec = _rec(_READY, tb=TICKER_BETA_CRITICAL + 0.1,
               portfolio_beta=PORTFOLIO_BETA_CEILING + 0.1)
    assert rec["suppression_kind"] == "beta"


def test_flat_book_lets_enter_now_through_with_no_portfolio_beta():
    rec = _rec(_FLAT, tb=TICKER_BETA_CRITICAL + 1.0, portfolio_beta=None,
               sector_weight_pct=0.0)
    assert rec["action"] == "ENTER_NOW"


def test_fit_state_and_flat_state_come_from_buy_check_state():
    assert buy_check_state.FIT_READY == "ready" and buy_check_state.FIT_FLAT == "flat"


# ═════════════════════ app.py structural pins ══════════════════════════════════
def _wl_fit_block():
    i = APP.index("# ── Build portfolio-fit context (consumed by ENTER_NOW risk gate)")
    j = APP.index("st.warning(_wl_beta_note)", i) + len("st.warning(_wl_beta_note)")
    return APP[i:j]


def _wl_page():
    i = APP.index('elif page == "📋 Watchlist":')
    return APP[i:APP.index("# PAGE — THESIS (Chunk A", i)]


def _parse(src):
    """Parse a slice of app.py's indented page body (the `elif page == ...:` header
    line is dropped when present; the rest is already indented)."""
    if src.lstrip().startswith("elif page =="):
        src = src.split("\n", 1)[1]
    return ast.parse("if True:\n" + src)


def _calls(src, dotted):
    tree = _parse(src)
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f"{f.value.id}.{f.attr}" == dotted):
                out.append(n)
    return out


def test_the_watchlist_block_calls_the_pure_source_and_fit_state_helpers():
    blk = _wl_fit_block()
    assert len(_calls(blk, "watchlist_fit_source.decide_source")) == 1
    assert len(_calls(blk, "buy_check_state.fit_state")) == 1
    assert len(_calls(blk, "watchlist_fit_source.banner")) == 1
    assert len(_calls(blk, "watchlist_fit_source.beta_note")) == 1


def test_fit_state_is_a_key_of_the_wl_pctx_dict_literal():
    tree = _parse(_wl_page())
    found = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Assign) and len(n.targets) == 1
                and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "_wl_pctx"):
            found.append(n.value)
    assert len(found) == 1 and isinstance(found[0], ast.Dict)
    keys = [k.value for k in found[0].keys if isinstance(k, ast.Constant)]
    assert "fit_state" in keys and "portfolio_beta" in keys


def test_the_fails_open_text_and_the_old_beta_banner_are_gone_from_the_page():
    page = _wl_page()
    assert "fails OPEN" not in page
    assert "_wl_gate_blind" not in page
    assert "Portfolio beta unavailable this session" not in page
    assert "Also off: the sector ceiling check" not in page
    # the rest of the Brief-offline banner stays
    assert "Today's Brief offline" in page


def test_the_self_built_frame_never_reaches_session_state_or_a_cache_stamp():
    blk = _wl_fit_block()
    tree = _parse(blk)
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.AugAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Subscript):
                    assert not (isinstance(t.value, ast.Attribute)
                                and t.value.attr == "session_state"), ast.dump(t)
    assert "_stamp_coord" not in blk
    assert not re.search(r"session_state\.(pop|setdefault|update)\(", blk)


def test_the_self_build_passes_blank_stops_and_no_published_manual_stops():
    blk = _wl_fit_block()
    calls = _calls(blk, "build_portfolio_df") or [
        n for n in ast.walk(_parse(blk))
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "build_portfolio_df"]
    assert len(calls) == 1
    kws = {k.arg: k.value for k in calls[0].keywords}
    assert isinstance(kws["held_stops"], ast.Constant) and kws["held_stops"].value is None
    assert isinstance(kws["manual_stops"], ast.Dict) and not kws["manual_stops"].keys


def test_the_macro_calendar_reads_the_frame_actually_used():
    page = _wl_page()
    assert "build_macro_calendar(\n                        _wl_port_df," in page.replace("\r\n", "\n")
    assert "_wl_port_df = pd.DataFrame()" in _wl_fit_block()


def test_the_decide_source_call_requires_the_risk_freshness_leg_from_coord_freshness():
    call = _calls(_wl_fit_block(), "watchlist_fit_source.decide_source")[0]
    kws = {k.arg: k.value for k in call.keywords}
    assert "home_risk_fresh" in kws
    src = ast.unparse(kws["home_risk_fresh"])
    assert "coord_freshness.classify(" in src and "_port_risk_cache" in src
    assert "_coord_epoch()" in src and "coord_freshness.FRESH" in src


def test_the_banner_call_passes_the_priced_count_and_the_fit_states_unpriced_map():
    blk = _wl_fit_block()
    call = _calls(blk, "watchlist_fit_source.banner")[0]
    kws = {k.arg: k.value for k in call.keywords}
    assert {"n_holdings", "n_priced", "unpriced"} <= set(kws)
    # buy-check C5: the map comes from the one fit_state, for BOTH sources; the attrs
    # reader is gone
    assert "unpriced_tickers" not in blk
    assert '_wl_fit_unpriced = _wl_fit_state.get("unpriced")' in blk
    assert ast.unparse(kws["unpriced"]) == "_wl_fit_unpriced"
