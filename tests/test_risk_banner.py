from stock_analyzer.constants import (
    PORTFOLIO_BETA_CEILING, PORTFOLIO_DRAWDOWN_ACTION_MAX,
    PORTFOLIO_SHARPE_WEAK_MAX, PORTFOLIO_VOL_HIGH_PCT,
)
from stock_analyzer.risk_banner import risk_flags

OK = dict(beta=1.0, vol=15.0, sharpe=1.0, max_drawdown=-5.0)


def _f(**kw):
    return risk_flags(**{**OK, **kw})


def test_values_unchanged_from_old_literals():
    assert PORTFOLIO_BETA_CEILING == 1.4
    assert PORTFOLIO_VOL_HIGH_PCT == 30.0
    assert PORTFOLIO_SHARPE_WEAK_MAX == 0.5
    assert PORTFOLIO_DRAWDOWN_ACTION_MAX == -20.0


def test_clear():
    assert _f() == []


def test_beta_boundary_strict():
    assert _f(beta=1.4) == []
    assert len(_f(beta=1.4001)) == 1


def test_vol_boundary_inclusive():
    assert _f(vol=29.99) == []
    assert len(_f(vol=30.0)) == 1


def test_sharpe_boundary_strict():
    assert _f(sharpe=0.5) == []
    assert len(_f(sharpe=0.4999)) == 1


def test_drawdown_boundary_strict():
    assert _f(max_drawdown=-20.0) == []
    assert len(_f(max_drawdown=-20.01)) == 1


def test_none_raises_no_flag():
    assert risk_flags(None, None, None, None) == []


def test_all_four():
    assert len(risk_flags(2.0, 40.0, 0.1, -30.0)) == 4
