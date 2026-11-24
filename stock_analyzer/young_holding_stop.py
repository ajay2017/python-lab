"""Gate module: what a holding with NO automatic stop yet is told (new listings).

`held_stop.compute_held_stop` returns no stop below `HELD_STOP_MIN_BARS`
completed bars (the stock's whole price history, not the time held), so every
holding bought within that many sessions of its listing has no automatic stop.
Before this module two Home notices disagreed about it (the composite-withheld
notice said a stop protects it; the stop warning said none could be built).

Everything here is pure (no streamlit, no I/O) so the app, the cron email and
the composite-withheld notice read ONE decision:

  stop_state(held_stop_result, manual_row) -> {"state", "manual_price", "bars",
      "need", "more", "reason"}
      state "auto"   the app built an automatic stop;
            "manual" no automatic stop, a valid manual stop is in force;
            "none"   no stop at all.
      The manual rule is `held_stop.compose_with_manual` (never re-derived), so a
      manual stop that is looser than an existing automatic one leaves the state
      "auto". `bars` / `need` come from the result's additive fields (None when it
      carries none); `more` = need - bars (never negative); `reason` is
      "short_history" when the bar count is below the floor, "other" otherwise
      (a failed build, no price history), None for "auto".
  split_stop_rows(held_stops, manual_stops) -> {"none": [...], "manual": [...],
      "approx": [(ticker, label, note), ...]}   (the Home stop warning's parts)
  plain_notes(held_stops, manual_stops) -> [str]  (the pre-market email footer)
  none_notice_markdown / manual_notice_markdown   (Home, `\\$` escaped)
  validate_manual_stop(price, current_price) -> (ok, message, pct_below)
  form_* text for the Home "set your own stop" form.

`manual_stops` is `db.load_manual_stops_or_none`'s result: a dict, `{}` = read and
none set, `None` = couldn't be read. On `None`, `split_stop_rows` treats every row
as having no manual stop (the table is unread, so nothing is claimed in force) and
`plain_notes` returns today's `degraded_notes` lines unchanged.

No threshold lives here: the floor is the result's own `need`.
"""
from __future__ import annotations

import math

STATE_AUTO = "auto"
STATE_MANUAL = "manual"
STATE_NONE = "none"

REASON_SHORT_HISTORY = "short_history"
REASON_OTHER = "other"

# Shown when the only thing known is that no stop exists (a result that was
# never built, or a failed build with no note).
_NO_RESULT_NOTE = "No stop could be built for this holding."


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _count(v) -> int | None:
    """A non-negative int count, else None (a bool is not a count)."""
    if isinstance(v, bool):
        return None
    x = _num(v)
    if x is None or x < 0 or x != int(x):
        return None
    return int(x)


def md_dollars(text: str) -> str:
    """Escape each `$` for Streamlit markdown (`$a ... $b` would render as LaTeX)."""
    return str(text).replace("$", "\\$")


def _sessions(n: int) -> str:
    return f"{n} session{'' if n == 1 else 's'}"


def _usd(x: float) -> str:
    return f"${x:.2f}"


def _manual_price(manual_row) -> float | None:
    mp = manual_row.get("stop_price") if isinstance(manual_row, dict) else manual_row
    return _num(mp)


def stop_state(held_stop_result, manual_row) -> dict:
    """See the module docstring. `held_stop_result` is one `build_held_stops`
    value (anything else reads as "no automatic stop, nothing known"); `manual_row`
    is a manual_stops row, a bare price or None."""
    # Lazy: held_stop imports portfolio, which imports this module inside its loop.
    from stock_analyzer.held_stop import compose_with_manual

    res = held_stop_result if isinstance(held_stop_result, dict) else {}
    auto = _num(res.get("stop"))
    if auto is not None and not auto > 0:
        auto = None
    stop, is_manual = compose_with_manual(auto, _manual_price(manual_row))
    bars, need = _count(res.get("bars")), _count(res.get("need"))
    more = max(need - bars, 0) if bars is not None and need is not None else None
    if auto is not None:
        return {"state": STATE_AUTO, "manual_price": None, "bars": bars,
                "need": need, "more": more, "reason": None}
    short = bars is not None and need is not None and bars < need
    reason = REASON_SHORT_HISTORY if short else REASON_OTHER
    if is_manual and stop is not None:
        return {"state": STATE_MANUAL, "manual_price": float(stop), "bars": bars,
                "need": need, "more": more, "reason": reason}
    return {"state": STATE_NONE, "manual_price": None, "bars": bars,
            "need": need, "more": more, "reason": reason}


