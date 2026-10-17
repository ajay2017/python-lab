"""📈 Analysis: holder-only wording/buttons are gated on the one normalized held
test (`_sa_holding`).  Display-only; app.py is not imported, so each gate is
pinned structurally against the Analysis page slice.
"""
import pathlib

import pytest

pytestmark = pytest.mark.fast

APP = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")


def _src():
    a = APP.index('elif page == "📈 Analysis":')
    return APP[a:APP.index('elif page == "📋 Watchlist":')]


def test_non_held_sell_gets_a_caption_not_a_sell_button():
    s = _src()
    i = s.index("elif _sa_is_sell and not _sa_holding:")
    j = s.index("elif _sa_is_sell:", i)
    blk = s[i:j]
    assert "nothing to sell" in blk and "_render_trade_button(" not in blk
    # the held Sell branch is unchanged and follows the gate
    held = s[j:s.index("elif _sa_is_hold:", j)]
    assert 'label=f"📒 Sell {ticker}"' in held


def test_trim_button_is_holder_only_and_add_button_is_unchanged():
    s = _src()
    hold = s[s.index("elif _sa_is_hold:", s.index("elif _sa_is_sell:\n                    # Sell signal")):]
    hold = hold[:hold.index("with _ap_c2:")]
    assert "if _sa_holding:" in hold
    assert hold.index("if _sa_holding:") < hold.index('label=f"📒 Trim {ticker}"')
    add = s[s.index('label=f"📒 Add {ticker}"') - 600:s.index('label=f"📒 Add {ticker}"')]
    assert 'suggested_action="BUY"' in add


def test_downside_if_you_hold_block_is_holder_only():
    s = _src()
    i = s.index('st.markdown("#### Downside Risk — If You Hold")')
    guard = s[s.rindex("\n", 0, s.rindex("if targets", 0, i)) + 1:i]
    assert "if targets and _sa_holding:" in guard
    # the ATR downside caption above it stays ungated by held status
    assert "ATR downside level" in s[:i]


def test_consider_smaller_entry_only_on_a_sized_buy():
    s = _src()
    i = s.index('targets.get("above_consensus")')
    blk = s[i:s.index("earn = r[\"earnings\"]", i)]
    gate = "if not (_sa_is_sell or _sa_is_hold or _da_held_back):"
    assert gate in blk
    warn = blk[blk.index(gate):blk.index("else:")]
    assert "Consider smaller entry" in warn
    neutral = blk[blk.index("else:"):]
    assert "Consider smaller entry" not in neutral
    assert "above analyst consensus (\\\\${at:.2f})" in neutral
