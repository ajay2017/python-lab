"""stock_analyzer/trade_caption.py -- the caption under the Analysis trade
buttons (S-D C4). Pure."""
import pytest

from stock_analyzer.trade_caption import context_caption

pytestmark = pytest.mark.fast


def _default(label, score):
    return f"Opens Trade Journal pre-filled with **{label} · {score}/100** as the signal context."


def _mismatch(label, score):
    return (f"Selling goes against today's {label} call ({score}/100). Trade Journal "
            "records that call as context; you choose whether it counts as following the signal.")


def test_buy_with_only_sell_button_is_mismatch():
    assert context_caption("Buy", 71.4, {"SELL"}) == _mismatch("Buy", "71")


def test_strong_buy_with_only_sell_button_is_mismatch():
    assert context_caption("Strong Buy", 88.0, {"SELL"}) == _mismatch("Strong Buy", "88")


def test_buy_with_both_buttons_is_default():
    assert context_caption("Buy", 71.0, {"BUY", "SELL"}) == _default("Buy", "71")


def test_buy_with_only_buy_button_is_default():
    assert context_caption("Buy", 71.0, {"BUY"}) == _default("Buy", "71")


def test_sell_with_sell_button_is_default():
    assert context_caption("Sell", 30.0, {"SELL"}) == _default("Sell", "30")


def test_hold_with_both_buttons_is_default():
    assert context_caption("Hold", 55.0, {"SELL", "BUY"}) == _default("Hold", "55")


def test_hold_trim_only_is_default_not_mismatch():
    assert context_caption("Hold", 55.0, {"SELL"}) == _default("Hold", "55")


def test_empty_shown_is_none():
    assert context_caption("Buy", 71.0, set()) is None


def test_non_numeric_score_does_not_raise():
    for bad in (None, float("nan"), "abc"):
        assert "—/100" in context_caption("Buy", bad, {"BUY"})
        assert "—/100" in context_caption("Buy", bad, {"SELL"})
