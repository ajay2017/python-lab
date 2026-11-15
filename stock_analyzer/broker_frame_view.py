"""
The "Net capital check" readout of the Robinhood-positions net capital (U7 plan
`docs/plans/net-capital-broker-frame.md`, chunks C2 + C3).

Pure text / row builder for the 💰 Account page and 🏠 Home's drift banner.
DISPLAY ONLY: it reads a `broker_frame.broker_frame(...)` result and the
journal's Total Account Value and returns strings and table rows. It decides
nothing and feeds no sizing, budget or gate; the frame's own decisions (pairing,
plausibility, drift tolerance) all live in `broker_frame`, and since C3
`effective_capital` sizes from the LOWER of the journal figure and that frame.
The wording says so: "Robinhood lower" means new-position sizes are capped at
Robinhood's figure, "Robinhood higher" means sizing keeps the journal figure.

Also holds three small pure helpers the Account page and Home call:
`cash_note_default` / `cash_note_to_save` (a hand-saved cash balance must not
keep the sync's note, which is what pairs cash with the positions snapshot) and
`drift_sizing_clause` (the sentence Home's drift banner appends when the
Robinhood figure binds).

Money is masked when `mask` is True (never a digit) and, in the caption, `$` is
escaped as `\\$` because `st.caption` renders markdown (two bare `$` render as
LaTeX). The detail rows go to `st.dataframe`, which is not markdown, so their `$`
is plain.
"""
from __future__ import annotations

import math

import pandas as pd

from stock_analyzer.broker_frame import (
    BROKER_CASH_NOTE,
    STATE_IMPLAUSIBLE,
    STATE_NO_CASH,
    STATE_NO_HOLDINGS,
    STATE_NO_PRICES,
    STATE_NO_SNAPSHOT,
    STATE_OK,
    STATE_UNPAIRED,
    STATE_UNREADABLE,
)
from stock_analyzer.market_time import ET

MASK = "••••••"

# The note a hand-saved cash balance carries when the typed note equals the
# sync's own (D4): the sync note is what pairs cash with the positions snapshot.
HAND_CASH_NOTE = "Entered by hand"

# A gap under one dollar is rounding, not a difference between the two books.
_SAME_BELOW_USD = 1.0

# "Not compared" reason per non-ok frame state (every state broker_frame returns).
_REASON = {
    STATE_NO_SNAPSHOT: "no positions snapshot saved yet",
    STATE_UNREADABLE: "the positions snapshot couldn't be read",
    STATE_NO_CASH: "no cash record",
    STATE_NO_HOLDINGS: "holdings couldn't be read",
    STATE_UNPAIRED: "positions and cash weren't saved by the same sync",
    STATE_IMPLAUSIBLE: "Robinhood's snapshot looks incomplete",
    STATE_NO_PRICES: "no prices for your holdings yet",
}
_REASON_HAND_CASH = "cash was entered by hand"

_UNSET = object()


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _usd(v: float, *, signed: bool = False) -> str:
    """'$1,234' / '-$1,234' (no cents); signed=True adds a leading '+'."""
    r = round(v)
    if r < 0:
        return f"-${abs(r):,.0f}"
    return f"+${r:,.0f}" if signed and r > 0 else f"${r:,.0f}"


def _money(v: float, mask: bool, *, signed: bool = False) -> str:
    return MASK if mask else _usd(v, signed=signed)


def _esc(s: str) -> str:
    """Escape `$` for markdown."""
    return s.replace("$", "\\$")


def _shares(x) -> str:
    """Shares without trailing zeros: 5, 0.25."""
    v = _num(x)
    if v is None:
        return "—"
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s or "0"


def _et_stamp(captured_at) -> "str | None":
    """'Fri Nov 13, 11:19 AM ET' from a tz-aware stamp (UTC or any zone), else
    None. A naive value is read as UTC (every stamp the app writes is tz-aware)."""
    if captured_at is None:
        return None
    try:
        t = pd.to_datetime(captured_at, utc=True)
        if pd.isna(t):
            return None
        d = t.tz_convert(ET)
    except Exception:
        return None
    h12 = (d.hour % 12) or 12
    return (f"{d:%a %b} {d.day}, {h12}:{d.minute:02d} "
            f"{'AM' if d.hour < 12 else 'PM'} ET")


def _not_compared(reason: str) -> dict:
    return {"caption": ("🧪 Net capital check: Robinhood positions "
                        f"not compared — {reason} (sizing uses the journal figure)"),
            "detail_rows": [], "detail_title": None}


def _reason(frame, cash_note) -> str:
    state = frame.get("state") if isinstance(frame, dict) else None
    if state == STATE_UNPAIRED and cash_note is not _UNSET \
            and str(cash_note or "").strip() != BROKER_CASH_NOTE:
        return _REASON_HAND_CASH
    if state in _REASON:
        return _REASON[state]
    if frame is None:
        return "no check was available"
    return "the check returned an unexpected result"


