"""Database maintenance / data-quality framework — Phase 1 (detection only).

docs/plans/data-maintenance-framework.md. A registry of small, independent,
pure(-ish) check functions, mirroring the two patterns this codebase already
uses successfully: `system_health.py`'s check-registry row shape, and
`cron_runner.py::_run_maintenance`'s "isolated sub-jobs, each in its own
try/except" discipline.

Answers a THIRD, distinct question from the two existing DB-focused efforts
in this repo: is the stored row-SET structurally clean over time — orphaned
rows, duplicate rows, stuck state transitions — not "is the cron alive"
(system_health.py already owns that) and not "is this composite fabricated"
(docs/plans/data-integrity.md already owns that).

PHASE 1 IS DETECTION-ONLY. Nothing in this module writes, updates, or
deletes a single row anywhere. `assert_not_protected` / `would_delete_from`
exist so the protected-table invariant is structurally enforced and tested
from day one, before any future remediation phase ever adds a real delete
call — not because Phase 1 needs them for anything it does today.

Severity vocabulary (matches system_health.py):
  "ok"      — checked, nothing wrong found.
  "warn"    — checked, found something to flag for a human (never auto-fixed).
  "unknown" — the underlying read failed / DB unreachable — checked NOTHING,
              never collapsed to "ok" (the offline-sentinel discipline this
              repo's audits repeatedly had to re-fix — see
              feedback_sentinel_is_present / feedback_none_sentinel_meets_pandas).
  "down"    — reserved for a future remediation phase; nothing in Phase 1
              emits it (there is nothing here that is "provably broken" in
              the way check_data_stores' missing-table case is).

Coordination guardrail (from the plan doc's Safety design section): this
module is pure back-office hygiene. It must never publish anything to
`st.session_state` that a decision consumer reads, and must never surface on
a decision surface (Act Today, Watchlist, etc.) — its only surfaces are the
owner-only 🩺 System Trust page and the Saturday maintenance email.
"""
from __future__ import annotations

from typing import Any

# ── The hard constraint: intentionally-permanent tables ─────────────────────
# Every table here exists specifically to grade a past call (Engine Track
# Record, Research Scorecard, Gate Suppression Ledger, Recommendation
# Outcomes Measurement) — a maintenance job that archives or prunes any of
# these would destroy the evidence base those features are built on. This
# framework must be architecturally INCAPABLE of targeting these tables for
# deletion, not merely instructed not to.
_PROTECTED_TABLES: "frozenset[str]" = frozenset({
    "recommendations",
    "exit_signals",
    "gate_suppressions",
    "rec_events",
    "score_history",
    "model_predictions",
    "analyst_target_snapshots",
    "judgment_opinions",
    "judgment_grades",
    "analyst_coverage",
})


def assert_not_protected(table_name: str) -> None:
    """Raise ValueError if `table_name` is one of the intentionally-permanent
    historical/event-log tables under "the hard constraint" above. Every
    present or future destructive code path in this module must call this
    as its first line, before doing anything else."""
    if table_name in _PROTECTED_TABLES:
        raise ValueError(
            f"refusing to delete from protected table '{table_name}' — this "
            "table is intentionally permanent (grades a past recommendation "
            "call) and must never be pruned or archived; see docs/plans/"
            "data-maintenance-framework.md's 'hard constraint' section"
        )


def would_delete_from(table_name: str) -> None:
    """Placeholder guard for a not-yet-built deletion path. Phase 1 has zero
    delete/remediation code — this exists purely so the protected-table
    invariant is tested and structurally enforced from day one, rather than
    bolted on later once Phase 2's F1 orphan-cache auto-remediation actually
    adds a real delete call (docs/plans/data-maintenance-framework.md,
    Decision 3 — gated on an 8-week clean observation window). Any future
    delete path must call `assert_not_protected(table_name)` as its own
    first line too; this function is not a substitute for that, it is the
    thing being exercised by today's test."""
    assert_not_protected(table_name)


# ── Check 1 — orphan cache rows (F1) ─────────────────────────────────────────
# table -> the column holding its "last touched" date/timestamp. Exported for
# tests. Keys must be a subset of _PROTECTED_TABLES's complement — none of
# these 8 are protected (they are all regenerable research caches).
_ORPHAN_CACHE_TABLES: "dict[str, str]" = {
    "bundle_cache":          "fetched_at",
    "fundamentals_cache":    "fetched_at",
    "etf_lookthrough_cache": "updated_at",
    "sector_cache":          "updated_at",
    "sentiment_llm_cache":   "score_date",
    "thesis_erosion_cache":  "score_date",
    "debate_cache":          "debate_date",
    "price_xcheck_history":  "check_date",
}


