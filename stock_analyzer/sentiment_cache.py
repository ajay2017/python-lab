"""Helpers for the score role's daily sentiment cache (plan 17, chunks C3-C4)."""
from __future__ import annotations

from datetime import datetime, timezone

from stock_analyzer import llm


def score_date_today() -> str:
    """The UTC date (ISO) the ``sentiment_llm_cache`` rows are keyed on.

    ``bundle_loader`` keys the cache with ``date.today()``, which is the UTC date
    on the hosted containers; this names that date explicitly so the score
    model switch (``llm_decision_settings.score_entry_for``) and the cache agree.
    """
    return datetime.now(timezone.utc).date().isoformat()


# The model every row with a NULL ``model`` column was made by: those rows predate
# the provenance columns, when the score role was pinned to Haiku 4.5. It is NOT
# ``llm.CODE_DEFAULTS["score"]``, which a later reviewed commit will move.
LEGACY_CACHE_MODEL = llm.HAIKU_4_5


def resolve_score_entry(score_date: str) -> llm.RoleSetting:
    """The ONE score setting a bundle uses for ``score_date``: the cache compare,
    the LLM call and the cache write all take this same entry. The saved
    ``llm_models_decision`` entry for that date, else the code default (effort
    None, capabilities None). Never raises."""
    default = llm.RoleSetting(model=llm.CODE_DEFAULTS[llm.ROLE_SCORE])
    try:
        from stock_analyzer import llm_decision_settings
        entry = llm_decision_settings.current_setting(score_date=score_date).get(
            llm.ROLE_SCORE)
        return entry if isinstance(entry, llm.RoleSetting) else default
    except Exception:  # noqa: BLE001
        return default


def row_matches(row, entry: llm.RoleSetting) -> bool:
    """True iff the cached row was made by ``entry`` (model and effort). A NULL /
    empty model reads as ``(LEGACY_CACHE_MODEL, None)``."""
    row = row if isinstance(row, dict) else {}
    model = row.get("model") or LEGACY_CACHE_MODEL
    effort = row.get("effort") or None
    return (model, effort) == (entry.model, entry.effort or None)
