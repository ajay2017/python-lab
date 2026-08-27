"""Tests for Phase 1 sizing coherence (F-249).

Pins the invariants established by retiring _suggest_size() and unifying on
ceiling-capped position_sizing() for both the new-pick and add-winner paths:

1. port_pct NEVER exceeds SINGLE_NAME_CEILING — the exact boundary _suggest_size
   violated (30% of book on Strong Uptrend vs the 15% ceiling).
2. port_pct == SINGLE_NAME_CEILING is reachable (cap binds on large risk-budget sizes).
3. When the ceiling cannot afford one whole share, NO size is suggested (the
   old max(1, ...) floor emitted one share = 45% of book with ceiling_capped
   False). shares >= 1 whenever a size IS returned.
4. No no-size path is ever SILENT: missing price/stop gives {} (nothing to
   explain), a zero portfolio gives `portfolio_unknown`, a ceiling that can't
   afford one share gives `ceiling_infeasible`, and a price at/below the stop
   gives `stop_infeasible` — each with a reason the renderer prints, and none
   carrying a `shares` key.
5. Both lanes size off an ATR stop, not a hardcoded trend bucket, verified
   end-to-end through _grow_today (new-pick lane re-derives against the LIVE
   price; the add lane reads held_data's stop verbatim, which is deliberate).
6. No bare 0.05/0.07/0.08 literal remains in the sizing path.
7. deploy_note frames RISK, not capital, and its figure is the one the code
   computes — asserted against the rendered string, not recomputed in the test.
8. sizing_unavailable_reason distinguishes "stop" from "ceiling" and never
   disagrees with position_sizing.
"""
import ast
from datetime import date
from pathlib import Path

import pytest

from stock_analyzer.constants import (
    ATR_STOP_MULT,
    COMPOSITE_BUY,
    RISK_PCT_PER_TRADE,
    SINGLE_NAME_CEILING,
)
from stock_analyzer.daily_briefing import (
    SIZING_FORMULA_VERSION,
    _grow_today,
    _position_size_for_render,
)
from stock_analyzer.risk import position_sizing, sizing_unavailable_reason

# ── fixtures ──────────────────────────────────────────────────────────────────

_TODAY = date(2026, 8, 22)

# A minimal held-data bundle shape (mirrors bundle_loader output).
def _held_bundle(price: float, stop: float, entry_lo=None, entry_hi=None) -> dict:
    return {
        "stop":     stop,
        "atr":      round(price - stop, 2),
        "entry_lo": entry_lo if entry_lo is not None else round(stop * 1.01, 2),
        "entry_hi": entry_hi if entry_hi is not None else round(price * 1.01, 2),
    }


# ── 1. port_pct invariant: never exceeds SINGLE_NAME_CEILING ─────────────────

class TestPortPctInvariant:
    """The hard property: port_pct <= SINGLE_NAME_CEILING for all valid inputs."""

    @pytest.mark.parametrize("price", [5.0, 50.0, 250.0, 1000.0, 4500.0])
    @pytest.mark.parametrize("portfolio_value", [10_000, 50_000, 250_000, 1_000_000])
    @pytest.mark.parametrize("stop_fraction", [0.03, 0.05, 0.07, 0.10, 0.20])
    def test_port_pct_never_exceeds_ceiling(self, price, portfolio_value, stop_fraction):
        """Property test: ceiling always holds across price × PV × stop cross."""
        stop = round(price * (1 - stop_fraction), 2)
        sz = _position_size_for_render(portfolio_value, price, stop, None, None)
        if not sz or sz.get("ceiling_infeasible"):
            # Degenerate input (stop >= price), or one share alone breaches the
            # ceiling so no size is suggested at all. Both are the fail-closed
            # paths — there is no port_pct to assert against.
            return
        assert sz["port_pct"] <= SINGLE_NAME_CEILING, (
            f"port_pct {sz['port_pct']:.1f}% > SINGLE_NAME_CEILING {SINGLE_NAME_CEILING}% "
            f"(price={price}, pv={portfolio_value}, stop_fraction={stop_fraction})"
        )

    def test_port_pct_cannot_exceed_ceiling_on_old_strong_uptrend_case(self):
        """The exact case _suggest_size('Strong Uptrend') used to produce 30% of book.

        At a 5% hardcoded stop, RISK_PCT_PER_TRADE / 0.05 = 30% regardless of
        price. With the ATR stop this is bounded by SINGLE_NAME_CEILING.
        """
        portfolio_value = 100_000
        price = 100.0
        # Reproduce the old 5% stop that drove the 30% allocation
        stop = price * 0.95  # 5% stop -> old code: 30% of book
        sz = _position_size_for_render(portfolio_value, price, stop, None, None)
        assert sz, "Expected non-empty sizing dict for valid inputs"
        assert sz["port_pct"] <= SINGLE_NAME_CEILING


