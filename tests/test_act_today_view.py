"""Tests for stock_analyzer.act_today_view — the single source of truth for
"how many Act Today items are there right now," shared by Home and Summary.

Two things this file guards:
1. The fail-open boundary: a crashed/not-yet-built Daily Brief must read as
   `state="offline"` / `n_active=None` (NEVER `0`) — the exact distinction
   that stops a crashed build from rendering as a false "nothing to act on".
2. The stop-breach demotion boundary (ported verbatim from 🏠 Home's
   pre-existing loop, now the single implementation every surface shares).
"""
import ast
from pathlib import Path

import pandas as pd
import pytest

from stock_analyzer.act_today_view import act_today_view

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parent.parent
APP_PY = ROOT / "app.py"
ACT_VIEW_PY = ROOT / "stock_analyzer" / "act_today_view.py"

MARGIN = 2.0  # arbitrary test margin -- act_today_view takes it as a plain
              # parameter and never hardcodes a threshold of its own.


def _pe(ticker="AAPL", gap=None, stop=100.0, has_gap_col=True):
    """Build a minimal port_df_enriched-shaped DataFrame for one ticker."""
    data = {"Ticker": [ticker], "Stop": [stop]}
    if has_gap_col:
        data["Gap to Stop (%)"] = [gap]
    return pd.DataFrame(data)


def _breach(ticker="AAPL"):
    return {"ticker": ticker, "kind": "stop_breach", "why": "stop breached"}


def _sell_signal(ticker="MSFT"):
    return {"ticker": ticker, "kind": "sell_signal", "why": "sell signal fired"}


# ── Fail-open boundary ───────────────────────────────────────────────────────

def test_offline_when_brief_offline_flag_true():
    view = act_today_view(
        daily_brief={"act_today": [], "review_list": []},
        brief_offline=True,
        port_df_enriched=None,
        live_prices=None,
        margin_pct=MARGIN,
    )
    assert view["state"] == "offline"
    assert view["n_active"] is None  # never 0 -- 0 means "checked, clean"
    assert view["active"] == []
    assert view["resolved"] == []
    assert view["aware"] == []


def test_offline_when_daily_brief_is_none():
    view = act_today_view(
        daily_brief=None,
        brief_offline=False,
        port_df_enriched=None,
        live_prices=None,
        margin_pct=MARGIN,
    )
    assert view["state"] == "offline"
    assert view["n_active"] is None


def test_clear_when_act_bucket_empty_and_not_offline():
    view = act_today_view(
        daily_brief={"act_today": [], "review_list": []},
        brief_offline=False,
        port_df_enriched=None,
        live_prices=None,
        margin_pct=MARGIN,
    )
    assert view["state"] == "clear"
    assert view["n_active"] == 0
    assert view["active"] == []


# ── Stop-breach demotion boundary ───────────────────────────────────────────

def test_recovered_breach_demotes_to_resolved():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("AAPL", gap=MARGIN + 0.01),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 0
    assert view["active"] == []
    assert len(view["resolved"]) == 1
    assert view["resolved"][0]["ticker"] == "AAPL"
    assert view["resolved"][0]["_breach_state"] == "recovered"


def test_gap_exactly_at_margin_stays_active_boundary():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("AAPL", gap=MARGIN),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []
    assert view["active"][0]["ticker"] == "AAPL"
    assert view["active"][0]["_breach_state"] == "active"


def test_gap_none_stays_active_never_demoted():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("AAPL", gap=None),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []
    assert view["active"][0]["_breach_state"] == "unavailable"


def test_gap_nan_stays_active_never_demoted():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("AAPL", gap=float("nan")),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []
    assert view["active"][0]["_breach_state"] == "unavailable"


def test_port_df_enriched_none_leaves_breach_active():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=None,
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []


def test_port_df_enriched_empty_leaves_breach_active():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=pd.DataFrame(),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []


