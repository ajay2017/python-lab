"""Tests for stock_analyzer.margin — margin call-distance computations."""
import ast
import importlib
import importlib.util
from pathlib import Path

import pandas as pd
import pytest
from stock_analyzer.margin import (
    call_distance, capital_basis_weight, resolve_net_capital, held_over_capital_cap,
)
from stock_analyzer.constants import (
    FRAGILITY_PULLBACK_PCT, MARGIN_MAINTENANCE_RATE, ACCOUNT_CASH_STALE_DAYS,
    NET_CAPITAL_POSITION_CAP_PCT,
)


# ── Founding-measurement test ─────────────────────────────────────────────────

def test_real_book_matches_measured_call_distance():
    """Founding measurement: real book as of 2026-08-23.

    Measured call at -9.03% (Robinhood's exact 25.02% rate).
    Our estimate at 25.00% gives -9.12% — within 0.1pp.
    The WRONG formula (cushion / stock_value) gives -6.84%; this test
    fails against that formula, which is the point.
    """
    result = call_distance(
        stock_value=24503.0,
        owner_equity=7802.0,
        margin_debit=16701.0,
        rate=0.25,
    )
    assert result is not None
    assert abs(result["cushion"] - 1676.25) < 1.0           # $24503 × 0.25 = $6125.75; $7802 - $6125.75 = $1676.25
    assert abs(result["call_distance_pct"] - (-9.12)) < 0.1  # correct formula
    assert result["in_call"] is False
    # Confirm it does NOT match the wrong formula's answer
    wrong_formula_answer = -(result["cushion"] / 24503.0) * 100
    assert abs(result["call_distance_pct"] - wrong_formula_answer) > 2.0  # must differ by >2pp


# ── Boundary and edge cases ───────────────────────────────────────────────────

def test_exactly_at_call_threshold():
    """At the exact call threshold cushion=0, call_distance_pct=0, in_call=True."""
    rate = 0.25
    # Construct: owner_equity = stock_value * rate (exactly at floor)
    stock_value = 10000.0
    margin_debit = stock_value * (1 - rate)  # 7500
    owner_equity = stock_value - margin_debit  # 2500
    result = call_distance(stock_value, owner_equity, margin_debit, rate)
    assert result is not None
    assert abs(result["cushion"]) < 0.01
    assert abs(result["call_distance_pct"]) < 0.01
    assert result["in_call"] is True


def test_in_call_state_negative_cushion():
    """When equity is already below the maintenance floor, in_call is True."""
    result = call_distance(
        stock_value=10000.0,
        owner_equity=2000.0,   # below 25% floor of $2500
        margin_debit=8000.0,
        rate=0.25,
    )
    assert result is not None
    assert result["cushion"] < 0
    assert result["in_call"] is True
    assert result["call_distance_pct"] > 0  # already past the call (positive = already breached)


def test_no_margin_debit_returns_none():
    """No debit → not leveraged → panel should hide."""
    assert call_distance(10000.0, 10000.0, 0.0, 0.25) is None


def test_zero_debit_returns_none():
    assert call_distance(10000.0, 10000.0, 0.0, 0.25) is None


def test_negative_debit_returns_none():
    assert call_distance(10000.0, 10000.0, -100.0, 0.25) is None


def test_zero_stock_value_returns_none():
    assert call_distance(0.0, 0.0, 1000.0, 0.25) is None


def test_rate_equal_to_one_returns_none():
    """rate=1 would divide by zero — guard returns None."""
    assert call_distance(10000.0, 5000.0, 5000.0, 1.0) is None


def test_rate_above_one_returns_none():
    assert call_distance(10000.0, 5000.0, 5000.0, 1.5) is None


# ── Awareness-only invariant ──────────────────────────────────────────────────

def test_margin_maintenance_rate_not_imported_by_gate_modules():
    """MARGIN_MAINTENANCE_RATE must not be imported by any gate or advisor module.

    This guards the awareness-only invariant: the constant must only feed
    the Account-page display, never a gate, recommendation, or suppression.
    """
    gate_modules = [
        "stock_analyzer.risk_advisor",
        "stock_analyzer.exit_advisor",
        "stock_analyzer.daily_briefing",
        "stock_analyzer.scoring",
        "stock_analyzer.ranking",
        "stock_analyzer.targets",
        "stock_analyzer.watchlist_advisor",
    ]
    for mod_name in gate_modules:
        try:
            mod = importlib.import_module(mod_name)
            assert not hasattr(mod, "MARGIN_MAINTENANCE_RATE"), (
                f"{mod_name} imported MARGIN_MAINTENANCE_RATE — "
                "this constant must remain awareness-only and never feed a gate"
            )
        except ImportError:
            pass  # module doesn't exist — nothing to check


# ── Fragility constant single-sourced ────────────────────────────────────────

