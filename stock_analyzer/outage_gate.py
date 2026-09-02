"""The DB-outage render gate — extracted from app.py 2026-08-25.

Queued as an F-243 reviewer non-blocking finding (2026-08-17): the allowlist
application, scope branching, and message text lived inline in app.py, so
none of it was unit-testable. Pure functions only — no Streamlit, no
session_state, no I/O. `app.py` calls `decide()` and renders whatever it
returns; the decision itself lives here.
"""
from __future__ import annotations


def decide(fail_rec: "dict | None", page: str,
           safe_pages: "tuple[str, ...]") -> "tuple[str, str | None]":
    """Outage-gate verdict for rendering `page`, given the current
    `db.classify_load_result()` record (or None = no outage).

    Returns `(verdict, message)`:
      - `("none", None)`  — no outage active; render `page` normally with no
                             mention of the database at all.
      - `("info", msg)`    — `page` is in `safe_pages` and doesn't depend on
                             holdings, so it renders normally, but a holdings
                             outage IS active elsewhere in the app right now.
                             Added 2026-09-01 (F-243 cosmetic follow-up): before
                             this, a safe page during a real outage looked
                             identical in the main body to a healthy session —
                             the sidebar dot was the only signal, easy to miss
                             on a page like 📖 User Guide.
      - `("warn", msg)`    — render `page`, but show `msg` as a soft warning
                             first (holdings are correct; other surfaces may
                             look emptier than they are).
      - `("stop", msg)`    — do NOT render `page`'s normal body; show `msg`
                             as a hard error instead (the book itself would
                             be misrepresented).

    `page in safe_pages` always renders normally regardless of scope — those
    pages (System Trust, User Guide) must stay reachable so the outage has a
    diagnostic route and the app never strands the user on a blank screen.
    Callers should pass a `safe_pages` tuple already scoped to what the
    CURRENT viewer can actually reach (e.g. excluding an owner-only page for
    a read-only viewer) — the "still available" text in the stop message is
    built directly from this tuple, and naming an unreachable page would be a
    dead pointer.

    An unrecognized `scope` (should not occur; defensive only) falls through
    to `("none", None)` rather than either extreme — same posture as the
    original inline `if/elif` with no trailing `else`.
    """
    if not fail_rec:
        return ("none", None)
    scope = fail_rec.get("scope")
    if scope == "holdings":
        if page in safe_pages:
            return ("info", (
                "ℹ️ The database is currently unreachable, so pages showing "
                "your portfolio are temporarily blocked elsewhere in the app. "
                "This page doesn't depend on your holdings, so it renders "
                "normally."
            ))
        return ("stop", (
            "⛔ **Cannot reach the database — your portfolio is NOT shown below.**\n\n"
            f"Reason: {fail_rec.get('detail', 'unknown')}\n\n"
            "This page is deliberately blocked rather than rendering an empty "
            "portfolio, which would look like you hold nothing. "
            f"Still available: {', '.join(safe_pages)}."
        ))
    if scope == "partial":
        return ("warn", (
            f"⚠️ Partial database outage — {fail_rec.get('detail', '')}. "
            "Your holdings are correct; history- and watchlist-driven surfaces may "
            "look empty when they are not."
        ))
    return ("none", None)