# ── 2. Boundary: ceiling is reachable, > never occurs ─────────────────────────

class TestCeilingBoundary:
    """The cap binds (ceiling_capped == True) on names with tight stops."""

    def test_ceiling_is_reachable(self):
        """A tight stop (< RISK_PCT_PER_TRADE / SINGLE_NAME_CEILING) produces ceiling_capped."""
        portfolio_value = 100_000
        price = 200.0
        # A 0.5% stop → risk-budget shares = (100k * 1.5%) / (200*0.005) = 1500 shares
        # = $300k = 300% of book → definitely capped
        stop = price * 0.995
        sz = _position_size_for_render(portfolio_value, price, stop, None, None)
        assert sz, "Expected non-empty sizing dict"
        assert sz["ceiling_capped"] is True
        assert sz["port_pct"] == SINGLE_NAME_CEILING

    def test_ceiling_not_exceeded_when_capped(self):
        """When capped, port_pct never exceeds SINGLE_NAME_CEILING at all.

        This previously allowed a one-share overshoot ("actual pct may be
        slightly above the theoretical ceiling"). That allowance existed only
        because of the max(1, ...) floor, which now fails closed instead, so the
        assertion is exact.
        """
        portfolio_value = 100_000
        price = 200.0
        stop = price * 0.995
        sz = _position_size_for_render(portfolio_value, price, stop, None, None)
        assert sz
        assert sz["port_pct"] <= SINGLE_NAME_CEILING

    def test_uncapped_case_ceiling_capped_is_false(self):
        """A wide stop (risk-budget fits inside ceiling) sets ceiling_capped=False."""
        portfolio_value = 10_000
        price = 10.0
        stop = price * 0.50  # 50% stop — very few shares, well inside ceiling
        sz = _position_size_for_render(portfolio_value, price, stop, None, None)
        assert sz
        assert sz["ceiling_capped"] is False
        assert sz["port_pct"] <= SINGLE_NAME_CEILING


# ── 3. shares >= 1 after capping ──────────────────────────────────────────────