def test_fragility_pullback_pct_is_imported_from_constants():
    """FRAGILITY_PULLBACK_PCT must come from constants, never be a literal."""
    # The value exists and is negative (a decline)
    assert FRAGILITY_PULLBACK_PCT < 0
    # Our standard rate is 0.25
    assert MARGIN_MAINTENANCE_RATE == 0.25


# ── Sensible output ranges ────────────────────────────────────────────────────

def test_moderate_leverage_produces_reasonable_call_distance():
    """2x leverage at 25% rate should give call around -33%."""
    result = call_distance(20000.0, 10000.0, 10000.0, 0.25)
    assert result is not None
    # maintenance = $5000; cushion = $5000; call_distance = -5000/(20000*0.75)*100 = -33.3%
    assert abs(result["call_distance_pct"] - (-33.3)) < 0.5
    assert result["in_call"] is False


def test_high_leverage_produces_closer_call():
    """Higher leverage → smaller cushion → call is closer (less negative %)."""
    low_lev  = call_distance(20000.0, 10000.0, 10000.0, 0.25)  # 2x
    high_lev = call_distance(20000.0,  5000.0, 15000.0, 0.25)  # 4x
    assert low_lev is not None and high_lev is not None
    # high leverage call distance is less negative (triggers sooner)
    assert high_lev["call_distance_pct"] > low_lev["call_distance_pct"]


# ── capital_basis_weight ───────────────────────────────────────────────────────

def test_capital_basis_weight_matches_real_book():
    """Founding measurement: a $3,546 position (OXY-sized buy) against the
    real book's $7,802 owner capital is ~45.4% of capital, vs ~14.5% of the
    ~$24,503 gross book — the exact gap this function exists to surface."""
    equity_pct = 3546.0 / 24503.0 * 100
    capital_pct = capital_basis_weight(3546.0, 7802.0)
    assert capital_pct is not None
    assert abs(capital_pct - 45.45) < 0.1
    assert capital_pct > equity_pct * 3  # the ~3x understatement this closes


def test_capital_basis_weight_unlevered_matches_equity_weight():
    """When net_capital == gross market value (no leverage), the two bases
    coincide exactly — the gap only exists once leverage is introduced."""
    assert capital_basis_weight(1500.0, 10000.0) == pytest.approx(15.0)


def test_capital_basis_weight_zero_capital_returns_none():
    assert capital_basis_weight(1000.0, 0.0) is None


def test_capital_basis_weight_negative_capital_returns_none():
    """A margin-called or net-negative account has no meaningful capital
    percentage to display."""
    assert capital_basis_weight(1000.0, -500.0) is None


# ── resolve_net_capital (F-255) ────────────────────────────────────────────────

def test_resolve_net_capital_none_rec_is_unlevered():
    now = pd.Timestamp.now(tz="UTC")
    assert resolve_net_capital(24503.0, None, ACCOUNT_CASH_STALE_DAYS, now) == (None, "unlevered")


def test_resolve_net_capital_no_updated_at_is_unlevered():
    now = pd.Timestamp.now(tz="UTC")
    rec = {"cash_balance": -16701.0}  # no "updated_at" key at all
    assert resolve_net_capital(24503.0, rec, ACCOUNT_CASH_STALE_DAYS, now) == (None, "unlevered")


def test_resolve_net_capital_no_debit_is_unlevered():
    now = pd.Timestamp.now(tz="UTC")
    rec = {"cash_balance": 500.0, "updated_at": now.isoformat()}
    assert resolve_net_capital(24503.0, rec, ACCOUNT_CASH_STALE_DAYS, now) == (None, "unlevered")


def test_resolve_net_capital_stale_debit():
    now = pd.Timestamp.now(tz="UTC")
    old = now - pd.Timedelta(days=ACCOUNT_CASH_STALE_DAYS + 1)
    rec = {"cash_balance": -16701.0, "updated_at": old.isoformat()}
    assert resolve_net_capital(24503.0, rec, ACCOUNT_CASH_STALE_DAYS, now) == (None, "stale")


def test_resolve_net_capital_fresh_levered():
    """Real-book-shaped case (2026-08-23): $24,503 gross book, $16,701 debit
    -> $7,802 net capital, fresh."""
    now = pd.Timestamp.now(tz="UTC")
    rec = {"cash_balance": -16701.0, "updated_at": now.isoformat()}
    net, basis = resolve_net_capital(24503.0, rec, ACCOUNT_CASH_STALE_DAYS, now)
    assert basis == "levered"
    assert abs(net - 7802.0) < 0.01


def test_resolve_net_capital_fresh_called():
    """Debit exceeds gross book -> net capital <= 0 -> "called", and the
    non-positive number is returned (not None) so a caller can fail closed."""
    now = pd.Timestamp.now(tz="UTC")
    rec = {"cash_balance": -30000.0, "updated_at": now.isoformat()}
    net, basis = resolve_net_capital(24503.0, rec, ACCOUNT_CASH_STALE_DAYS, now)
    assert basis == "called"
    assert net is not None
    assert net <= 0


