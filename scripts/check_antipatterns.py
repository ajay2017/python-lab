#!/usr/bin/env python3
"""Recurring-defect tripwire: fails when a NEW instance of a bug-class that our
audits keep re-finding is introduced into app.py / cron_runner.py /
stock_analyzer/**.py.

Why: the 2026-07-29 and 2026-08-04 full audits showed the findings aren't
random — a handful of *classes* recur because the safe idiom is opt-in and
hand-remembered, and per-feature review is structurally blind to them. This
gate turns "discipline must remember" into "mechanism enforces" for the classes
that are mechanically detectable (see CLAUDE.md "Definition of Done" and the
audit memories project_audit_2026_07_29 / project_audit_2026_08_04).

Rules (each an AST signature, low false-positive by design):
  OFFLINE_SENTINEL_COLLAPSE  — `<something>.get(...) or []` / `or {}` / `or ()`,
      OR the semantically identical ternary form `<something>.get(...) if <cond>
      else []` / `[] if <cond> else <something>.get(...)` (2026-08-24 audit: an
      `IfExp` collapses "couldn't compute" into "checked, empty" exactly like the
      `BoolOp/Or` form — a live instance survived undetected in app.py's F-252
      broker-drift cross-reference until this rule was widened).
      The offline-vs-checked-empty contract (producer returns None on failure)
      is silently defeated at the *consumer* read site by `or []`, turning
      "couldn't compute" into "checked, no risk." The single most-repeated
      finding in the 2026-08-04 audit (hit by 3 of 9 passes). Read with an
      explicit `is None` check (or the shared get_or_offline helper) instead.
      NOTE: deliberately does NOT flag the two-arg default form
      `<something>.get(key, [])` — unlike `or []`, dict.get()'s default only
      fires when the key is genuinely absent, not when it's present and set
      to `None`; since this project's producers set a failed cache key to
      `None` (never omit it), the two-arg form actually preserves the offline
      signal correctly. A 2026-08-05 audit pass proposed widening this rule to
      catch the two-arg form too and was reverted after breaking
      `test_ignores_safe_reads` — that test's `# default arg is fine` comment
      already captured this exact distinction; investigate a prior test
      before broadening a rule's AST match, not just the docstring.
  UNSAFE_HTML_DYNAMIC        — `unsafe_allow_html=True` on a call whose HTML arg
      is dynamic: an f-string / concatenation / bare variable / `.format()`
      or `.join()` call / a call to a locally-defined helper function (the
      last one was a detector blind spot found by the 2026-08-05 audit — a
      helper can build interpolated markup just as easily as an inline
      f-string). This project has paid the XSS class down 7+ times on
      different surfaces. Escape interpolated values (safe_html/html.escape)
      before rendering.
  NAIVE_UTCNOW               — `datetime.utcnow()` (naive AND deprecated); the
      project convention is NY-tz-aware time (pytz / America/New_York).
  NAIVE_DATE_TODAY           — bare `date.today()` / `datetime.today()`; off-by-
      one across ~8pm-midnight ET vs the project's NY-tz convention. Use the
      shared trading-day / _today_et helper on any date that feeds a decision.
  MIXED_TZ_PARSE             — `pd.to_datetime(...)` on `traded_at` WITHOUT
      `format="ISO8601"`. pandas infers a strict format from the first non-null
      value, so a column mixing microsecond and second precision silently
      coerces the non-matching rows to NaT — and `traded_at` genuinely mixes
      (raw-SQL inserts vs Postgres `now()`; see db.recalculate_from_trades).
      `utc=True` alone does NOT fix it. THIRD recurrence of this one class
      (2026-05-28, 2026-08-02, then eight sites at once on 2026-08-23) is what
      earned it a gate. Scoped to `traded_at` only: it is the column with a
      PROVEN mixed writer. `snapshot_date` is a plain `date` (no time to vary)
      and `surfaced_at` is written solely through the SDK — gating on those
      would be an inferred premise. The kwarg VALUE is checked, not merely its
      presence: `format=None` is definitionally the inferred-format behaviour
      this bans, and an explicit strptime format reproduces the original defect
      exactly — both would otherwise read as compliant.
      LIMIT, stated honestly: detection is an AST filter plus a substring scan of
      the call source, so a parse whose column name is ALIASED into a local first
      (`_ta = row.get("traded_at")` then `pd.to_datetime(_ta, ...)`, as in
      risk_advisor) is NOT caught. Those happen to be scalar and so are immune,
      but do not read a green gate as proof that every parse carries the kwarg.
      This rule currently baselines at ZERO, so any hit is a real finding.
      Memory `feedback_pandas_mixed_tz_parsing`.

Baseline: like check_constants_documented.py, a snapshot of the existing tail is
recorded (scripts/antipattern_baseline.json) so CI is green on day one and only
NEW instances fail. The baseline is keyed on the *normalized source segment*
(not line numbers) so it survives the app.py line-drift that routinely happens
during a refactor. An allowlisted item is a conscious "this instance is
acceptable" checkpoint — burn the baseline down over time, don't grow it.

Usage:
  python scripts/check_antipatterns.py            # check (exit 1 on a new instance)
  python scripts/check_antipatterns.py --init      # (re)write the baseline snapshot
  python scripts/check_antipatterns.py --list       # print every current instance
"""
from __future__ import annotations

