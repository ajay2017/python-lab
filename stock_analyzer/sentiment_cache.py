"""Helpers for the score role's daily sentiment cache (plan 17, chunks C3-C4)."""
from __future__ import annotations

from datetime import datetime, timezone


def score_date_today() -> str:
    """The UTC date (ISO) the ``sentiment_llm_cache`` rows are keyed on.

    ``bundle_loader`` keys the cache with ``date.today()``, which is the UTC date
    on the hosted containers; this names that date explicitly so the score
    model switch (``llm_decision_settings.score_entry_for``) and the cache agree.
    """
    return datetime.now(timezone.utc).date().isoformat()
