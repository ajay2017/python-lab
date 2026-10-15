"""Impossible-states audit — the baseline and the regression decision.

The audit (docs/sql/impossible-states-audit.sql, Q0) counts rows in prod that
should not exist if the code were correct. The 2026-10-14 sweep using it found
five real defects (ff5ef85, 3ab4d59). Q0 also lives as a Postgres function
(docs/sql/impossible-states-function.sql) so the weekly maintenance cron can
run it through PostgREST rpc as data_maintenance check 6. This module decides
what a run's counts MEAN. It is pure, so the decision is unit-tested.

Three kinds of check, set from the post-cleanup baseline of 2026-10-14:

  ZERO      must stay 0. Any row is a new defect. This is every check not
            listed in _RESIDUAL.
  FROZEN    has explained residual rows that must not GROW. Some are
            legacy, with the cause fixed (C8: the MU 2026-06-25 sweep hole,
            which can't be recovered; C9: new_pick + EXIT rows from before the
            F-39i fix; D5: May-2026 rows from before the composite column was
            filled). Others are rare and explained, but a regression in the
            same class would add rows daily (C3: one producer's composite
            against another's; C7: book against snapshots). A higher count is
            either the cause coming back or a new legitimate case. Investigate
            it, then raise the baseline. That review is the point.
  ACCRUING  can grow without a defect, and can't be told apart from one by
            count. C1 is the owner's warn-only ENTER_NOW-on-a-WATCH-name choice.
            C2 is non-binding G-20 rows beside a buy, from intraday
            transitions. A3 is exact-50 pillars: mostly the score lattice, BUT
            it also includes the WK 2026-10-07 exact-50 technical row, which
            stays an unproven exact-50 (most likely a real full-leg reading
            per the 2026-10-15 SQL). Since C3 (2026-10-15) records null an
            unmeasured technical score via t_available. Never
            raises a finding. The count is reported next to its baseline on
            System Trust.

The baseline numbers are observed counts of explained rows, not investment-
policy thresholds, so they live here rather than in constants.py (the
same reasoning as a test's expected value). Raise a FROZEN baseline only
after investigating the new rows with Q1-style detail queries; that
investigation is the point of the alert.
"""
from __future__ import annotations

BASELINE_DATE = "2026-10-14"

ZERO, FROZEN, ACCRUING = "zero", "frozen", "accruing"

# Every check id Q0 returns. Kept explicit, so a function that returns fewer
# or extra checks (a stale or edited copy of Q0) is itself reported.
EXPECTED_IDS: "frozenset[str]" = frozenset(
    [f"A{i}" for i in range(1, 7)]
    + [f"B{i}" for i in range(1, 4)]
    + [f"C{i}" for i in range(1, 10)]
    + [f"D{i}" for i in range(1, 11)]
)

# id -> (policy, baseline n_bad). Every other id in EXPECTED_IDS is ZERO.
_RESIDUAL: "dict[str, tuple[str, int]]" = {
    "A3": (ACCRUING, 23),
    "C1": (ACCRUING, 19),
    "C2": (ACCRUING, 6),
    "C3": (FROZEN, 2),
    "C7": (FROZEN, 3),
    "C8": (FROZEN, 2),
    "C9": (FROZEN, 5),   # derived from Q1's C1 detail; confirm on first automated run
    "D5": (FROZEN, 3),
}

# Must equal scripts/gen_impossible_states_function.py::q0_version() (a test
# pins it). The function returns this as its `_V` row, so a function applied
# in Supabase before a Q0 edit is reported instead of silently running old SQL.
Q0_VERSION = "q0:16c85d32d98d"
_VERSION_ID = "_V"

_KEY, _LABEL = "impossible_states", "Impossible-states audit"
_HOWTO = ("investigate with the detail queries in docs/sql/impossible-states-audit.sql "
          "(Q1/Q2 pattern) before changing the baseline")


def policy_for(check_id: str) -> "tuple[str, int]":
    """(policy, baseline) for one check id; unlisted ids are ZERO with 0."""
    return _RESIDUAL.get(check_id, (ZERO, 0))


def classify(rows: "list[dict] | None", error: "str | None" = None) -> "list[dict]":
    """Turn one Q0 run into data_maintenance finding rows.

    `rows is None` means the audit did not run (function missing, DB
    unreachable). That is "unknown", never "ok": nothing was checked.
    """
    if rows is None:
        return [{
            "key": _KEY, "label": _LABEL, "severity": "unknown",
            "detail": ("audit did not run"
                       + (f" — {str(error)[:140]}" if error else "")
                       + ". If the function is missing, apply "
                         "docs/sql/impossible-states-function.sql in Supabase."),
        }]

    by_id: "dict[str, dict]" = {}
    for r in rows:
        cid = str((r or {}).get("id") or "").strip()
        if cid:
            by_id[cid] = r

    findings: "list[dict]" = []
    _vrow = by_id.pop(_VERSION_ID, None)
    applied = "none" if _vrow is None else str(_vrow.get("chk") or "none")
    if applied != Q0_VERSION:
        findings.append({
            "key": f"{_KEY}_version", "label": f"{_LABEL}: function out of date",
            "severity": "warn",
            "detail": (f"Supabase runs {applied}, the repo's Q0 is {Q0_VERSION} -- "
                       "re-apply docs/sql/impossible-states-function.sql; until then "
                       "these counts come from the OLD audit logic"),
            "count": 1,
        })
    missing = sorted(EXPECTED_IDS - by_id.keys())
    extra = sorted(by_id.keys() - EXPECTED_IDS)
    if missing or extra:
        findings.append({
            "key": f"{_KEY}_shape", "label": f"{_LABEL}: function out of date",
            "severity": "warn",
            "detail": (f"missing {missing or 'none'}, unexpected {extra or 'none'} — "
                       "regenerate docs/sql/impossible-states-function.sql from Q0 "
                       "and re-apply it; unlisted checks were NOT evaluated"),
            "count": len(missing) + len(extra),
        })

    accruing_notes: "list[str]" = []
    for cid in sorted(by_id.keys() & EXPECTED_IDS):
        r = by_id[cid]
        raw = r.get("n_bad")
        try:
            n = int(raw)
        except (TypeError, ValueError):
            findings.append({
                "key": f"{_KEY}_{cid}", "label": f"{_LABEL} {cid}", "severity": "unknown",
                "detail": f"{r.get('chk') or cid}: unreadable count {raw!r}",
            })
            continue
        policy, base = policy_for(cid)
        if policy == ACCRUING:
            accruing_notes.append(f"{cid} {n} (baseline {base})")
            continue
        if n > base:
            findings.append({
                "key": f"{_KEY}_{cid}", "label": f"{_LABEL} {cid}", "severity": "warn",
                "detail": (f"{r.get('chk') or cid}: {n} row(s), baseline {base} "
                           f"({BASELINE_DATE}) — {_HOWTO}"),
                "count": n - base,
            })

    if not findings:
        findings.append({
            "key": _KEY, "label": _LABEL, "severity": "ok",
            "detail": (f"{len(by_id)} checks at or below the {BASELINE_DATE} baseline"
                       + (f"; not alerted (can grow without a defect): "
                          f"{', '.join(accruing_notes)}"
                          if accruing_notes else "")),
            "count": 0,
        })
    return findings
