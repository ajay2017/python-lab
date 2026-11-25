"""Pure display formatters: share plural, signed dollars, signed counts."""
import math

from stock_analyzer.text_fmt import (
    TRADE_HISTORY_GAP_NOTE, share_word, signed_count, signed_dollars,
)


def test_share_word_singular_only_for_exactly_one():
    assert share_word(1) == "share"
    assert share_word(1.0) == "share"
    assert share_word("1") == "share"
    assert share_word(2) == "shares"
    assert share_word(0) == "shares"
    assert share_word(1.5) == "shares"
    assert share_word(None) == "shares"
    assert share_word("x") == "shares"


def test_signed_dollars_puts_the_sign_before_the_dollar():
    assert signed_dollars(80) == "+$80"
    assert signed_dollars(-198) == "-$198"
    assert signed_dollars(1234.5, 2) == "+$1,234.50"
    assert signed_dollars(-1234.5, 2) == "-$1,234.50"


def test_signed_dollars_zero_and_negative_zero_are_plain():
    assert signed_dollars(0) == "$0"
    assert signed_dollars(-0.2) == "$0"       # rounds to zero at 0 dp
    assert signed_dollars(0.004, 2) == "$0.00"
    assert signed_dollars(-0.0, 2) == "$0.00"


def test_signed_dollars_unreadable_is_a_dash_not_nan():
    assert signed_dollars(None) == "—"
    assert signed_dollars(float("nan")) == "—"
    assert signed_dollars(math.inf) == "—"
    assert signed_dollars("abc") == "—"


def test_signed_count_never_prints_minus_zero():
    assert signed_count(-3, "downgrades") == "-3 downgrades"
    assert signed_count(-0.0, "downgrades") == "0 downgrades"
    assert signed_count(-0.2, "downgrades") == "0 downgrades"
    assert signed_count(None, "downgrades") == "— downgrades"


def test_trade_history_gap_note_text():
    assert "before mid-August 2026 is incomplete" in TRADE_HISTORY_GAP_NOTE
    assert "1099-B is the tax figure" in TRADE_HISTORY_GAP_NOTE
    assert "$" not in TRADE_HISTORY_GAP_NOTE      # no LaTeX pairs in markdown


def test_macro_playbook_singular_share():
    # "1 shares" regression through a real call path is covered by share_word;
    # this pins the helper used by macro_playbook / notify / app.
    assert f"sell 1 {share_word(1)}" == "sell 1 share"
    assert f"sell 3 {share_word(3)}" == "sell 3 shares"


def test_plural_singular_only_for_one():
    from stock_analyzer.text_fmt import plural
    assert plural(1, "downgrade") == "1 downgrade"
    assert plural(0, "downgrade") == "0 downgrades"
    assert plural(3, "upgrade") == "3 upgrades"
    assert plural(None, "upgrade") == "— upgrades"


def test_mask_plain_usd_hides_every_dollar_figure():
    # Hide values (owner 2026-11-25): the 🔔 Catalyst Watch playbook text quotes
    # the position's dollars; every figure is masked, the words around it stay.
    from stock_analyzer.text_fmt import mask_plain_usd
    txt = ("An estimated ±16% earnings move on a $1,355 position = ±$217 in a single "
           "session. **Trim to ≈10% weight: sell 2 shares (≈$542)** before earnings. "
           "Stop $253.39, worst day -$45.")
    out = mask_plain_usd(txt)
    assert "$" not in out
    assert "a •••••• position = •••••• in" in out
    assert "(≈••••••)" in out
    assert "Stop ••••••, worst day ••••••." in out
    assert "±16%" in out and "≈10% weight" in out          # percentages untouched
    assert "**Trim to" in out                               # markdown kept for _md_bold


def test_mask_plain_usd_edge_inputs():
    from stock_analyzer.text_fmt import mask_plain_usd
    assert mask_plain_usd(None) == ""
    assert mask_plain_usd("no money here, 15% weight") == "no money here, 15% weight"
    assert mask_plain_usd("costs $5", mask="X") == "costs X"
    assert mask_plain_usd("a lone $ sign") == "a lone $ sign"
