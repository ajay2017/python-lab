"""stock_analyzer/beta_fit_gate.py (beta gate C1, docs/plans/beta-gate-c1-spec.md).

C1 changes no behaviour: `breached` replaces the Watchlist's inline G-06
comparison, and `decide` (not yet called by any surface) must agree with the
Watchlist's `_portfolio_risk_gate` on every readable input."""
import inspect
import itertools

import pytest

from stock_analyzer import beta_fit_gate as bfg
from stock_analyzer import buy_check_state as bcs
from stock_analyzer import watchlist_advisor as wa
from stock_analyzer.constants import PORTFOLIO_BETA_CEILING as CEIL
from stock_analyzer.constants import TICKER_BETA_CRITICAL as CRIT

pytestmark = pytest.mark.fast

_READY = {"state": bcs.FIT_READY}
_FLAT = {"state": bcs.FIT_FLAT}
_UNKNOWN = {"state": bcs.FIT_UNKNOWN}

_UNREAD = [None, float("nan"), float("inf"), True, False, "n/a", object()]


# ── breached ────────────────────────────────────────────────────────────────
def test_breached_boundaries_are_strict_on_both_legs():
    assert bfg.breached(CEIL, CRIT + 1) is False
    assert bfg.breached(CEIL + 0.01, CRIT + 0.01) is True
    assert bfg.breached(CEIL + 0.1, CRIT) is False
    assert bfg.breached(CEIL, CRIT) is False


@pytest.mark.parametrize("unread", _UNREAD)
def test_breached_is_false_for_an_unread_leg(unread):
    assert bfg.breached(unread, CRIT + 1) is False
    assert bfg.breached(CEIL + 1, unread) is False
    assert bfg.breached(unread, unread) is False


def test_breached_reads_a_numeric_string_leg():
    assert bfg.breached(str(CEIL + 1), str(CRIT + 1)) is True


# ── decide ──────────────────────────────────────────────────────────────────
def _d(pb, tb, fs=_READY):
    return bfg.decide(port_beta=pb, ticker_beta=tb, fit_state=fs)


@pytest.mark.parametrize("pb,tb", [(None, None), (0.1, 0.1), (CEIL + 1, None), (None, CRIT + 1)])
def test_decide_flat_book_passes_when_nothing_is_breached(pb, tb):
    out = _d(pb, tb, _FLAT)
    assert out["state"] == bfg.STATE_PASS and out["kind"] is None


@pytest.mark.parametrize("fs", [None, {}, {"state": "unknown"}, "garbage"])
def test_decide_a_readable_breach_binds_before_any_fit_state(fs):
    # Same order as the Watchlist: a real breach blocks (and is ledgered) first,
    # whatever the fit check says; a flat book has no portfolio beta in practice.
    for f in (fs, _FLAT):
        out = _d(CEIL + 0.01, CRIT + 0.01, f)
        assert out["state"] == bfg.STATE_BLOCKED and out["kind"] == "beta"


@pytest.mark.parametrize("pb,tb", [
    (CEIL, None), (None, CRIT), (CEIL, CRIT), (CEIL + 1, CRIT), (CEIL, CRIT + 1),
])
def test_decide_passes_when_not_breached_and_nothing_could_bind(pb, tb):
    out = _d(pb, tb)
    assert out["state"] == bfg.STATE_PASS and out["kind"] is None
    assert out["gate_value"] is None and out["gate_threshold"] is None


def test_decide_blocked_on_a_breach_carries_gate_value_and_threshold():
    out = _d(CEIL + 0.01, CRIT + 0.01)
    assert out["state"] == bfg.STATE_BLOCKED and out["kind"] == "beta"
    assert out["gate_value"] == CRIT + 0.01
    assert out["gate_threshold"] == CRIT


@pytest.mark.parametrize("pb,tb", [
    (None, CRIT + 0.01), (CEIL + 0.01, None), (None, None),
])
def test_decide_unknown_when_an_unread_leg_could_bind(pb, tb):
    out = _d(pb, tb)
    assert out["state"] == bfg.STATE_UNKNOWN and out["kind"] == "beta_unknown"
    assert out["gate_value"] is None and out["gate_threshold"] is None


@pytest.mark.parametrize("fs", [None, {}, {"state": "unknown"}, "garbage", {"state": None}])
def test_decide_unknown_when_the_book_could_not_be_read(fs):
    out = _d(CEIL, CRIT, fs)   # no readable breach: the fit check decides
    assert out["state"] == bfg.STATE_UNKNOWN and out["kind"] == "fit_unknown"


def test_decide_result_fields_are_the_cleaned_floats():
    out = _d(str(CEIL + 1), float("nan"))
    assert out["port_beta"] == CEIL + 1 and isinstance(out["port_beta"], float)
    assert out["ticker_beta"] is None


def test_decide_never_raises_and_fails_closed(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(bfg, "beta_or_none", _boom)
    out = _d(CEIL + 1, CRIT + 1)
    assert out["state"] == bfg.STATE_UNKNOWN and out["kind"] == "fit_unknown"
    assert out["port_beta"] is None and out["ticker_beta"] is None
    assert bfg.breached(CEIL + 1, CRIT + 1) is False


# ── parity with the Watchlist's _portfolio_risk_gate ────────────────────────
_GRID = [None, float("nan"), CEIL - 0.1, CEIL, CEIL + 0.01, CRIT - 0.1, CRIT, CRIT + 0.01, CRIT + 1]


def _kind(gate):
    return None if gate is None else gate["kind"]


@pytest.mark.parametrize("pb,tb,fs", list(itertools.product(_GRID, _GRID, [_READY, _FLAT, _UNKNOWN])))
def test_watchlist_gate_agrees_with_decide(pb, tb, fs):
    got = _kind(wa._portfolio_risk_gate(tb, {"portfolio_beta": pb, "fit_state": fs}))
    want = _d(pb, tb, fs)
    if want["state"] == bfg.STATE_BLOCKED:
        assert got == "beta"
    elif want["state"] == bfg.STATE_UNKNOWN:
        assert got == want["kind"]
    else:
        assert got not in ("beta", "beta_unknown", "fit_unknown")


@pytest.mark.parametrize("pb,tb", list(itertools.product(_GRID, _GRID)))
def test_watchlist_gate_without_fit_state_blocks_only_on_a_breach(pb, tb):
    got = _kind(wa._portfolio_risk_gate(tb, {"portfolio_beta": pb}))
    assert (got == "beta") is bfg.breached(pb, tb)
    assert got not in ("beta_unknown", "fit_unknown")


def test_watchlist_beta_reason_formats_the_cleaned_floats():
    gate = wa._portfolio_risk_gate(str(CRIT + 1), {"portfolio_beta": str(CEIL + 1)})
    assert gate["kind"] == "beta" and gate["gate_value"] == CRIT + 1


# ── wiring and integrity pins ───────────────────────────────────────────────
def test_breached_uses_strict_comparison_on_both_constants():
    src = inspect.getsource(bfg.breached)
    assert "pb > PORTFOLIO_BETA_CEILING" in src and "tb > TICKER_BETA_CRITICAL" in src


def test_module_is_a_review_gate_file():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".claude" / "hooks"))
    import pre_tool_checks as p
    assert "stock_analyzer/beta_fit_gate.py" in p._GATE_FILES
