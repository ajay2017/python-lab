"""Tests for stock_analyzer/home_risk_synthesis.py (F-260 Phase 3, Unit A).

This module is a byte-identical lift of Home's memo-miss correlation/
diversification block out of app.py — a file with zero test coverage of its
own. The point of the lift is that the offline-sentinel discipline (failure
returns `None`/empty shapes, never a fabricated value) can finally be asserted
directly instead of only verified by inline reading.
"""
import pandas as pd

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
        bundle = hrs.build_correlation_bundle(port_df, held, 50_000.0)

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