import ast
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "antipattern_baseline.json"

# Files in scope: the two runtimes + the pure-logic package. Tests and scripts
# are intentionally out of scope (a test may deliberately construct a bad case).
TARGETS = [ROOT / "app.py", ROOT / "cron_runner.py"] + sorted(
    (ROOT / "stock_analyzer").rglob("*.py")
)


def _is_empty_container(node: ast.AST) -> bool:
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        return len(node.elts) == 0
    if isinstance(node, ast.Dict):
        return len(node.keys) == 0
    return False


def _has_get_call(node: ast.AST) -> bool:
    """True if the expr is (or contains at its top level) a `.get(...)` call."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return node.func.attr == "get"
    return False


def _is_dynamic_html(node: ast.AST) -> bool:
    """True if an HTML value is anything other than a plain string literal."""
    if isinstance(node, ast.JoinedStr):  # f-string
        return True
    if isinstance(node, ast.BinOp):  # concatenation ("..." + x, "..." % x)
        return True
    if isinstance(node, ast.Name):  # a variable holding assembled markup
        return True
    if isinstance(node, ast.Call):
        # .format(...) is dynamic; plain "".join([...]) of literals is rare and
        # baselined if benign.
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "format",
            "join",
        }:
            return True
        # A call to a locally-defined helper (e.g. `_row(t)`) that builds and
        # returns markup internally is just as dynamic as an inline f-string —
        # the interpolation happens one frame away, not absent (2026-08-05
        # audit: app.py's _tr_evidence_row() went uncaught this way).
        if isinstance(node.func, ast.Name):
            return True
    return False


class _Visitor(ast.NodeVisitor):
    def __init__(self, src: str) -> None:
        self.src = src
        self.hits: list[tuple[str, str]] = []  # (rule, normalized_segment)

    def _seg(self, node: ast.AST) -> str:
        seg = ast.get_source_segment(self.src, node) or ""
        return " ".join(seg.split())  # normalize whitespace/newlines

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if isinstance(node.op, ast.Or):
            has_get = any(_has_get_call(v) for v in node.values)
            empty_default = any(_is_empty_container(v) for v in node.values[1:])
            if has_get and empty_default:
                self.hits.append(("OFFLINE_SENTINEL_COLLAPSE", self._seg(node)))
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        # `X.get(...) if <cond> else []` / `[] if <cond> else X.get(...)` —
        # the ternary form of the same OFFLINE_SENTINEL_COLLAPSE the `or []`
        # BoolOp form above catches. Checked in both orderings since either
        # side of the ternary may be the `.get()` call (2026-08-24 audit).
        for get_side, empty_side in ((node.body, node.orelse), (node.orelse, node.body)):
            if _has_get_call(get_side) and _is_empty_container(empty_side):
                self.hits.append(("OFFLINE_SENTINEL_COLLAPSE", self._seg(node)))
                break
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # UNSAFE_HTML_DYNAMIC
        for kw in node.keywords:
            if (
                kw.arg == "unsafe_allow_html"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
            ):
                html_arg = node.args[0] if node.args else None
                if html_arg is not None and _is_dynamic_html(html_arg):
                    self.hits.append(("UNSAFE_HTML_DYNAMIC", self._seg(node)))
        # MIXED_TZ_PARSE — pd.to_datetime on a mixed-precision timestamptz
        # column without format="ISO8601". THIRD recurrence of one bug class
        # (2026-05-28, 2026-08-02, 2026-08-23 at eight sites at once), which is
        # what earns it a gate. pandas infers a strict format from the first
        # non-null value, so a column mixing microsecond and second precision —
        # which `trades.traded_at` genuinely does, raw-SQL rows vs Postgres
        # `now()` — silently coerces the non-matching rows to NaT. `utc=True`
        # alone does NOT fix it. Memory `feedback_pandas_mixed_tz_parsing`.
        _fn = node.func.attr if isinstance(node.func, ast.Attribute) else \
              getattr(node.func, "id", None)
        _has_iso = any(
            kw.arg == "format"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value == "ISO8601"
            for kw in node.keywords
        )
        if _fn == "to_datetime" and not _has_iso:
            # Scope to the known-mixed columns rather than every parse in the
            # repo — a false positive here costs a harmless extra kwarg, but a
            # broad rule would bury the signal.
            if "traded_at" in self._seg(node):
                self.hits.append(("MIXED_TZ_PARSE", self._seg(node)))
        # NAIVE_UTCNOW / NAIVE_DATE_TODAY
        if isinstance(node.func, ast.Attribute):
            if node.func.attr == "utcnow":
                self.hits.append(("NAIVE_UTCNOW", self._seg(node)))
            elif node.func.attr == "today" and not node.args:
                # date.today() / datetime.today() — attribute call, no args.
                base = node.func.value
                base_name = getattr(base, "id", None) or getattr(base, "attr", None)
                if base_name in {"date", "datetime"}:
                    self.hits.append(("NAIVE_DATE_TODAY", self._seg(node)))
        self.generic_visit(node)


def scan() -> dict[str, Counter]:
    """{relpath: Counter({(rule, segment): count})} across all target files."""
    out: dict[str, Counter] = {}
    for path in TARGETS:
        if not path.exists():
            continue
        rel = path.relative_to(ROOT).as_posix()
        try:
            # utf-8-sig strips a leading BOM (app.py has one) — ast.parse rejects
            # a BOM as a non-printable char; harmless for non-BOM files.
            src = path.read_text(encoding="utf-8-sig")
            tree = ast.parse(src, filename=str(path))
        except (OSError, SyntaxError) as exc:  # never false-block on a parse error
            print(f"⚠️  skipped {rel}: {exc}", file=sys.stderr)
            continue
        v = _Visitor(src)
        v.visit(tree)
        if v.hits:
            out[rel] = Counter((rule, seg) for rule, seg in v.hits)
    return out


def _serialize(scanned: dict[str, Counter]) -> dict:
    # JSON can't key on tuples; store as rule\x1fsegment -> count per file.
    return {
        rel: {f"{rule}\x1f{seg}": n for (rule, seg), n in ctr.items()}
        for rel, ctr in sorted(scanned.items())
    }


def _load_baseline() -> dict[str, Counter]:
    if not BASELINE.exists():
        return {}
    raw = json.loads(BASELINE.read_text(encoding="utf-8"))
    out: dict[str, Counter] = {}
    for rel, entries in raw.get("instances", {}).items():
        ctr: Counter = Counter()
        for key, n in entries.items():
            rule, seg = key.split("\x1f", 1)
            ctr[(rule, seg)] = n
        out[rel] = ctr
    return out


def main() -> int:
    # Segments can contain non-ASCII (emoji in app.py f-strings); the default
    # Windows console codepage would raise UnicodeEncodeError mid-print. Force
    # utf-8 with replacement so the gate never false-crashes on output.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    scanned = scan()

    if "--list" in sys.argv:
        total = 0
        for rel, ctr in sorted(scanned.items()):
            for (rule, seg), n in sorted(ctr.items()):
                total += n
                print(f"{rel}: [{rule}] x{n}  {seg[:100]}")
        print(f"\n{total} instance(s) across {len(scanned)} file(s).")
        return 0

    if "--init" in sys.argv:
        BASELINE.write_text(
            json.dumps(
                {
                    "_comment": (
                        "Baseline snapshot of pre-existing anti-pattern instances "
                        "(check_antipatterns.py). Keyed on normalized AST source "
                        "segments so it survives line-number drift. Makes CI green "
                        "on day one so the gate catches only NEW instances. This is "
                        "a burn-down backlog, NOT a permanent skip — remove entries "
                        "as the underlying instances are fixed; never grow it to "
                        "silence a new finding. Regenerate: "
                        "python scripts/check_antipatterns.py --init"
                    ),
                    "instances": _serialize(scanned),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        n = sum(sum(c.values()) for c in scanned.values())
        print(f"Wrote baseline: {n} instance(s) across {len(scanned)} file(s).")
        return 0

    baseline = _load_baseline()
    drift: list[str] = []
    for rel, ctr in scanned.items():
        base = baseline.get(rel, Counter())
        for (rule, seg), n in ctr.items():
            allowed = base.get((rule, seg), 0)
            if n > allowed:
                drift.append(f"   {rel}: [{rule}] {seg[:120]}")

    if drift:
        print("❌ New anti-pattern instance(s) introduced:")
        for line in sorted(drift):
            print(line)
        print(
            "\nEach is a bug-class our audits keep re-finding. Fix at the source:\n"
            "  OFFLINE_SENTINEL_COLLAPSE → read with an explicit `is None` check "
            "(or stock_analyzer.util.get_or_offline), not `... or []`.\n"
            "  UNSAFE_HTML_DYNAMIC       → escape interpolated values before "
            "unsafe_allow_html (stock_analyzer.util.safe_html / html.escape).\n"
            "  NAIVE_UTCNOW/DATE_TODAY   → use the NY-tz trading-day helper "
            "(stock_analyzer.market_time), not naive utcnow()/date.today().\n"
            "  MIXED_TZ_PARSE            → add format=\"ISO8601\"; utc=True "
            "ALONE does not fix mixed microsecond precision — it silently NaTs "
            "rows. This rule baselines at ZERO; do NOT --init past it.\n"
            "If an instance is genuinely acceptable, regenerate the baseline "
            "deliberately: python scripts/check_antipatterns.py --init\n"
        )
        return 1

    print("✅ No new anti-pattern instances vs. the baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