def _group_max_touch(rows: "list[dict]", date_col: str) -> "dict[str, Any]":
    """Group rows by uppercased ticker, keeping the MAX parsed date per
    ticker. A malformed/unparseable date is skipped from aggregation for
    that row (never crashes, never silently treated as "very old" or "very
    new") — if every row for a ticker is unparseable, that ticker is simply
    absent from the returned dict and can never be flagged as an orphan
    (can't determine recency -> not a confident finding)."""
    import pandas as pd
    out: "dict[str, Any]" = {}
    for r in rows or []:
        tk = str(r.get("ticker") or "").strip().upper()
        if not tk:
            continue
        ts = pd.to_datetime(r.get(date_col), utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        prev = out.get(tk)
        if prev is None or ts > prev:
            out[tk] = ts
    return out


def check_orphan_cache_rows(
    held_tickers: "set[str]",
    watchlist_tickers: "set[str]",
    discovery_tickers: "set[str]",
) -> "list[dict]":
    """F1 — a ticker no longer held/watchlisted/in the discovery universe
    whose most-recent cache row (across 8 per-ticker cache tables) is older
    than DATA_MAINT_ORPHAN_CACHE_GRACE_DAYS is flagged as an orphan-cache
    candidate. Detection only — nothing is deleted. Low individual stakes:
    every consumer of these caches already gates reads on a max-age, so a
    stale row is never SERVED, only accumulated.

    Returns one row per table in `_ORPHAN_CACHE_TABLES` — "unknown" if that
    table could not be read at all, "warn" if 1+ orphaned tickers were
    found, "ok" if checked and none were found.
    """
    from stock_analyzer import db
    from stock_analyzer import market_time
    from stock_analyzer.constants import DATA_MAINT_ORPHAN_CACHE_GRACE_DAYS

    known = {
        str(t).strip().upper()
        for t in (
            set(held_tickers or set())
            | set(watchlist_tickers or set())
            | set(discovery_tickers or set())
        )
    }
    today = market_time.today_et()

    out: "list[dict]" = []
    for table, date_col in _ORPHAN_CACHE_TABLES.items():
        key = f"orphan_cache_{table}"
        label = f"Orphan cache rows — {table}"
        rows = db.load_ticker_last_touched(table, date_col)
        if rows is None:
            out.append({
                "key": key, "label": label, "severity": "unknown",
                "detail": f"could not read {table} — DB unreachable",
            })
            continue
        last_touch = _group_max_touch(rows, date_col)
        orphaned = sorted(
            tk for tk, ts in last_touch.items()
            if tk not in known
            and (today - ts.date()).days > DATA_MAINT_ORPHAN_CACHE_GRACE_DAYS
        )
        if orphaned:
            examples = ", ".join(orphaned[:5])
            tail = f" (+{len(orphaned) - 5} more)" if len(orphaned) > 5 else ""
            out.append({
                "key": key, "label": label, "severity": "warn",
                "detail": (
                    f"{len(orphaned)} ticker(s) not held/watchlisted/"
                    f"discovered, cache older than "
                    f"{DATA_MAINT_ORPHAN_CACHE_GRACE_DAYS}d: {examples}{tail}"
                ),
                "count": len(orphaned),
            })
        else:
            out.append({
                "key": key, "label": label, "severity": "ok",
                "detail": "no orphaned cache rows found", "count": 0,
            })
    return out


# ── Check 2 — account_flows duplicates (F2a backstop) ────────────────────────
def check_account_flows_duplicates() -> "list[dict]":
    """F2a backstop — among MANUAL account_flows rows (snaptrade_txn_id IS
    NULL), flag any (flow_date, flow_type, amount, note) group with 2+ rows
    as a likely accidental duplicate (e.g. a double-clicked submit that
    slipped past the write-time guard). Broker-synced rows (non-null
    snaptrade_txn_id) are never part of the grouping — that population is
    already protected by a real partial unique index. Detail names only the
    grouping key and count, never raw row ids."""
    from stock_analyzer import db

    key, label = "account_flows_duplicates", "Duplicate account_flows entries"
    rows = db.load_account_flows_for_dedup_check()
    if rows is None:
        return [{
            "key": key, "label": label, "severity": "unknown",
            "detail": "could not read account_flows — DB unreachable",
        }]

    groups: "dict[tuple, int]" = {}
    for r in rows:
        if r.get("snaptrade_txn_id"):
            continue  # broker-synced — separately protected, not this check's concern
        gk = (
            str(r.get("flow_date")),
            str(r.get("flow_type")),
            round(float(r.get("amount") or 0.0), 2),
            r.get("note"),
        )
        groups[gk] = groups.get(gk, 0) + 1

    dupes = {k: v for k, v in groups.items() if v >= 2}
    if dupes:
        examples = "; ".join(
            f"{gk[0]} {gk[1]} ${gk[2]:.2f} (×{n})" for gk, n in list(dupes.items())[:5]
        )
        return [{
            "key": key, "label": label, "severity": "warn",
            "detail": f"{len(dupes)} duplicate group(s) among manual entries: {examples}",
            "count": len(dupes),
        }]
    return [{
        "key": key, "label": label, "severity": "ok",
        "detail": "no duplicate manual account_flows entries found", "count": 0,
    }]


# ── Check 3 — analyst_coverage duplicates (F2b) ──────────────────────────────
def check_analyst_coverage_duplicates() -> "list[dict]":
    """F2b — flag-for-review only, forever (never auto-merged). Groups by
    (ticker, article_date, raw_text) and flags a group with 2+ rows as a
    likely accidental duplicate paste of the SAME article. Two rows sharing
    only (ticker, article_date) with DIFFERENT raw_text are the legitimate
    multi-firm-per-article case (confirmed by docs/plans/data-integrity.md's
    D9 investigation) and are never flagged.

    IMPORTANT null-safety note (a documented, previously-hit mistake in this
    repo — see D9's "process note"): a row is only ever compared when its
    raw_text is truthy on BOTH sides. Two rows that both have NULL raw_text
    are never treated as matching each other — using `raw_text or ''` (or
    COALESCE) to test equality would incorrectly call two unrelated
    NULL-raw_text rows "duplicates".

    Uses `db.load_analyst_coverage_or_none()` (not the plain
    `load_analyst_coverage()`) specifically so a genuine read failure is
    distinguishable from a real zero-row table — the plain version collapses
    both to the same empty DataFrame (2026-09-28 Opus review finding: the
    None-capable sibling already existed and should be used here, matching
    the offline-sentinel discipline every other check in this module follows).
    """
    from stock_analyzer import db
    import pandas as pd

    key, label = "analyst_coverage_duplicates", "Duplicate analyst_coverage pastes"
    df = db.load_analyst_coverage_or_none(ticker=None, days=None, limit=None)

    if df is None:
        return [{
            "key": key, "label": label, "severity": "unknown",
            "detail": "could not read analyst_coverage — DB unreachable",
            "count": 0,
        }]

    groups: "dict[tuple, int]" = {}
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            raw = row.get("raw_text")
            if pd.isna(raw) or not str(raw).strip():
                continue  # NULL/empty raw_text never matches anything — the COALESCE trap
            gk = (
                str(row.get("ticker") or "").strip().upper(),
                str(row.get("article_date") or ""),
                str(raw),
            )
            groups[gk] = groups.get(gk, 0) + 1

    dupes = {k: v for k, v in groups.items() if v >= 2}
    if dupes:
        examples = ", ".join(f"{gk[0]}@{gk[1]}" for gk in list(dupes.keys())[:5])
        return [{
            "key": key, "label": label, "severity": "warn",
            "detail": (
                f"{len(dupes)} likely accidental duplicate paste(s) — "
                f"identical article text on the same (ticker, article_date): {examples}"
            ),
            "count": len(dupes),
        }]
    return [{
        "key": key, "label": label, "severity": "ok",
        "detail": "no duplicate pastes found",
        "count": 0,
    }]


# ── Check 4 — thesis_reviews duplicates (F2c) ────────────────────────────────
def check_thesis_reviews_duplicates() -> "list[dict]":
    """F2c — low blast radius (display-only AI review log). Groups by
    (ticker, inputs_hash) — more precise than a synthetic key, since two
    reviews sharing the same ticker and the same inputs_hash are the same
    underlying inputs re-reviewed, i.e. a genuine duplicate (most plausibly
    a double-click before st.rerun() fired). Only rows with a truthy
    inputs_hash on both sides are ever compared (same null-safety rule as
    check 3 — a NULL inputs_hash never matches another NULL).

    Same LIMITATION as check 3: `db.load_thesis_reviews()` collapses a read
    failure to the same empty DataFrame as "genuinely no rows yet" — no
    offline-sentinel distinction is available here either.
    """
    from stock_analyzer import db
    import pandas as pd

    key, label = "thesis_reviews_duplicates", "Duplicate thesis_reviews"
    df = db.load_thesis_reviews()

    groups: "dict[tuple, int]" = {}
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            ih = row.get("inputs_hash")
            if pd.isna(ih) or not str(ih).strip():
                continue
            gk = (str(row.get("ticker") or "").strip().upper(), str(ih))
            groups[gk] = groups.get(gk, 0) + 1

    dupes = {k: v for k, v in groups.items() if v >= 2}
    if dupes:
        examples = ", ".join(gk[0] for gk in list(dupes.keys())[:5])
        return [{
            "key": key, "label": label, "severity": "warn",
            "detail": (
                f"{len(dupes)} ticker(s) with a duplicate (ticker, inputs_hash) "
                f"thesis review: {examples}"
            ),
            "count": len(dupes),
        }]
    return [{
        "key": key, "label": label, "severity": "ok",
        "detail": "no duplicate thesis reviews found (empty/unreadable reads as clean "
                  "here — load_thesis_reviews has no offline-sentinel distinction)",
        "count": 0,
    }]


# ── Check 5 — snaptrade_pending_imports anomalies (F4) ───────────────────────
def check_pending_import_anomalies() -> "list[dict]":
    """F4 — thin/low-stakes per the plan doc. Flags a `snaptrade_txn_id`
    that appears more than once among currently-"pending" rows, which would
    indicate the same broker transaction was queued for import twice.

    Same LIMITATION as checks 3/4: `db.load_snaptrade_pending_imports()`
    returns [] on any failure per its own docstring — no offline-sentinel
    distinction is available here either.
    """
    from stock_analyzer import db

    key, label = "pending_import_duplicates", "Duplicate pending broker imports"
    rows = db.load_snaptrade_pending_imports(status="pending")

    counts: "dict[str, int]" = {}
    for r in rows or []:
        txn = r.get("snaptrade_txn_id")
        if not txn:
            continue
        counts[txn] = counts.get(txn, 0) + 1

    dupes = {k: v for k, v in counts.items() if v >= 2}
    if dupes:
        return [{
            "key": key, "label": label, "severity": "warn",
            "detail": (
                f"{len(dupes)} snaptrade_txn_id(s) queued more than once "
                "among pending imports"
            ),
            "count": len(dupes),
        }]
    return [{
        "key": key, "label": label, "severity": "ok",
        "detail": "no duplicate pending imports found (empty/unreadable reads as "
                  "clean here — load_snaptrade_pending_imports has no offline-sentinel "
                  "distinction)",
        "count": 0,
    }]


# ── Orchestrator ──────────────────────────────────────────────────────────────
def run_all_checks(
    held_tickers: "set[str]",
    watchlist_tickers: "set[str]",
    discovery_tickers: "set[str]",
) -> "list[dict]":
    """Run all 5 checks, each isolated in its own try/except (mirrors
    cron_runner.py::_run_maintenance's per-sub-job isolation) so one raising
    can't prevent the others from reporting. A check that raises degrades to
    a single "unknown" finding row naming which check failed, rather than
    propagating and losing every other check's result."""
    out: "list[dict]" = []

    def _safe(name: str, fn) -> None:
        try:
            out.extend(fn())
        except Exception as exc:
            out.append({
                "key": f"{name}_error", "label": f"Check failed: {name}",
                "severity": "unknown",
                "detail": f"check raised — {str(exc)[:160]}",
            })

    _safe("orphan_cache", lambda: check_orphan_cache_rows(
        held_tickers, watchlist_tickers, discovery_tickers))
    _safe("account_flows_duplicates", check_account_flows_duplicates)
    _safe("analyst_coverage_duplicates", check_analyst_coverage_duplicates)
    _safe("thesis_reviews_duplicates", check_thesis_reviews_duplicates)
    _safe("pending_import_duplicates", check_pending_import_anomalies)
    return out