def test_port_df_enriched_missing_gap_column_leaves_breach_active():
    view = act_today_view(
        daily_brief={"act_today": [_breach("AAPL")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("AAPL", has_gap_col=False),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []


def test_non_breach_item_never_demoted_regardless_of_gap():
    # Even a port_df_enriched row that WOULD classify as "recovered" for a
    # stop_breach must have no effect on a non-stop_breach kind.
    view = act_today_view(
        daily_brief={"act_today": [_sell_signal("MSFT")], "review_list": []},
        brief_offline=False,
        port_df_enriched=_pe("MSFT", gap=99.0),
        live_prices={},
        margin_pct=MARGIN,
    )
    assert view["n_active"] == 1
    assert view["resolved"] == []
    assert view["active"][0]["ticker"] == "MSFT"
    assert "_breach_state" not in view["active"][0]


# ── Source-scan wiring test (AST-based, same pattern as
#    tests/test_repo_hygiene.py) ────────────────────────────────────────────
#
# A Call-only scan (the first draft of this test) misses an aliased import
# (`from stock_analyzer.decision_bucket import split_defensive as _sd`,
# never called under its real name) or a bound reference carried around and
# invoked later (`_f = decision_bucket.split_defensive; ...; _f(x, y)`).
# `_split_defensive_any_reference` catches ALL of those; `_split_defensive_
# call_sites` stays narrower (Call nodes only) so act_today_view.py's own
# genuine invocation can still be positively asserted, not just "referenced."

def _split_defensive_call_sites(path: Path) -> list[str]:
    """Call nodes only -- used to prove act_today_view.py genuinely INVOKES
    split_defensive (an any-reference hit alone wouldn't prove that)."""
    src = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(src, filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None
        )
        if name == "split_defensive":
            hits.append(ast.dump(node))
    return hits


def _split_defensive_any_reference(path: Path) -> list[str]:
    """Every way `split_defensive` could be referenced in `path`'s source:
    a direct Call, a bare Name/Attribute node (covers both a real call's own
    `.func` child AND a rebound reference that is never directly called in
    this file), or an `ImportFrom` alias. Used to prove ABSENCE in app.py --
    a Call-only scan would pass on an aliased import that's never called
    under its original name, which is exactly the kind of fork this module
    exists to close."""
    src = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(src, filename=str(path))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "split_defensive":
            hits.append(ast.dump(node))
        elif isinstance(node, ast.Attribute) and node.attr == "split_defensive":
            hits.append(ast.dump(node))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "split_defensive":
                    hits.append(f"ImportFrom({alias.name} as {alias.asname or alias.name})")
    return hits


def test_app_py_has_no_split_defensive_reference_of_any_kind():
    """Every Act Today count surface must route through act_today_view() --
    a direct decision_bucket.split_defensive() call (or an aliased import, or
    a rebound reference carried around and called later) in app.py is exactly
    the fork that let Home and Summary count differently (the bug this
    module exists to close). Paired with
    test_act_today_view_module_is_the_sole_caller_of_split_defensive below:
    together the two prove "sole caller," not just each half in isolation --
    app.py has ZERO references of any kind, and act_today_view.py has a real
    Call, so nothing else in app.py's own source could be the actual wiring
    point."""
    hits = _split_defensive_any_reference(APP_PY)
    assert not hits, (
        f"app.py references decision_bucket.split_defensive "
        f"({len(hits)} site(s), including aliased imports/rebound names, not "
        f"just direct calls) -- route through "
        "stock_analyzer.act_today_view.act_today_view() instead."
    )


def test_act_today_view_module_is_the_sole_caller_of_split_defensive():
    """The other half of the pairing above -- act_today_view.py must
    genuinely INVOKE split_defensive (a Call, not merely an unused import),
    or the whole module would be dead wiring."""
    hits = _split_defensive_call_sites(ACT_VIEW_PY)
    assert hits, (
        "act_today_view.py no longer calls decision_bucket.split_defensive -- "
        "has the implementation moved elsewhere unreviewed?"
    )