def _full_note(res) -> str:
    # Lazy for the same reason as above; the note plus the saved-floor sentence.
    from stock_analyzer.held_stop_wiring import full_note
    note = full_note(res)
    return note if note.strip() else _NO_RESULT_NOTE


def _rows(held_stops, manual_stops) -> list[dict]:
    """One classified row per ticker, sorted by ticker. `manual_stops` None reads
    as `{}` here (callers that must not claim a manual stop pass None and handle
    it themselves)."""
    from stock_analyzer.held_stop_wiring import degraded
    if not isinstance(held_stops, dict):
        return []
    ms = manual_stops if isinstance(manual_stops, dict) else {}
    out: list[dict] = []
    for t, res in sorted(held_stops.items(), key=lambda kv: str(kv[0])):
        ticker = str(t).strip().upper()
        if not ticker:
            continue
        st = stop_state(res, ms.get(ticker))
        row = {"ticker": ticker, **st}
        if st["state"] == STATE_AUTO:
            if not degraded(res):
                continue
            row["kind"] = "approx"
            row["label"] = str((res or {}).get("label") or "")
            row["note"] = _full_note(res)
        else:
            row["kind"] = "manual" if st["state"] == STATE_MANUAL else "none"
            row["note"] = _full_note(res)
        out.append(row)
    return out


def split_stop_rows(held_stops, manual_stops) -> dict:
    """The Home stop warning's three parts (each shown only when it has rows):
    "none" (no stop at all), "manual" (no automatic stop, the owner's is in force)
    and "approx" ([(ticker, label, note)] for an automatic stop on a partial /
    prior-close basis). An unavailable automatic stop is never in "approx"."""
    out = {"none": [], "manual": [], "approx": []}
    for row in _rows(held_stops, manual_stops):
        if row["kind"] == "approx":
            out["approx"].append((row["ticker"], row["label"], row["note"]))
        else:
            out[row["kind"]].append(row)
    return out


# ── Home text (markdown) ─────────────────────────────────────────────────────
def _none_row_line(row: dict) -> str:
    if row["reason"] == REASON_SHORT_HISTORY:
        what = (f"only {row['bars']} completed session{'' if row['bars'] == 1 else 's'} "
                f"({row['need']} needed, {row['more']} more)")
    else:
        # not a too-few-bars reason: keep the build's own note text
        # held-stop notes are already escaped for markdown: unescape first so a
        # `\$` in a note never prints as a literal backslash
        from stock_analyzer.held_stop_wiring import plain_text_note
        note = plain_text_note(str(row.get("note") or "")).strip() or _NO_RESULT_NOTE
        what = md_dollars(note.rstrip(".!?"))
    return (f"- **{row['ticker']}**: {what}. Set your own stop below, "
            "or place one at your broker.")


def none_notice_markdown(rows) -> str | None:
    """Part 1 of the Home stop warning (an st.warning), or None without rows."""
    rows = list(rows or [])
    if not rows:
        return None
    n = len(rows)
    head = (f"⚠️ **No stop yet for {n} holding{'' if n == 1 else 's'}.** Nothing in the "
            f"app will tell you to exit {'it' if n == 1 else 'them'} until a stop exists:")
    return head + "\n\n" + "\n".join(_none_row_line(r) for r in rows)


def manual_notice_markdown(row: dict) -> str:
    """Part 2 for one ticker (an st.info): the owner's stop is in force."""
    price = md_dollars(_usd(row["manual_price"]))
    if row["reason"] == REASON_SHORT_HISTORY:
        tail = (f"The app builds its own stop after {row['more']} more "
                f"session{'' if row['more'] == 1 else 's'}; "
                "yours stays in force only while it is the higher of the two.")
    else:
        tail = "The app could not build its own stop."
    return f"ℹ️ **{row['ticker']}**: your manual stop {price} is in force. {tail}"


