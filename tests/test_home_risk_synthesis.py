"""Tests for stock_analyzer/home_risk_synthesis.py (F-260 Phase 3, Units A & B).

This module is a byte-identical lift of Home's memo-miss correlation/
diversification block (Unit A) and risk-metrics/fragility/high-beta/risk-
advisor block (Unit B) out of app.py — a file with zero test coverage of its
own. The point of the lift is that the offline-sentinel discipline (failure
returns `None`/empty shapes, never a fabricated value) can finally be asserted
directly instead of only verified by inline reading.
"""
import pandas as pd
from unittest.mock import MagicMock

from stock_analyzer import home_risk_synthesis as hrs


def _hist(n, start=100.0, step=1.0):
    return {"df": pd.DataFrame({"Close": [start + step * i for i in range(n)]})}


def _held_data():
    # Three real-shaped, non-degenerate price histories so
    # correlation_matrix/diversification_score actually compute something.
    return {
        "AAA": _hist(60, start=100.0, step=1.0),
        "BBB": _hist(60, start=200.0, step=-0.6),
        "CCC": _hist(60, start=50.0, step=0.3),
    }


def _port_df(tickers=("AAA", "BBB", "CCC"), sector="Technology"):
    n = len(tickers)
    return pd.DataFrame({
        "Ticker":       list(tickers),
        "Sector":       [sector] * n,
        "Market Value": [10_000.0] * n,
        "Weight (%)":   [100.0 / n] * n,
        "Score":        [70.0] * n,
        "Signal":       ["BUY"] * n,
        "P&L (%)":      [5.0] * n,
    })


class TestHappyPath:
    def test_bundle_shape_when_computation_succeeds(self):
        held = _held_data()
        port_df = _port_df()
        # Explicit (empty is fine) sector_candidates/discovery_universe — App
        # Settings Commit 3 made diversifying_candidate_pool's own params
        # required, so leaving these at build_correlation_bundle's own None
        # default would now raise inside the pool call (caught two frames up
        # as the div_recs=None offline sentinel) instead of exercising the
        # real "computed successfully" path this test is named for.
        bundle = hrs.build_correlation_bundle(
            port_df, held, 50_000.0,
            sector_candidates={}, discovery_universe={},
        )

        assert set(bundle) == {
            "corr_df", "div", "div_score", "avg_corr", "risk_pairs",
            "div_label", "corr_coverage", "div_recs",
        }
        assert not bundle["corr_df"].empty
        assert isinstance(bundle["div"], dict)
        assert bundle["div_score"] is not None
        assert bundle["avg_corr"] is not None
        assert isinstance(bundle["risk_pairs"], list)
        assert bundle["div_label"] in (
            "Well Diversified", "Moderate", "High Correlation Risk",
        )
        assert bundle["corr_coverage"] is not None
        assert isinstance(bundle["div_recs"], list)