def _detail(frame, mask: bool) -> "tuple[list, str | None]":
    rows = []
    diffs, unpriced = frame.get("rows"), frame.get("unpriced")
    for r in (diffs if diffs is not None else []):
        dollars = _num(r.get("dollars"))
        price = _num(r.get("price"))
        rows.append({
            "Ticker": str(r.get("ticker", "?")),
            "Journal shares": _shares(r.get("journal_shares")),
            "Robinhood shares": _shares(r.get("broker_shares")),
            "Price": ("—" if price is None
                      else MASK if mask else f"${price:,.2f}"),
            "Value of difference (journal − Robinhood)":
                "—" if dollars is None else _money(dollars, mask, signed=True),
        })
    for tk in (unpriced if unpriced is not None else []):
        rows.append({
            "Ticker": str(tk),
            "Journal shares": "—",
            "Robinhood shares": "—",
            "Price": "no price",
            "Value of difference (journal − Robinhood)": "no price — left out",
        })
    # A split logged after (or at an unknown time relative to) the sync: the two
    # share counts sit on different sides of it, so `broker_frame` left the name out.
    excluded = frame.get("excluded")
    for r in (excluded if excluded is not None else []):
        rows.append({
            "Ticker": str(r.get("ticker", "?")),
            "Journal shares": _shares(r.get("journal_shares")),
            "Robinhood shares": _shares(r.get("broker_shares")),
            "Price": "—",
            "Value of difference (journal − Robinhood)":
                "split since the sync — left out",
        })
    if not rows:
        return [], None
    n = len(rows)
    return rows, f"Where the two figures differ ({n} name{'s' if n != 1 else ''})"


def shadow_lines(frame, journal_total, *, mask, cash_note=_UNSET) -> dict:
    """{caption, detail_rows, detail_title} for the Account page's net capital check.

    frame : `broker_frame.broker_frame(...)`'s result (or None).
    journal_total : invested equity + net cash, the Account page's "Total Account
        Value" tile (net capital when levered).
    mask : privacy mode; every dollar figure reads "••••••" and no money digit
        is returned.
    cash_note : the cash record's `note`, optional. Lets an "unpaired" frame say
        the cash was entered by hand rather than "not saved by the same sync".

    caption is markdown-safe (`$` escaped). detail_rows are plain-text rows for a
    dataframe (empty unless the frame is "ok" and at least one name differs or
    was left out: no price, or a split since the sync).
    """
    if not isinstance(frame, dict) or frame.get("state") != STATE_OK:
        return _not_compared(_reason(frame, cash_note))
    adjust, jt = _num(frame.get("adjust")), _num(journal_total)
    if adjust is None:
        return _not_compared("the check returned an unexpected result")
    if jt is None:
        return _not_compared("the journal total was unavailable")

    broker_total = jt - adjust
    text = ("🧪 Net capital check: "
            f"journal {_money(jt, mask)} · Robinhood positions "
            f"{_money(broker_total, mask)}")
    if abs(adjust) < _SAME_BELOW_USD:
        text += " · same"
    elif adjust > 0:
        text += (f" · gap {_money(abs(adjust), mask)} (Robinhood lower — "
                 "new-position sizes are capped at Robinhood's figure)")
    else:
        text += (f" · gap {_money(abs(adjust), mask)} (Robinhood higher — "
                 "sizing keeps the journal figure)")
    stamp = _et_stamp(frame.get("captured_at"))
    if stamp:
        text += f" · positions synced {stamp}"

    rows, title = _detail(frame, mask)
    return {"caption": _esc(text), "detail_rows": rows, "detail_title": title}


def drift_sizing_clause(frame) -> "str | None":
    """The sentence Home's drift banner appends in its mismatch branch when
    Robinhood's positions say LESS than the journal (so sizing is already capped
    at Robinhood's figure), else None. Plain text, no money figure. Only an "ok"
    frame whose `adjust` is at least a dollar counts: a not-compared, same or
    Robinhood-higher frame says nothing."""
    if not isinstance(frame, dict) or frame.get("state") != STATE_OK:
        return None
    adjust = _num(frame.get("adjust"))
    if adjust is None or adjust < _SAME_BELOW_USD:
        return None
    return "New-position sizes are capped at Robinhood's lower figure until this is fixed."


def cash_note_default(stored) -> str:
    """The cash-note field's pre-filled value: blank over the sync's own note, so a
    hand-saved balance doesn't inherit it (D4); anything else unchanged."""
    text = "" if stored is None else str(stored)
    return "" if text.strip() == BROKER_CASH_NOTE else text


def cash_note_to_save(typed) -> "str | None":
    """The note to persist with a hand-saved cash balance: None when blank,
    `HAND_CASH_NOTE` when the typed note equals the sync's own (so it can't pass
    as a synced balance), else the stripped text."""
    text = "" if typed is None else str(typed).strip()
    if not text:
        return None
    return HAND_CASH_NOTE if text == BROKER_CASH_NOTE else text
