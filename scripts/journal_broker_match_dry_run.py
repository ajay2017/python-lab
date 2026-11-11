#!/usr/bin/env python3
"""Dry run of the journal vs Robinhood matcher (TJDate C5; docs/plans/journal-broker-match-c5.md).

READ-ONLY, always: it calls `db.set_readonly(True)` before anything else and never
writes. It loads the journal (`db.load_trades_or_none`), every Robinhood fill of
any status since `journal_broker_match.fills_read_since(now)`
(`db.load_snaptrade_fills_or_none`) and the last completed transaction sync
(`db.load_snaptrade_txn_synced_at_or_none`), runs
`journal_broker_match.match_journal_to_broker` and prints:

  * the coverage text (how far the comparison reaches given the last sync);
  * a count per kind and per severity;
  * every finding: rank, kind, ticker, action, journal ids, txn ids, statuses, text;
  * the Phase-2 gate count: the number of findings with severity "differs"
    (owner decision D4: measured here now, a broker-lane log line later).

A result of None (the journal or the fills couldn't be read) is printed as such,
never as "nothing to report". "No differences" is only as good as the coverage
line printed above it.

First run, expected: CSCO 13902 (logged, unlinked) reads LINK with the
RECOMMENDATION row.

Needs SUPABASE_URL and SUPABASE_KEY (the service-role key) in the environment
(run from a normal shell; never from the app).

Usage:
    python scripts/journal_broker_match_dry_run.py

Exit codes: 0 ok; 2 a variable missing, or the journal / fills / result could not
be read (the reason is printed).
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stock_analyzer import db  # noqa: E402
from stock_analyzer import journal_broker_match as jbm  # noqa: E402
from stock_analyzer import market_time  # noqa: E402

_SEVERITY_ORDER = ("differs", "unresolved", "unchecked", "info")


def _fmt_date(d) -> str:
    return "?" if d is None else d.isoformat()


def format_finding(f: dict) -> list:
    """The lines for one finding (pure; no I/O)."""
    jids = ", ".join(f"#{i} ({_fmt_date(d)})"
                     for i, d in zip(f["journal_ids"], f["journal_dates"])) or "none"
    txns = ", ".join(
        f"{t or '(no txn id)'} [{s or 'no status'}, session {_fmt_date(sd)}, placed {_fmt_date(pd_)}]"
        for t, s, sd, pd_ in zip(f["txn_ids"], f["fill_statuses"], f["fill_dates"],
                                 f["placed_dates"])) or "none"
    head = (f"[{f['rank']:>3}] {f['kind']}  {f['ticker'] or '-'} {f['action'] or '-'}"
            f"  severity={f['severity']}")
    lines = [head, f"      journal: {jids}", f"      robinhood: {txns}"]
    extra = []
    if f.get("holdings_effect") is not None:
        extra.append(f"holdings_effect={f['holdings_effect']:+g}")
    if f.get("correction_shape"):
        extra.append(f"shape={f['correction_shape']}")
    extra.append("one_click=" + ("yes" if f.get("one_click_eligible")
                                 else f"no ({f.get('one_click_block') or 'n/a'})"))
    if f.get("requires_fill_date_check"):
        extra.append("needs the 'Filled' date confirmed")
    if f.get("nearby_journal_ids"):
        extra.append("nearby journal rows " + ", ".join(f"#{i}" for i in f["nearby_journal_ids"]))
    if f.get("possible_duplicate_of") is not None:
        extra.append(f"possible duplicate of #{f['possible_duplicate_of']}")
    lines.append("      " + "; ".join(extra))
    lines.append(f"      {f['text']}")
    return lines


def format_report(findings, coverage: dict) -> list:
    """The whole printout as lines (pure; no I/O). `findings` is the matcher's
    result (None = couldn't check), `coverage` is `jbm.match_coverage(...)`."""
    lines = ["Coverage: " + str(coverage.get("text") or "")]
    if findings is None:
        lines.append("RESULT: couldn't check. The matcher returned None (the journal or the "
                     "Robinhood fills were unreadable). This is NOT 'no differences'.")
        return lines
    by_kind = Counter(f["kind"] for f in findings)
    by_sev = Counter(f["severity"] for f in findings)
    lines.append(f"Findings: {len(findings)}")
    if not findings:
        lines.append("  none (checked, nothing to say; read it together with the coverage line)")
    for sev in _SEVERITY_ORDER:
        if by_sev.get(sev):
            kinds = ", ".join(f"{k} {n}" for k, n in sorted(by_kind.items())
                              if any(f["kind"] == k and f["severity"] == sev for f in findings))
            lines.append(f"  {sev}: {by_sev[sev]} ({kinds})")
    for f in findings:
        lines.append("")
        lines.extend(format_finding(f))
    lines.append("")
    lines.append(f"Phase-2 gate count (severity \"differs\"): {by_sev.get('differs', 0)}")
    return lines


def _die(msg: str, code: int = 2):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def main(argv=None) -> int:
    db.set_readonly(True)          # before anything else: this script never writes
    for var in ("SUPABASE_URL", "SUPABASE_KEY"):
        if not os.environ.get(var):
            _die(f"{var} is not set in the environment.")
    now = market_time.now_et()
    journal = db.load_trades_or_none()
    if journal is None:
        _die("the journal could not be read (db.load_trades_or_none returned None).")
    since = jbm.fills_read_since(now).isoformat()
    fills = db.load_snaptrade_fills_or_none(since)
    if fills is None:
        _die(f"Robinhood fills since {since} could not be read "
             "(db.load_snaptrade_fills_or_none returned None).")
    synced_at = db.load_snaptrade_txn_synced_at_or_none()
    findings = jbm.match_journal_to_broker(journal, fills, synced_at, now)
    coverage = jbm.match_coverage(synced_at)
    print(f"Journal rows: {len(journal)}; Robinhood fills read since {since}: {len(fills)}; "
          f"now {now.isoformat(timespec='minutes')}")
    for line in format_report(findings, coverage):
        print(line)
    return 2 if findings is None else 0


if __name__ == "__main__":
    sys.exit(main())
