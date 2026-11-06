"""Decide whether a Trade Journal holdings rebuild may run, and whether it may
save an EMPTY book (bug register HoldWipe).

An empty `save_holdings` sweeps every holdings row AND every manual stop
(owner-entered, can't be rebuilt). The lenient `db.load_trades()` returns an
empty frame on a failed read, so a rebuild fed from it could wipe both. The
callers read with the strict `db.load_trades_or_none()` and pass the result
here; `allow_empty` is bound to the read so no caller can pass a bare True.
"""
from __future__ import annotations

import pandas as pd


def decide(fresh: "pd.DataFrame | None", *, rows_just_inserted: bool) -> dict:
    """Return {"action": "unreadable" | "empty" | "rebuild", "allow_empty": bool}.

    - `fresh is None`: the journal couldn't be read -> "unreadable"; never
      rebuild, never save.
    - empty frame right after an insert (`rows_just_inserted`): an empty
      journal can't be true when we just wrote a row -> treated as
      "unreadable", not as an empty book.
    - empty frame, nothing inserted: a genuinely empty journal -> "empty";
      nothing to replay, holdings left as they are (`allow_empty` False).
    - non-empty frame: "rebuild". `allow_empty` is True because a successful
      read of a non-empty journal that replays to zero shares is a real
      sell-everything.
    """
    if fresh is None:
        return {"action": "unreadable", "allow_empty": False}
    if fresh.empty:
        if rows_just_inserted:
            return {"action": "unreadable", "allow_empty": False}
        return {"action": "empty", "allow_empty": False}
    return {"action": "rebuild", "allow_empty": True}


# ── RbPartial: hold on mismatch ──────────────────────────────────────────────
# A replay of the journal is only as complete as the journal. When it can't
# account for a held position (history before mid-Aug 2026 is missing, a
# deleted SELL of a stock whose BUYs predate the journal, a manual holdings
# edit) a blind `save_holdings(replay)` silently drops or changes it and sweeps
# its manual stop. The auto-save sites therefore save ONLY when the journal
# explains the book: current holdings +/- the effect of exactly the trades just
# deleted / imported must equal the replay for every ticker.

_SHARE_EPS = 1e-6  # float noise, same epsilon db.recalculate_from_trades uses; not a policy threshold


def _tk(v) -> str:
    return str(v if v is not None else "").upper().strip()


def _shares_by_ticker(df: "pd.DataFrame | None") -> dict:
    """{TICKER: summed shares} from a holdings-shaped frame (None/empty -> {})."""
    out: dict = {}
    if df is None or len(df) == 0 or "Ticker" not in df.columns or "Shares" not in df.columns:
        return out
    shares = pd.to_numeric(df["Shares"], errors="coerce").fillna(0.0)
    for tk, sh in zip(df["Ticker"], shares):
        t = _tk(tk)
        if t:
            out[t] = out.get(t, 0.0) + float(sh)
    return out


def replay_check(current: "pd.DataFrame | None", replay: "pd.DataFrame",
                 changed_trades: "pd.DataFrame | list[dict]", *, mode: str) -> dict:
    """Does the journal explain the book? Return
    {"safe": bool, "reason": None | "mismatch" | "holdings_unreadable" |
     "unpredictable_trade" | "unreadable_trade", "mismatches": [{"ticker","held","expected","replay"}]}.

    - `current`: a FRESH holdings read; None (unreadable) -> not safe.
    - `replay`: `recalculate_from_trades(...)["holdings_df"]`.
    - `changed_trades`: the rows just deleted (mode="delete") or imported
      (mode="import"), each with ticker / action / shares.
    - expected shares = current +/- the changed trades (BUY +, SELL -; negated
      for "delete"). A SPLIT (or any non-BUY/SELL action) can't be predicted ->
      "unpredictable_trade"; a row with no usable ticker or share count ->
      "unreadable_trade".
    A ticker is a mismatch when |expected - replay| > _SHARE_EPS; a ticker
    absent from one side counts as 0 shares.
    """
    if mode not in ("delete", "import"):
        raise ValueError(f"mode must be 'delete' or 'import', got {mode!r}")
    if current is None:
        return {"safe": False, "reason": "holdings_unreadable", "mismatches": []}

    held = _shares_by_ticker(current)
    expected = dict(held)
    if isinstance(changed_trades, pd.DataFrame):
        rows = changed_trades.to_dict("records")
    else:
        rows = [] if changed_trades is None else list(changed_trades)
    sign = -1.0 if mode == "delete" else 1.0
    for r in rows:
        r = {} if r is None else r
        action = str(r.get("action", "")).upper().strip()
        if action not in ("BUY", "SELL"):  # SPLIT / other: can't be predicted
            return {"safe": False, "reason": "unpredictable_trade", "mismatches": []}
        t = _tk(r.get("ticker"))
        try:
            sh = float(r.get("shares"))
        except (TypeError, ValueError):
            sh = float("nan")
        if not t or sh != sh:  # no usable ticker / share count
            return {"safe": False, "reason": "unreadable_trade", "mismatches": []}
        delta = sh if action == "BUY" else -sh
        expected[t] = expected.get(t, 0.0) + sign * delta

    rep = _shares_by_ticker(replay)
    mismatches = []
    for t in sorted(set(expected) | set(rep)):
        e, p = expected.get(t, 0.0), rep.get(t, 0.0)
        if abs(e - p) > _SHARE_EPS:
            mismatches.append({"ticker": t, "held": held.get(t, 0.0),
                               "expected": e, "replay": p})
    return {"safe": not mismatches,
            "reason": None if not mismatches else "mismatch",
            "mismatches": mismatches}