class TestSharesFloor:
    """shares is always >= 1 — the max(1,...) guard in position_sizing must hold."""

    def test_shares_at_least_one_standard_case(self):
        portfolio_value = 50_000
        price = 100.0
        stop = price * 0.93  # 7% stop
        sz = _position_size_for_render(portfolio_value, price, stop, None, None)
        assert sz
        assert sz["shares"] >= 1

    def test_high_price_small_account_suppresses_rather_than_breaching(self):
        """One share > the ceiling ⇒ NO size, not a silent breach.

        The old `max(1, ...)` floor in position_sizing emitted one share here
        anyway — and left ceiling_capped False, because risk_based_shares was
        also 1 so the `shares > ceiling_shares` test never fired. A $4,000 name
        against a $5,000 book therefore rendered 1 share = 80% of portfolio with
        no disclosure at all. There is no honest size at this cap, so the
        contract is now: suppress, and hand the render layer the reason.
        """
        portfolio_value = 5_000      # small account
        price = 4_000.0              # one share = 80% of the book
        stop = price * 0.97          # 3% stop
        # ceiling affords int(5000 * 0.15 / 4000) = int(0.1875) = 0 whole shares
        sz = _position_size_for_render(portfolio_value, price, stop, None, None)
        assert sz.get("ceiling_infeasible") is True
        assert "shares" not in sz, (
            "a suppression marker must carry no share count — every renderer "
            "gates its size text on `shares`, so a stray 0 would print '0 shares'"
        )
        assert sz["one_share_pct"] == pytest.approx(80.0)

    def test_position_sizing_returns_none_when_one_share_breaches_ceiling(self):
        """The same contract one layer down, so every caller fails closed.

        Analysis, Watchlist and the Grow Today adapter all treat None as "no
        sizing block", so returning None here propagates the suppression to
        every surface rather than only the one Phase 1 touched.
        """
        assert position_sizing(
            portfolio_value=5_000, risk_pct=RISK_PCT_PER_TRADE,
            entry=4_000.0, stop=3_880.0, max_position_pct=SINGLE_NAME_CEILING,
        ) is None

    def test_exactly_one_share_affordable_still_sizes(self):
        """Boundary: when the ceiling affords exactly one share, size normally.

        Guards against the fail-closed branch being written as `<= 1` and
        silently suppressing the smallest legitimate position.
        """
        portfolio_value = 10_000     # ceiling = $1,500
        price = 1_500.0              # exactly one share fits
        sz = _position_size_for_render(portfolio_value, price, price * 0.97, None, None)
        assert sz.get("ceiling_infeasible") is None
        assert sz["shares"] == 1
        assert sz["port_pct"] <= SINGLE_NAME_CEILING

    def test_position_sizing_never_returns_zero_shares_when_it_sizes_at_all(self):
        """Whenever position_sizing DOES return a size, that size is >= 1 share.

        The 1-share floor is still correct for every case the ceiling can
        afford; what changed is that the infeasible case now returns None
        instead of forcing a floor that breached the cap. So the floor is
        asserted over sized results only — a zero would mean a card rendering
        "0 shares", which is never a valid instruction.
        """
        for pv, entry, stop in [
            (50_000, 100.0, 93.0),
            (10_000, 1_500.0, 1_455.0),   # exactly one share fits the ceiling
            (250_000, 50.0, 49.75),       # very tight stop -> ceiling binds
        ]:
            result = position_sizing(
                portfolio_value=pv, risk_pct=RISK_PCT_PER_TRADE,
                entry=entry, stop=stop, max_position_pct=SINGLE_NAME_CEILING,
            )
            assert result is not None, f"expected a size for pv={pv}, entry={entry}"
            assert result["shares"] >= 1


# ── 4. None degradation: entry <= stop returns {} ─────────────────────────────

class TestNoneDegradation:
    """position_sizing() returns None on bad input; the adapter never raises.

    Note the split:
    - Zero PRICE or STOP -> {} (market data missing; nothing to explain).
    - Zero PORTFOLIO VALUE -> portfolio_unknown marker (2026-08-27 fix):
      price/stop are present and valid, but the app does not know the book
      yet. Returns a marker so the card can explain WHY, not silently blank.
    - Stop AT or ABOVE price -> stop_infeasible marker (card explains itself).
    That distinction (explained marker vs bare {}) is the Opus review's
    blocking finding B2, extended to the portfolio case here.
    """

    def test_stop_equal_to_price_is_explained_not_empty(self):
        sz = _position_size_for_render(100_000, 100.0, 100.0, None, None)
        assert sz.get("stop_infeasible") is True and "shares" not in sz

    def test_stop_above_price_is_explained_not_empty(self):
        sz = _position_size_for_render(100_000, 100.0, 105.0, None, None)
        assert sz.get("stop_infeasible") is True and "shares" not in sz

    def test_position_sizing_none_when_entry_le_stop(self):
        """Direct check that position_sizing() returns None on bad stop."""
        result = position_sizing(100_000, RISK_PCT_PER_TRADE, 100.0, 100.0,
                                 max_position_pct=SINGLE_NAME_CEILING)
        assert result is None

    def test_zero_stop_returns_empty(self):
        sz = _position_size_for_render(100_000, 100.0, 0.0, None, None)
        assert sz == {}

    def test_zero_portfolio_value_returns_portfolio_unknown_marker(self):
        # 2026-08-27 (fabricated-book fix): a non-positive portfolio_value with
        # valid price/stop now returns a portfolio_unknown marker instead of {},
        # so the card can explain WHY rather than silently showing no size.
        sz = _position_size_for_render(0, 100.0, 90.0, None, None)
        assert sz.get("portfolio_unknown") is True
        assert "shares" not in sz

    def test_zero_price_returns_empty(self):
        sz = _position_size_for_render(100_000, 0.0, 90.0, None, None)
        assert sz == {}

    def test_no_raise_on_degenerate_inputs(self):
        """No exception for any degenerate input combination."""
        for args in [
            (0, 0, 0, None, None),
            (100_000, 50.0, 50.0, None, None),
            (-1000, 100.0, 90.0, None, None),
        ]:
            try:
                _position_size_for_render(*args)
            except Exception as exc:
                pytest.fail(f"Raised {exc!r} for args {args}")


