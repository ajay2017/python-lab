"""
Discovery universe — the broad net for the Movers feature.

This is intentionally LARGER than the curated daily scan universe (the ~70
hand-picked names Grow Today scans every day). The Movers pipeline scans this
wider list for big 1-day gainers so a genuine breakout in a name the user
isn't tracking (the next SMCI) can surface — instead of being invisible
because it wasn't on the short list.

Curated extended set (~200 liquid large/mid-caps across every sector).
Overlap with the scan universe is fine: the Movers pipeline excludes
already-tracked / held / watchlist tickers at runtime, so a name appearing in
both is simply deduped.

App Settings (docs/plans/app-settings.md) Commit 3, 2026-09-01 — the
module-level `DISCOVERY_UNIVERSE` dict that used to live here was deleted;
the roster is now DB-backed (Supabase `reference_tables`, key
'discovery_universe') and reached exclusively via
`stock_analyzer.reference_data.resolve_universe` / `resolve_universe_or_none`.
Editing membership happens in the ⚙️ App Settings UI, which keeps an
append-only history of every change — the per-refresh rationale that used to
live as inline comments on this dict now lives there.
"""


def discovery_tickers(
    universe: "dict[str, list[str]]",
    exclude: set[str] | None = None,
) -> list[str]:
    """Flatten the discovery universe into a deduped ticker list.

    universe (App Settings, docs/plans/app-settings.md): the resolved
    `discovery_universe` payload, threaded in by the caller (via
    `stock_analyzer.reference_data.resolve_universe`) so this function stays
    pure/testable. REQUIRED, no default — Commit 3 deleted the module-level
    `DISCOVERY_UNIVERSE` dict this used to fall back to. Every real caller
    must pass a resolved payload on success, or an explicit `{}` — never a
    bare `None` — when the table is unavailable (see `scanner.scan_sectors`'s
    identical `universe` param for the full reasoning).

    exclude: tickers to drop (already-tracked scan-universe names, held
    positions, watchlist) — these are already scanned elsewhere, so the
    Movers pipeline shouldn't re-surface them. Comparison is case-insensitive.
    """
    excl = {str(t).upper().strip() for t in (exclude or set())}
    seen: set[str] = set()
    out: list[str] = []
    for names in universe.values():
        for t in names:
            tu = t.upper().strip()
            if tu and tu not in excl and tu not in seen:
                out.append(tu)
                seen.add(tu)
    return out