def diff_holdings(current: "pd.DataFrame | None", replay: "pd.DataFrame") -> "dict | None":
    """What applying `replay` over `current` would change, for the manual
    preview: {"removed": [tickers held, absent from replay], "added": [in
    replay, not held], "changed": [{"ticker","held","replay"}]}, all sorted.
    `current is None` (unreadable) -> None."""
    if current is None:
        return None
    held, rep = _shares_by_ticker(current), _shares_by_ticker(replay)
    removed = sorted(t for t in held if t not in rep)
    added = sorted(t for t in rep if t not in held)
    changed = [{"ticker": t, "held": held[t], "replay": rep[t]}
               for t in sorted(set(held) & set(rep))
               if abs(held[t] - rep[t]) > _SHARE_EPS]
    return {"removed": removed, "added": added, "changed": changed}


def same_holdings(a: "pd.DataFrame | None", b: "pd.DataFrame | None") -> bool:
    """True when two holdings frames carry the same tickers with the same
    shares (within _SHARE_EPS). Used to confirm an Apply matches its preview."""
    if a is None or b is None:
        return False
    sa, sb = _shares_by_ticker(a), _shares_by_ticker(b)
    return all(abs(sa.get(t, 0.0) - sb.get(t, 0.0)) <= _SHARE_EPS
               for t in set(sa) | set(sb))


def hold_message(ctx: str, check: dict) -> str:
    """Plain-text (markdown `**bold**`, NOT HTML) message for st.warning when
    an auto-rebuild was held back. `ctx` is e.g. "Deleted 2 trades"."""
    reason = check.get("reason")
    if reason == "holdings_unreadable":
        return (f"⚠️ {ctx}. Holdings were not rebuilt: the current holdings couldn't "
                "be read to check the rebuild against. Nothing was changed; try "
                "⚠️ Fix drift between trades and holdings → Run rebuild now in a minute.")
    if reason == "unpredictable_trade":
        return (f"⚠️ {ctx}. Holdings were not rebuilt automatically because a split "
                "(or other non-BUY/SELL row) is involved. Nothing was changed; use "
                "⚠️ Fix drift between trades and holdings → Run rebuild now to "
                "preview and apply.")
    if reason == "unreadable_trade":
        return (f"⚠️ {ctx}. Holdings were not rebuilt automatically because a deleted "
                "or imported row has no usable ticker or share count. Nothing was "
                "changed; use ⚠️ Fix drift between trades and holdings → Run rebuild "
                "now to preview and apply.")
    if reason == "capture_failed":
        return (f"⚠️ {ctx}. Holdings were not rebuilt automatically: the deleted rows "
                "couldn't be matched to the trade history on screen, so the rebuild "
                "couldn't be checked. Nothing was changed; use ⚠️ Fix drift between "
                "trades and holdings → Run rebuild now to preview and apply.")
    _mm = check.get("mismatches")
    mm = [] if _mm is None else list(_mm)
    names = ", ".join(
        f"{m['ticker']} (held {m['held']:g}; after this change it should be "
        f"{m['expected']:g}; the trade history gives {m['replay']:g})"
        for m in mm[:5]
    )
    if len(mm) > 5:
        names += f", and {len(mm) - 5} more"
    return (f"⚠️ {ctx}. Holdings were **not** rebuilt automatically: the trade history "
            f"doesn't explain the current holdings for {names}. Nothing was changed "
            "(holdings, manual stops and realized P&L are as they were). Open "
            "📒 Trade Journal → ⚠️ Fix drift between trades and holdings → Run "
            "rebuild now to preview the change before applying it.")