# ── 5. Both paths agree on stop methodology ───────────────────────────────────

class TestStopMethodologyAgreement:
    """New-pick and add-winner both call _position_size_for_render with ATR stop.

    Verify that for equivalent inputs the adapter produces the same output
    regardless of which path sourced the stop — the methodology is now identical.
    """

    def test_both_paths_size_off_the_atr_stop_end_to_end(self):
        """Exercise _grow_today so the MOVE and the held_data stop source are covered.

        The adapter being correct in isolation says nothing about whether
        `_grow_today` still attaches `sizing` to the pick dict after the
        computation moved below the composite gate, nor whether the add-winner
        branch reads the right `held_data` key. Both would ship green against a
        pure-function test.
        """
        from tests.test_daily_briefing import _scanner_df, make_port_df

        pv, price, atr = 100_000.0, 100.0, 3.0
        bundle = {
            "total": COMPOSITE_BUY + 10, "rec": {"label": "Buy"},
            "fundamentals_available": True,
            "stop": round(price - ATR_STOP_MULT * atr, 2), "atr": atr,
            "entry_lo": 99.0, "entry_hi": 101.5,
        }
        port_df = make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}])
        scanner = _scanner_df([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Tech"}])
        grow = _grow_today(port_df, scanner, [], {}, _TODAY, pv, {"tone": "bull"},
                           composites={"NEW": bundle})

        pick = next((p for p in grow["new_picks"] if p["ticker"] == "NEW"), None)
        assert pick is not None, "the fixture must produce a pick, or this proves nothing"
        sz = pick["sizing"]
        assert sz, "sizing must still be attached after moving below the composite gate"
        # risk_per_share is exactly ATR_STOP_MULT x ATR, so shares is derivable.
        # shares is min(risk-budget, ceiling) — assert the contract, not one leg.
        # (An earlier draft asserted the risk-budget leg alone and failed at 150
        # vs 250: 250 x $100 is 25% of the book, so the ceiling correctly binds.)
        # Read the price the engine ACTUALLY sized against — the scanner fixture
        # supplies its own, and assuming the bundle's price here silently tested
        # the wrong ceiling leg.
        _live = pick["price"]
        _risk_leg    = int((pv * RISK_PCT_PER_TRADE) / (ATR_STOP_MULT * atr))
        _ceiling_leg = int(pv * (SINGLE_NAME_CEILING / 100.0) / _live)
        assert sz["shares"] == min(_risk_leg, _ceiling_leg)
        assert sz["port_pct"] <= SINGLE_NAME_CEILING
        assert sz["ceiling_capped"] is (_risk_leg > _ceiling_leg)
        # The stop must sit ATR_STOP_MULT x ATR below the LIVE price, not below
        # the bundle's close — this is the B2 fix.
        assert sz["stop"] == pytest.approx(round(_live - ATR_STOP_MULT * atr, 2))
        # Entry zone is derived on that same live-price basis, so it always
        # brackets the price the size was computed from.
        assert sz["entry_lo"] < _live < sz["entry_hi"], (
            f"entry zone {sz['entry_lo']}-{sz['entry_hi']} must bracket {_live}"
        )

    def test_add_winner_lane_sizes_off_the_held_data_stop(self):
        """The ADD lane end-to-end — the coverage gap the Opus review caught.

        Both other end-to-end tests pass held_data={} and a non-winner row, so
        add_positions was always empty and `sizing` was only ever read off
        new_picks. Reading the wrong held_data key, or dropping sizing from the
        add branch, would have shipped green. The add lane deliberately keeps the
        bundle's last-close ATR stop (see the note at its call site), so this
        asserts the stop is taken verbatim from held_data.
        """
        from tests.test_daily_briefing import _winner_row, make_port_df

        pv, price, atr = 100_000.0, 100.0, 3.0
        bundle = {
            "stop": round(price - ATR_STOP_MULT * atr, 2), "atr": atr,
            "entry_lo": 99.0, "entry_hi": 101.5,
        }
        port_df = make_port_df([_winner_row(ticker="WINNER", price=price)])
        grow = _grow_today(port_df, None, [], {"WINNER": bundle}, _TODAY, pv,
                           {"tone": "bull"})
        add = next((a for a in grow["add_positions"] if a["ticker"] == "WINNER"), None)
        assert add is not None, "fixture must surface an add, or this proves nothing"
        sz = add["sizing"]
        assert sz, "sizing must be attached to add_positions too"
        assert sz["stop"] == bundle["stop"], (
            "the add lane must read its stop from held_data, verbatim"
        )
        assert sz["port_pct"] <= SINGLE_NAME_CEILING
        _risk_leg    = int((pv * RISK_PCT_PER_TRADE) / (ATR_STOP_MULT * atr))
        _ceiling_leg = int(pv * (SINGLE_NAME_CEILING / 100.0) / price)
        assert sz["shares"] == min(_risk_leg, _ceiling_leg)

    def test_new_pick_stop_uses_live_price_not_the_bundle_close(self):
        """A mover whose live price ran past the bundle close must still size.

        Regression for the Opus review's blocking B2: the stop was taken from the
        bundle (derived from its own last close) while the size used the scanner
        row's live price. Below the stale stop, risk_per_share went negative and
        the card rendered a BUY with no size and no explanation.
        """
        from tests.test_daily_briefing import _scanner_df, make_port_df

        pv, atr = 100_000.0, 3.0
        # Bundle close was 100 (stop 94); the live quote has since fallen to 92,
        # i.e. BELOW the stale stop. Same-basis derivation must keep sizing valid.
        bundle = {
            "total": COMPOSITE_BUY + 10, "rec": {"label": "Buy"},
            "fundamentals_available": True,
            "stop": round(100.0 - ATR_STOP_MULT * atr, 2), "atr": atr,
            "entry_lo": 99.0, "entry_hi": 101.5,
        }
        port_df = make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}])
        scanner = _scanner_df([{"ticker": "NEW", "score": COMPOSITE_BUY + 10,
                                "sector": "Tech", "price": 92.0}])
        grow = _grow_today(port_df, scanner, [], {}, _TODAY, pv, {"tone": "bull"},
                           composites={"NEW": bundle})
        pick = next((p for p in grow["new_picks"] if p["ticker"] == "NEW"), None)
        # Deliberately an assert, not a skip: this is the ONLY test pinning the
        # price-basis fix, and a skip would let a future gate change silently
        # turn it into a permanent green no-op.
        assert pick is not None, "fixture must surface the pick, or this proves nothing"
        sz = pick["sizing"]
        assert sz.get("shares"), (
            "a live price below the BUNDLE's stale stop must not blank the size — "
            "the stop is re-derived against the same price the size uses"
        )
        assert sz["stop"] < 92.0, "stop must sit below the live price, not the stale close"

    def test_adapter_keys_match_renderer_contract(self):
        """The adapter output has every key the renderers (app.py, notify.py) expect."""
        sz = _position_size_for_render(50_000, 100.0, 93.0, 94.0, 101.5)
        assert sz
        required = {"shares", "total_cost", "stop", "stop_pct", "port_pct",
                    "risk_budget", "entry_lo", "entry_hi", "ceiling_capped",
                    "uncapped_shares"}
        missing = required - set(sz.keys())
        assert not missing, f"Adapter missing keys: {missing}"