# ── pre-market email footer (plain text) ─────────────────────────────────────
def _plain_line(row: dict) -> str:
    from stock_analyzer.held_stop_wiring import plain_text_note
    t = row["ticker"]
    if row["kind"] == "approx":
        return f"{t} ({row['label']}): {plain_text_note(row['note'])}"
    if row["kind"] == "manual":
        price = _usd(row["manual_price"])
        if row["reason"] == REASON_SHORT_HISTORY:
            return (f"{t}: your manual stop {price} is in force (no automatic stop "
                    f"for {row['more']} more session{'' if row['more'] == 1 else 's'}).")
        return f"{t}: your manual stop {price} is in force (no automatic stop could be built)."
    if row["reason"] == REASON_SHORT_HISTORY:
        why = f"{row['bars']} of {row['need']} sessions"
    else:
        why = plain_text_note(row["note"]).strip().rstrip(".!?")
    return (f"{t}: no stop yet ({why}). Nothing will flag an exit until one exists; "
            "set one on 🏠 Home or at your broker.")


def plain_notes(held_stops, manual_stops) -> list[str]:
    """One plain-text line per ticker that needs a word (no automatic stop, or an
    automatic stop on a limited basis), sorted by ticker. `manual_stops` None (the
    table couldn't be read) returns today's `T (label): note` lines for every
    degraded result, so nothing is claimed about a manual stop that was not read."""
    if manual_stops is None:
        from stock_analyzer.held_stop_wiring import degraded_notes
        return [f"{t} ({label}): {note}" for t, label, note in degraded_notes(held_stops)]
    return [_plain_line(r) for r in _rows(held_stops, manual_stops)]


# ── the manual-stop form (Home) ──────────────────────────────────────────────
def validate_manual_stop(price, current_price) -> tuple[bool, str, float | None]:
    """(ok, message, pct_below) for a stop the owner typed. Both prices are taken
    to the cent (the saved precision), so a stop that rounds to today's price counts
    as at it. Refuses blank / zero / negative / non-finite, and a stop at or above
    today's price (it would trigger at once). No other bound. `message` is plain
    text with `$` (escape for markdown with `md_dollars`); "" when ok. `pct_below`
    is the % under today's price, for the information line, only when ok."""
    p = _num(price)
    if p is None or round(p, 2) <= 0:
        return False, "Enter a stop price above $0.", None
    cur = _num(current_price)
    if cur is None or round(cur, 2) <= 0:
        return (False, "Today's price isn't available, so this stop can't be checked "
                       "against it yet.", None)
    p, cur = round(p, 2), round(cur, 2)
    if p >= cur:
        return (False, f"A stop at or above today's price (${cur:.2f}) would trigger at "
                       f"once. Enter a price below ${cur:.2f}.", None)
    return True, "", (cur - p) / cur * 100.0


def form_label(ticker: str) -> str:
    return f"✋ Set your own stop for {ticker}"


def form_caption() -> str:
    from stock_analyzer.constants import HELD_STOP_MIN_BARS
    return ("The app advises; your broker executes. Enter the stop you placed (or want "
            f"the app to watch). From session {int(HELD_STOP_MIN_BARS)} the app builds its "
            "own stop; yours stays in force only while it is the higher of the two.")


FORM_PRICE_LABEL = "Stop price ($)"
FORM_NOTE_LABEL = "Note (optional)"
FORM_SAVE_LABEL = "💾 Save stop"
FORM_CLEAR_LABEL = "↩️ Clear my stop"
FORM_CLEAR_CONFIRM_LABEL = "Yes, remove my stop"
FORM_READONLY_HELP = "Read-only viewer — changes are disabled"
FORM_SOURCE_ACTION = "stop_unavailable"


def clear_confirm_markdown(row: dict) -> str:
    """The confirmation shown (an st.warning) before "Clear my stop" removes the
    owner's only stop on a holding that has no automatic one. `row` is a "manual"
    row from `split_stop_rows`."""
    t = row["ticker"]
    price = md_dollars(_usd(row["manual_price"]))
    head = f"Remove your stop {price} for {t}?"
    more = _count(row.get("more"))
    if row.get("reason") == REASON_SHORT_HISTORY and more is not None:
        return (f"{head} {t} has no automatic stop yet, so nothing in the app will tell "
                "you to exit it until you set one again or the app builds its own "
                f"(after {more} more session{'' if more == 1 else 's'}).")
    return (f"{head} The app could not build its own stop for {t}, so nothing in the "
            "app will tell you to exit it until you set one again.")


def form_pct_text(pct_below: float, current_price: float) -> str:
    """The information line shown before saving a valid entry (plain text)."""
    return f"That is {pct_below:.1f}% below today's price (${current_price:.2f})."


def form_saved_text(ticker: str, price: float) -> str:
    return f"Stop ${price:.2f} saved for {ticker}."