class TestCorrelationChainFailure:
    def test_offline_sentinel_shape_exactly(self, monkeypatch):
        """A raise anywhere in the corr->coverage->score->label chain must
        yield the EXACT sentinel shape — never a fabricated partial value."""
        monkeypatch.setattr(
            hrs, "correlation_matrix",
            lambda held_data: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        bundle = hrs.build_correlation_bundle(_port_df(), _held_data(), 50_000.0)

        assert bundle["corr_df"].empty is True
        assert bundle["div"] == {"score": None, "avg_correlation": None, "risk_pairs": []}
        assert bundle["div_score"] is None
        assert bundle["avg_corr"] is None
        assert bundle["risk_pairs"] == []
        assert bundle["div_label"] == "Unavailable"
        assert bundle["corr_coverage"] is None

    def test_failure_downstream_of_correlation_matrix_still_yields_the_same_sentinel(self, monkeypatch):
        """Same invariant, but the raise happens on `correlation_coverage` —
        the SECOND call in the chain, after `correlation_matrix` itself
        already succeeded. Pins that the whole chain (matrix through label)
        shares ONE try/except, not that only the first call is guarded — a
        future edit that narrowed the try to wrap just `correlation_matrix`
        would pass the sibling test above (which fails at the first call)
        but must fail THIS one, since `corr_df` would otherwise stay
        populated from the succeeded matrix call while the rest of the
        sentinel shape went missing."""
        monkeypatch.setattr(
            hrs, "correlation_coverage",
            lambda held_data: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        bundle = hrs.build_correlation_bundle(_port_df(), _held_data(), 50_000.0)

        assert bundle["corr_df"].empty is True, (
            "a successfully-computed corr_df must still be RESET to empty — "
            "the except branch resets the whole shape, it doesn't preserve "
            "partial progress from earlier calls in the same try"
        )
        assert bundle["div"] == {"score": None, "avg_correlation": None, "risk_pairs": []}
        assert bundle["div_score"] is None
        assert bundle["avg_corr"] is None
        assert bundle["risk_pairs"] == []
        assert bundle["div_label"] == "Unavailable"
        assert bundle["corr_coverage"] is None


class TestDiversificationRecommendationsFailure:
    def test_div_recs_sentinel_is_none_not_empty_list(self, monkeypatch):
        """The correlation chain succeeds normally; only the recommendations
        call fails. Its offline sentinel must be `None`, NOT `[]` — this is
        the exact invariant the module docstring calls out, because every
        sibling cache's contract is "`None` means offline, `[]` means checked
        and genuinely nothing to recommend". Collapsing the two would make a
        real "nothing to add" indistinguishable from "we never checked"."""
        monkeypatch.setattr(
            hrs, "diversification_recommendations",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        bundle = hrs.build_correlation_bundle(_port_df(), _held_data(), 50_000.0)

        # Correlation chain must have succeeded (it was not touched).
        assert not bundle["corr_df"].empty
        assert bundle["div_score"] is not None

        # The load-bearing assertion: `is None`, never `== []` / `not x`.
        assert bundle["div_recs"] is None, (
            "div_recs must be the None offline sentinel on failure, not an "
            "empty list — `not bundle['div_recs']` would pass for both and "
            "hide a regression to the wrong sentinel"
        )


class TestDiversificationLabelBands:
    """42/30 are bare literals in the extracted code by deliberate design
    (naming them constants is a separate, later decision) — these boundary
    tests pin the bands exactly as they exist today, byte-identical to the
    inline app.py block this module replaced."""

    def _bundle_at_score(self, monkeypatch, score):
        monkeypatch.setattr(
            hrs, "diversification_score",
            lambda corr_df, weights=None: {
                "score": score, "avg_correlation": 0.1, "risk_pairs": [],
            },
        )
        return hrs.build_correlation_bundle(_port_df(), _held_data(), 50_000.0)

    def test_exactly_42_is_well_diversified(self, monkeypatch):
        bundle = self._bundle_at_score(monkeypatch, 42)
        assert bundle["div_label"] == "Well Diversified"

    def test_just_under_42_is_moderate(self, monkeypatch):
        bundle = self._bundle_at_score(monkeypatch, 41.99)
        assert bundle["div_label"] == "Moderate"

    def test_exactly_30_is_moderate(self, monkeypatch):
        bundle = self._bundle_at_score(monkeypatch, 30)
        assert bundle["div_label"] == "Moderate"

    def test_just_under_30_is_high_correlation_risk(self, monkeypatch):
        bundle = self._bundle_at_score(monkeypatch, 29.99)
        assert bundle["div_label"] == "High Correlation Risk"


# ---------------------------------------------------------------------------
# Unit B — build_risk_bundle
# ---------------------------------------------------------------------------

_SPY_STUB = pd.DataFrame({"Close": [400.0, 401.0, 402.0]})


def _call_risk_bundle(
    *, port_df=None, held_data=None, h_rets=None,
    total_val=50_000.0, gate_denom=50_000.0, trades_df=None,
    spy_df=_SPY_STUB, rfr=0.04, beta_elevated=1.2, beta_ceiling=1.8,
    fragility_pullback_pct=-10.0,
):
    """Shared call-shape helper — every test supplies its own monkeypatches
    on `hrs` before calling this, then this just wires the args positionally
    the same way app.py's call site does."""
    return hrs.build_risk_bundle(
        port_df if port_df is not None else _port_df(),
        held_data if held_data is not None else _held_data(),
        h_rets if h_rets is not None else {"AAA": 0.01, "BBB": -0.02, "CCC": 0.03},
        total_val, gate_denom, trades_df,
        spy_df, rfr, beta_elevated, beta_ceiling, fragility_pullback_pct,
    )


class TestRiskBundleHappyPath:
    def test_bundle_shape_when_computation_succeeds(self, monkeypatch):
        h_rets = {"AAA": 0.01, "BBB": -0.02, "CCC": 0.03}
        monkeypatch.setattr(
            hrs, "compute_portfolio_risk_metrics",
            lambda pdf, hd, spy, rfr: {"beta": 1.1, "sharpe": 0.8},
        )
        monkeypatch.setattr(
            hrs, "run_scenario",
            lambda sc, pdf, hd, beta, custom_spy_move=None: {"portfolio_loss_pct": -6.6},
        )
        monkeypatch.setattr(
            hrs, "assess_fragility",
            lambda res, beta, elevated, ceiling, pullback: {"severity": "elevated"},
        )
        monkeypatch.setattr(hrs, "high_beta_share", lambda positions, threshold: 42.0)
        monkeypatch.setattr(
            hrs, "build_risk_advisor_recommendations",
            lambda pdf, hd, pr, hr, tv, gate_denom, trades_df: [
                {"title": "Trim XYZ", "priority": "HIGH"},
                {"title": "Watch ABC", "priority": "MEDIUM"},
            ],
        )

        bundle = _call_risk_bundle(h_rets=h_rets)

        assert set(bundle) == {
            "port_risk", "fragility", "highbeta_share",
            "risk_advisor_recs", "risk_high_alerts", "h_rets",
        }
        assert bundle["port_risk"] == {"beta": 1.1, "sharpe": 0.8}
        assert bundle["fragility"] == {"severity": "elevated"}
        assert bundle["highbeta_share"] == 42.0
        assert bundle["risk_advisor_recs"] == [
            {"title": "Trim XYZ", "priority": "HIGH"},
            {"title": "Watch ABC", "priority": "MEDIUM"},
        ]
        assert bundle["risk_high_alerts"] == ["Trim XYZ"], (
            "only HIGH-priority titles belong in risk_high_alerts"
        )
        assert bundle["h_rets"] is h_rets, (
            "h_rets is passed straight through — the bundle stays the single "
            "source of truth for what this call computed"
        )


class TestPortRiskFailureCascade:
    """The real dependency: fragility reads port_risk's beta, computed in
    THIS call. A port_risk failure must cascade into fragility, and must
    short-circuit the risk-advisor block before it is ever called."""

    def _setup_failing_port_risk(self, monkeypatch, advisor_spy=None):
        monkeypatch.setattr(
            hrs, "compute_portfolio_risk_metrics",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        # Independent try block — give it a normal stub so a later assertion
        # can prove the failure did NOT bleed into it.
        monkeypatch.setattr(hrs, "high_beta_share", lambda positions, threshold: 10.0)
        monkeypatch.setattr(
            hrs, "build_risk_advisor_recommendations",
            advisor_spy if advisor_spy is not None else MagicMock(),
        )

    def test_port_risk_none_cascades_into_fragility_and_advisor_sentinels(self, monkeypatch):
        self._setup_failing_port_risk(monkeypatch)
        bundle = _call_risk_bundle()

        assert bundle["port_risk"] is None
        assert bundle["fragility"] is None, (
            "fragility depends on port_risk's beta — a port_risk failure "
            "must cascade rather than compute a reading from stale data"
        )
        assert bundle["risk_advisor_recs"] is None
        assert bundle["risk_high_alerts"] is None
        assert bundle["highbeta_share"] == 10.0, (
            "high-beta share is an independent try block — a port_risk "
            "failure must not take it down too"
        )

    def test_advisor_is_never_called_when_port_risk_is_none(self, monkeypatch):
        """The 2026-08-04 invariant, explicitly: calling the advisor on a
        None port_risk can itself return a falsy [], which would then get
        cached as "checked, no risk" instead of the honest "never checked".
        The guard must short-circuit BEFORE the call, not merely discard
        its result afterward."""
        advisor_spy = MagicMock()
        self._setup_failing_port_risk(monkeypatch, advisor_spy=advisor_spy)

        _call_risk_bundle()

        assert advisor_spy.call_count == 0


class TestRiskAdvisorFailureModes:
    def _setup_healthy_port_risk(self, monkeypatch):
        monkeypatch.setattr(
            hrs, "compute_portfolio_risk_metrics",
            lambda pdf, hd, spy, rfr: {"beta": 1.1},
        )

    def test_advisor_returns_none_yields_both_caches_none_no_crash(self, monkeypatch):
        self._setup_healthy_port_risk(monkeypatch)
        monkeypatch.setattr(
            hrs, "build_risk_advisor_recommendations",
            lambda *a, **kw: None,
        )

        bundle = _call_risk_bundle()

        assert bundle["port_risk"] == {"beta": 1.1}
        assert bundle["risk_advisor_recs"] is None
        assert bundle["risk_high_alerts"] is None

    def test_advisor_raises_yields_both_caches_none(self, monkeypatch):
        self._setup_healthy_port_risk(monkeypatch)
        monkeypatch.setattr(
            hrs, "build_risk_advisor_recommendations",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        bundle = _call_risk_bundle()

        assert bundle["risk_advisor_recs"] is None
        assert bundle["risk_high_alerts"] is None


class TestHighBetaShareIsolation:
    def test_high_beta_share_failure_is_independent_of_the_other_three_blocks(self, monkeypatch):
        """high_beta_share raising must not cascade into, or be caused by,
        port_risk / fragility / risk-advisor — and their success must not
        mask a real failure here either."""
        monkeypatch.setattr(
            hrs, "compute_portfolio_risk_metrics",
            lambda pdf, hd, spy, rfr: {"beta": 1.1},
        )
        monkeypatch.setattr(
            hrs, "run_scenario",
            lambda sc, pdf, hd, beta, custom_spy_move=None: {"portfolio_loss_pct": -6.6},
        )
        monkeypatch.setattr(
            hrs, "assess_fragility",
            lambda res, beta, elevated, ceiling, pullback: {"severity": "elevated"},
        )
        monkeypatch.setattr(
            hrs, "high_beta_share",
            lambda positions, threshold: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(
            hrs, "build_risk_advisor_recommendations",
            lambda *a, **kw: [{"title": "Trim XYZ", "priority": "HIGH"}],
        )

        bundle = _call_risk_bundle()

        assert bundle["highbeta_share"] is None
        assert bundle["port_risk"] == {"beta": 1.1}
        assert bundle["fragility"] == {"severity": "elevated"}
        assert bundle["risk_advisor_recs"] == [{"title": "Trim XYZ", "priority": "HIGH"}]


class TestFragilityInternalBranching:
    def test_beta_available_and_nonempty_port_df_computes_fragility_via_scenario(self, monkeypatch):
        monkeypatch.setattr(
            hrs, "compute_portfolio_risk_metrics",
            lambda pdf, hd, spy, rfr: {"beta": 1.3},
        )
        scenario_spy = MagicMock(return_value={"portfolio_loss_pct": -7.0})
        monkeypatch.setattr(hrs, "run_scenario", scenario_spy)
        monkeypatch.setattr(
            hrs, "assess_fragility",
            lambda res, beta, elevated, ceiling, pullback: {"severity": "elevated", "loss": res},
        )
        monkeypatch.setattr(hrs, "high_beta_share", lambda positions, threshold: 0.0)
        monkeypatch.setattr(hrs, "build_risk_advisor_recommendations", lambda *a, **kw: None)

        bundle = _call_risk_bundle(fragility_pullback_pct=-10.0)

        assert bundle["fragility"] == {
            "severity": "elevated", "loss": {"portfolio_loss_pct": -7.0},
        }
        assert scenario_spy.call_count == 1
        # custom_spy_move must be threaded through as the fragility pullback pct.
        assert scenario_spy.call_args.kwargs.get("custom_spy_move") == -10.0

    def test_beta_missing_yields_none_via_else_branch_not_exception(self, monkeypatch):
        """port_risk present but beta is None/missing must hit the `else:
        _fragility = None` branch — proven by asserting run_scenario is
        never called, not merely that the end result happens to be None
        (which an accidentally-broadened except could also produce)."""
        monkeypatch.setattr(
            hrs, "compute_portfolio_risk_metrics",
            lambda pdf, hd, spy, rfr: {"beta": None},
        )
        scenario_spy = MagicMock()
        monkeypatch.setattr(hrs, "run_scenario", scenario_spy)
        monkeypatch.setattr(hrs, "high_beta_share", lambda positions, threshold: 0.0)
        monkeypatch.setattr(hrs, "build_risk_advisor_recommendations", lambda *a, **kw: None)

        bundle = _call_risk_bundle()

        assert bundle["fragility"] is None
        assert scenario_spy.call_count == 0, (
            "beta missing must skip the scenario call entirely (the else "
            "branch), not attempt it and swallow a resulting exception"
        )


class TestSpyOrRfrIoFailureFallback:
    """app.py's call site wraps `_cached_spy("6mo")`/`_get_rfr()` in its own
    try/except and falls back to `spy_df=None, rfr=None` on failure — added
    2026-08-31 after an Opus review caught that the extraction had silently
    narrowed the ORIGINAL exception scope (both I/O calls used to sit inside
    the same try as `compute_portfolio_risk_metrics`, so an rfr-fetch failure
    degraded to `port_risk=None`; the first extraction draft resolved both
    calls unguarded at the app.py call site, so the same failure would have
    crashed Home's render instead). This test pins that `rfr=None` specifically
    (the one call NOT already unconditionally called earlier in the same
    page-build function, unlike `_cached_spy`) still correctly cascades to a
    fully-offline bundle when it reaches `build_risk_bundle` un-mocked —
    i.e. real `compute_portfolio_risk_metrics`, not a stub, so this proves the
    ACTUAL function raises on `rfr=None` rather than silently accepting it."""

    def test_rfr_none_degrades_to_a_fully_offline_bundle(self):
        bundle = hrs.build_risk_bundle(
            _port_df(), _held_data(), {}, 50_000.0, 50_000.0, pd.DataFrame(),
            _SPY_STUB, None,  # rfr=None — the app.py fallback value
            1.2, 1.8, -10.0,
        )
        assert bundle["port_risk"] is None
        assert bundle["fragility"] is None
        assert bundle["risk_advisor_recs"] is None
        assert bundle["risk_high_alerts"] is None