# ── 6. No bare 0.05/0.07/0.08 literal in sizing path ─────────────────────────

class TestNoHardcodedStopLiterals:
    """_suggest_size is gone; no 0.05/0.07/0.08 stop literals remain in daily_briefing."""

    def test_no_suggest_size_function_defined(self):
        """_suggest_size must not exist in the module (deleted, not just unused)."""
        import stock_analyzer.daily_briefing as db
        assert not hasattr(db, "_suggest_size"), (
            "_suggest_size still exists in daily_briefing — it should have been deleted"
        )

    def test_no_hardcoded_stop_literals_in_source(self):
        """Source must not contain the retired hardcoded stop fractions as float literals."""
        src_path = Path(__file__).resolve().parent.parent / "stock_analyzer" / "daily_briefing.py"
        tree = ast.parse(src_path.read_text(encoding="utf-8"))

        # Walk all Constant nodes; check for the three retired stop fractions
        retired = {0.05, 0.07, 0.08}
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                if node.value in retired:
                    found.append(node.value)

        assert not found, (
            f"Retired stop fraction literal(s) still present in daily_briefing.py: {found}. "
            "These were from _suggest_size and must have been removed."
        )


# ── 7. deploy_note wording ────────────────────────────────────────────────────

class TestDeployNote:
    """deploy_note must express risk, not capital to deploy."""

    def test_deploy_note_states_the_real_risk_dollars(self):
        """The note's dollar figure equals the risk budget, computed by the CODE.

        The previous version of this test recomputed the formula inside the test
        and asserted the result against itself, so the formula could have changed
        freely and the suite stayed green. This drives `_grow_today` and parses
        the rendered string instead.
        """
        from tests.test_daily_briefing import _scanner_df, make_port_df

        pv, atr, price = 100_000.0, 3.0, 100.0
        bundle = {
            "total": COMPOSITE_BUY + 10, "rec": {"label": "Buy"},
            "fundamentals_available": True,
            "stop": round(price - ATR_STOP_MULT * atr, 2), "atr": atr,
            "entry_lo": 99.0, "entry_hi": 101.5,
        }
        port_df = make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}])
        scanner = _scanner_df([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Tech"}])
        grow = _grow_today(port_df, scanner, [], {}, _TODAY, pv, {"tone": "bull"},
                           composites={"NEW": bundle})

        note = grow.get("deploy_note")
        assert note, "fixture must produce a note, or this test proves nothing"
        n_trades = len(grow["new_picks"]) + len(grow["add_positions"])
        expected = pv * RISK_PCT_PER_TRADE * n_trades
        assert f"${expected:,.0f}" in note, f"expected ${expected:,.0f} in {note!r}"
        # And the framing must be risk, never capital to commit.
        assert "deploying" not in note.lower()
        assert "risk" in note.lower()

    def test_deploy_note_never_frames_risk_as_capital(self):
        """The rendered note must not use a capital-deployment verb.

        Previously this scanned the whole daily_briefing module for the string
        "deploying", which would fail on any future comment containing the word.
        Asserts on the rendered output instead.
        """
        from tests.test_daily_briefing import _scanner_df, make_port_df

        pv, atr, price = 100_000.0, 3.0, 100.0
        bundle = {
            "total": COMPOSITE_BUY + 10, "rec": {"label": "Buy"},
            "fundamentals_available": True,
            "stop": round(price - ATR_STOP_MULT * atr, 2), "atr": atr,
            "entry_lo": 99.0, "entry_hi": 101.5,
        }
        grow = _grow_today(
            make_port_df([{"ticker": "HELD", "weight": 5.0, "sector": "Tech"}]),
            _scanner_df([{"ticker": "NEW", "score": COMPOSITE_BUY + 10, "sector": "Tech"}]),
            [], {}, _TODAY, pv, {"tone": "bull"}, composites={"NEW": bundle},
        )
        note = grow.get("deploy_note") or ""
        assert note, "fixture must produce a note"
        assert "deploy" not in note.lower(), f"capital framing leaked into {note!r}"

    def test_deploy_note_contains_risk_phrasing(self):
        """The note must mention risk (the correct framing, not capital deployment)."""
        src_path = Path(__file__).resolve().parent.parent / "stock_analyzer" / "daily_briefing.py"
        src = src_path.read_text(encoding="utf-8")
        assert "if every stop hits" in src, (
            "deploy_note risk framing 'if every stop hits' not found — "
            "re-check the deploy_note edit."
        )


# ── 8. sizing_unavailable_reason — the two causes must stay distinguishable ────

class TestSizingUnavailableReason:
    """One detector, two causes. Conflating them tells the user to fix the wrong thing.

    Analysis and Watchlist render different copy off this. Before F-249 both
    printed "stop price too close to entry or not set" for the ceiling case,
    blaming the stop while a healthy 2xATR stop rendered directly above.
    """

    def test_healthy_inputs_return_none(self):
        assert sizing_unavailable_reason(100_000, 100.0, 94.0, SINGLE_NAME_CEILING) is None

    def test_degenerate_stop_is_reported_as_stop(self):
        assert sizing_unavailable_reason(100_000, 100.0, 100.0, SINGLE_NAME_CEILING) == "stop"
        assert sizing_unavailable_reason(100_000, 100.0, 105.0, SINGLE_NAME_CEILING) == "stop"
        assert sizing_unavailable_reason(100_000, 100.0, 0.0, SINGLE_NAME_CEILING) == "stop"

    def test_one_share_over_ceiling_is_reported_as_ceiling(self):
        # $4,500 name on a $10,000 book: ceiling affords int(1500/4500) = 0 shares.
        assert sizing_unavailable_reason(10_000, 4_500.0, 4_365.0, SINGLE_NAME_CEILING) == "ceiling"

    def test_stop_takes_precedence_over_ceiling(self):
        """A degenerate stop is reported even when the ceiling also fails.

        Order matters for the copy: telling someone their account is too small
        while their stop is also broken would send them to fix the wrong thing
        twice. The stop is the more proximate, more fixable cause.
        """
        assert sizing_unavailable_reason(10_000, 4_500.0, 4_500.0, SINGLE_NAME_CEILING) == "stop"

    def test_no_ceiling_passed_means_no_ceiling_verdict(self):
        """Callers that pass no cap must never get a "ceiling" answer."""
        assert sizing_unavailable_reason(10_000, 4_500.0, 4_365.0, None) is None

    def test_agrees_with_position_sizing_across_a_grid(self):
        """The detector and position_sizing must never disagree.

        position_sizing delegates BOTH guards to this function, so a
        disagreement would mean the delegation was undone.
        """
        # 0 included 2026-08-27: the cheapest direct pin that the detector and
        # position_sizing still agree at the new "portfolio" branch.
        for pv in (0, 5_000, 10_000, 50_000, 250_000):
            for entry in (5.0, 100.0, 1_500.0, 4_500.0):
                for stop_frac in (0.0, 0.03, 0.5, 1.0, 1.2):
                    stop = entry * (1 - stop_frac)
                    reason = sizing_unavailable_reason(pv, entry, stop, SINGLE_NAME_CEILING)
                    sized = position_sizing(pv, RISK_PCT_PER_TRADE, entry, stop,
                                            max_position_pct=SINGLE_NAME_CEILING)
                    assert (reason is None) == (sized is not None), (
                        f"disagreement at pv={pv} entry={entry} stop={stop}: "
                        f"reason={reason!r} sized={sized is not None}"
                    )


# ── 9. stop_infeasible marker (Opus review B2) ────────────────────────────────

class TestStopInfeasibleMarker:
    """A price at/below the stop must explain itself, not render a blank size."""

    def test_price_at_or_below_stop_returns_an_explained_marker(self):
        sz = _position_size_for_render(100_000, 90.0, 94.0, None, None)
        assert sz.get("stop_infeasible") is True
        assert "shares" not in sz, "a marker must never carry a share count"
        assert sz["stop_at"] == 94.0

    def test_marker_is_never_silent(self):
        """Every no-size path carries a reason the renderer can print."""
        cases = [
            _position_size_for_render(100_000, 90.0, 94.0, None, None),   # stop
            _position_size_for_render(10_000, 4_500.0, 4_365.0, None, None),  # ceiling
        ]
        for sz in cases:
            assert sz, "no-size must not degrade to an empty dict — that renders silently"
            assert sz.get("stop_infeasible") or sz.get("ceiling_infeasible")


# ── 10. Persistence provenance (F-249 Phase 2) ───────────────────────────────

class TestSizingProvenance:
    """Every sizing shape must carry the two keys the DB capture reads.

    The renderer-contract test uses a SUBSET check, so dropping
    `sizing_version` from _position_size_for_render would leave the whole suite
    green while the capture silently went to NULL -- precisely the silent-loss
    class Phase 2 exists to prevent. Asserted per shape, not once.
    """

    def test_full_size_carries_version_and_basis(self):
        sz = _position_size_for_render(100_000, 100.0, 94.0, 99.0, 101.0)
        assert sz["sizing_version"] == SIZING_FORMULA_VERSION
        assert sz["portfolio_value"] == 100_000

    def test_ceiling_marker_carries_version_and_basis(self):
        sz = _position_size_for_render(10_000, 4_500.0, 4_365.0, None, None)
        assert sz.get("ceiling_infeasible") is True
        assert sz["sizing_version"] == SIZING_FORMULA_VERSION
        assert sz["portfolio_value"] == 10_000

    def test_stop_marker_carries_version_and_basis(self):
        sz = _position_size_for_render(100_000, 90.0, 94.0, None, None)
        assert sz.get("stop_infeasible") is True
        assert sz["sizing_version"] == SIZING_FORMULA_VERSION
        assert sz["portfolio_value"] == 100_000

    def test_missing_input_carries_nothing(self):
        """{} must stay bare -- a version with no capture would misreport state.

        The contract reads "version set" as "the app made a sizing decision".
        A missing price or stop gives {} (nothing to explain; the caller simply
        has no market data). A zero portfolio_value is distinct: the market data
        is present, but the app does not know the book — it returns a
        portfolio_unknown marker with sizing_version set, so the renderer can
        explain WHY rather than silently showing nothing.
        """
        # Zero price -> {} (no market data, nothing to explain)
        assert _position_size_for_render(100_000, 0.0, 94.0, None, None) == {}
        # Zero portfolio_value with valid price/stop -> portfolio_unknown marker
        sz = _position_size_for_render(0, 100.0, 94.0, None, None)
        assert sz.get("portfolio_unknown") is True and "shares" not in sz


# ── 11. Net-capital cap (F-255) ────────────────────────────────────────────────
# SEPARATE, additive cap: 25% of net capital (equity after margin debit) on
# top of the existing 15%-of-gross-book ceiling. Version bumped 2 -> 3
# regardless of whether net_capital is ever supplied (provenance marks the
# FORMULA, not whether a given call happened to use the new branch); the
# sizing OUTPUT itself stays byte-identical when net_capital is omitted.

class TestNetCapitalCap:

    def test_sizing_formula_version_is_3(self):
        assert SIZING_FORMULA_VERSION == 3

    def test_net_capital_none_is_byte_identical_to_pre_f255_output(self):
        """_position_size_for_render(..., net_capital=None) (the default, and
        every call site before F-255) must produce the same shape as calling
        it with no net_capital argument at all."""
        cases = [
            (100_000, 100.0, 94.0, 99.0, 101.0),   # full size
            (10_000, 4_500.0, 4_365.0, None, None),  # ceiling_infeasible
            (100_000, 90.0, 94.0, None, None),       # stop_infeasible
            (0, 100.0, 94.0, None, None),            # zero book -> portfolio_unknown marker
        ]
        for args in cases:
            without_kw = _position_size_for_render(*args)
            with_none = _position_size_for_render(*args, net_capital=None)
            assert with_none == without_kw
            assert "capital_capped" not in with_none
            assert "capital_pct" not in with_none

    def test_net_capital_triggers_capped_key_in_render_dict(self):
        """A net_capital small enough to bind produces capital_capped/capital_pct
        in the normal (non-infeasible) render dict."""
        sz = _position_size_for_render(
            24503, 143.25, 140.0, None, None, net_capital=7802,
        )
        assert "capital_capped" in sz
        assert "capital_pct" in sz
        assert sz["capital_capped"] is True
        assert sz["capital_pct"] <= 25.0

    def test_net_capital_infeasible_marker_shape(self):
        """net_capital small enough that even one share breaches the capital
        cap must give an explained capital_infeasible marker, never a bare
        0-share dict and never silently falling through to {}."""
        sz = _position_size_for_render(
            100_000, 500.0, 450.0, None, None, net_capital=100.0,
        )
        assert sz.get("capital_infeasible") is True
        assert "shares" not in sz
        assert sz["sizing_version"] == SIZING_FORMULA_VERSION
        assert sz["net_capital"] == 100.0

    def test_net_capital_called_state_is_infeasible_not_missing(self):
        """net_capital <= 0 (margin-called) must also route to
        capital_infeasible, not to the bare-missing-input {} branch."""
        sz = _position_size_for_render(
            100_000, 100.0, 94.0, None, None, net_capital=0.0,
        )
        assert sz.get("capital_infeasible") is True