# ── held_over_capital_cap (F-255) ───────────────────────────────────────────────

def _port_df(rows):
    return pd.DataFrame(rows)


def test_held_over_capital_cap_flags_alb_oxy_shaped_row():
    """A $3,546 position (OXY-sized) against $7,802 net capital is ~45% of
    capital -- well over the 25% cap."""
    df = _port_df([{"Ticker": "OXY", "Market Value": 3546.0}])
    result = held_over_capital_cap(df, net_capital=7802.0, cap_pct=NET_CAPITAL_POSITION_CAP_PCT)
    assert result is not None
    assert len(result) == 1
    assert result[0]["ticker"] == "OXY"
    assert result[0]["capital_pct"] > NET_CAPITAL_POSITION_CAP_PCT


def test_held_over_capital_cap_does_not_flag_under_cap_row():
    df = _port_df([{"Ticker": "SPY", "Market Value": 780.0}])  # ~10% of net_capital
    result = held_over_capital_cap(df, net_capital=7802.0, cap_pct=NET_CAPITAL_POSITION_CAP_PCT)
    assert result == []


def test_held_over_capital_cap_returns_none_when_net_capital_unavailable():
    df = _port_df([{"Ticker": "OXY", "Market Value": 3546.0}])
    assert held_over_capital_cap(df, net_capital=None, cap_pct=25.0) is None
    assert held_over_capital_cap(df, net_capital=0.0, cap_pct=25.0) is None
    assert held_over_capital_cap(df, net_capital=-100.0, cap_pct=25.0) is None


def test_held_over_capital_cap_valid_net_capital_none_breach_returns_empty_list():
    """A real 'checked, none over' answer -- distinct from None ('couldn't check')."""
    df = _port_df([
        {"Ticker": "A", "Market Value": 100.0},
        {"Ticker": "B", "Market Value": 200.0},
    ])
    result = held_over_capital_cap(df, net_capital=100_000.0, cap_pct=25.0)
    assert result == []


def _module_imports_margin(mod_name: str) -> bool:
    """True if `mod_name`'s SOURCE contains an import of stock_analyzer.margin,
    in ANY form — `from stock_analyzer.margin import X`, a bare `import
    stock_analyzer.margin`, or the alias style app.py itself actually uses
    (`import stock_analyzer.margin as m`). Checked at the import-graph level
    (parsing the source) rather than `hasattr(mod, "capital_basis_weight")`
    on the already-imported module object: a hasattr check only sees names
    bound directly into the importing module's namespace, so it cannot catch
    an alias import — the module would still be imported and its functions
    still callable via the alias, just under a different name (2026-08-24
    review finding: the old hasattr version would have stayed green even if
    a gate module adopted app.py's own import style)."""
    spec = importlib.util.find_spec(mod_name)
    if spec is None or spec.origin is None:
        return False
    src = Path(spec.origin).read_text(encoding="utf-8-sig")
    tree = ast.parse(src, filename=spec.origin)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "stock_analyzer.margin":
            return True
        if isinstance(node, ast.Import) and any(
            alias.name == "stock_analyzer.margin" for alias in node.names
        ):
            return True
    return False


# The full decision-engine-core list (CLAUDE.md's _GATE_FILES minus
# constants.py/db.py/cron_runner.py/system_health.py, which are data/
# infrastructure files rather than gate/advisor logic) — the prior version
# of this test covered only 8 of these 13 (2026-08-24 review finding).
_GATE_MODULES = [
    "stock_analyzer.risk_advisor",
    "stock_analyzer.exit_advisor",
    "stock_analyzer.daily_briefing",
    "stock_analyzer.portfolio",
    "stock_analyzer.scoring",
    "stock_analyzer.valuation",
    "stock_analyzer.technicals",
    "stock_analyzer.fundamentals",
    "stock_analyzer.ranking",
    "stock_analyzer.targets",
    "stock_analyzer.risk",
    "stock_analyzer.bundle_loader",
    "stock_analyzer.watchlist_advisor",
]


@pytest.mark.parametrize("mod_name", _GATE_MODULES)
def test_margin_module_not_imported_by_any_gate_module(mod_name):
    """Same awareness-only invariant as MARGIN_MAINTENANCE_RATE, but for the
    whole stock_analyzer.margin module (covers BOTH capital_basis_weight AND
    call_distance in one check, since neither can be imported without
    importing the module itself) across the complete gate-module list."""
    assert not _module_imports_margin(mod_name), (
        f"{mod_name} imports stock_analyzer.margin — margin.py's awareness-"
        "only invariant (never a gate, never a score, never a suppression) "
        "would be broken by this import existing at all, regardless of "
        "which name or alias it's bound to"
    )
