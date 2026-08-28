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
  NAIVE_DATE_TODAY           — bare `date.today()` / `datetime.today()` / zero-arg
      `datetime.now()`; off-by-one across ~8pm-midnight ET vs the project's
      NY-tz convention (the UTC container has no TZ var). Two-arg `datetime.now(tz)`
      is the correct form and must NOT be flagged. Use the shared trading-day /
      _today_et / _now_et helpers on any time or date that feeds a decision or
      a user-visible timestamp.
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

  SENTINEL_BARE_TRUTHINESS — `_v = st.session_state.get("<coordination key>")`
      (no default) followed by a bare `if _v:` / `if not _v:` on that name. This is
      the SAME offline-sentinel collapse as the rule above, in the one form that
      rule structurally cannot see: the read itself is clean, and the None is
      destroyed one line later by truthiness. Live instance found 2026-08-26 in the
      app review (D9, app.py Sector Gaps): the producer sets `_div_recs_cache = None`
      when the correlation computation fails, and `if _sg_recs:` folded that into the
      same branch as `[]` = "checked, no gaps", so the section vanished with no
      banner. That was the THIRD consecutive review to find this detector narrower
      than the class it is named for, which is what earns the widening.
      Scoped to the documented coordination keys in _SENTINEL_KEYS rather than every
      session_state read, because only those carry the None-on-failure contract —
      a broad rule would bury the signal. LIMIT, stated honestly: single-assignment
      tracking by name within a file, so a variable reassigned between the read and
      the test, or one passed into a helper and tested there, is NOT caught. Do not
      read a green gate as proof that every consumer honours the sentinel. The
      mapping is write-only (a later `_v = other()` does not clear it), so that
      reassignment case is a FALSE POSITIVE, not a miss. The rule also sees only
      the `if _v:` / `if not _v:` shape — `for x in _v or []`, `len(_v)`,
      subscripting and BoolOp uses are all invisible to it, so a green gate means
      "no new bare-truthiness instance", never "the class is closed."

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

# The coordination caches that carry the None-on-failure contract (CLAUDE.md
# "Coordination pattern"). Only these are gated by SENTINEL_BARE_TRUTHINESS: a
# key NOT in this set has no documented offline sentinel, so a bare truthiness
# test on it is not a defect. Keep in sync with CLAUDE.md when a producer adds
# a cache. Four documented keys are deliberately OMITTED: _portfolio_value and
# _holdings_sig_at_home_build (scalars, not offline sentinels), the dynamic
# _rh_prices_cache_* family (name is not a literal), and _daily_brief_offline --
# which must NEVER be added, because it is a plain bool whose falsy state is
# meaningful and `if _x:` is the CORRECT idiom for it (app.py:9732, 22327).
_SENTINEL_KEYS = frozenset({
    "_last_port_df", "_port_df_enriched", "_last_held_data", "_last_held_tickers",
    "_port_risk_cache", "_fragility_cache", "_highbeta_share",
    "_risk_high_alerts_cache", "_risk_advisor_recs_cache", "_alert_list_cache",
    "_actions_cache", "_div_recs_cache", "_corr_df_cache", "_div_score_cache",
    "_avg_corr_cache", "_risk_pairs_cache", "_div_label_cache",
    "_corr_coverage_cache", "_grow_today_sectors_cache", "_grow_composites",
    "_grow_composites_coverage", "_acct_gate_cache", "_leverage_cache",
    "_reduce_calls", "_day_shock_cache", "_structural_alert_cache", "_dpnl_cache",
    "_leading_sectors_cache", "_market_tone_cache", "_mirror_orphans",
    "_mirror_overexp", "_mirror_overhangs", "_pi_factor_tilt_cache",
    "_broker_drift_cache", "_home_synth_cache",
})

# The RENDER LAYER: the two entrypoints, which together are ~40k lines that no
# test imports (verified 2026-08-28: zero tests import app.py). A policy
# threshold compared HERE is a decision made in the only part of the codebase
# with no unit coverage — which is why rendering/decision-adjacent defects are
# the ones that reach production, while the pure-logic package catches its own.
# Comparing a threshold inside stock_analyzer/ is correct and NOT flagged; that
# is where decisions belong and where they are tested.
_RENDER_LAYER = {"app.py", "cron_runner.py"}

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


def _sentinel_read_key(node: ast.AST) -> str | None:
    """Key name for `st.session_state.get("<key>")` with NO default argument.

    A supplied default is deliberately NOT matched here: that form is already
    the OFFLINE_SENTINEL_COLLAPSE rule's business when the default is an empty
    container, and a non-empty default is a considered choice.
    """
    if not isinstance(node, ast.Call) or len(node.args) != 1 or node.keywords:
        return None
    if not (isinstance(node.func, ast.Attribute) and node.func.attr == "get"):
        return None
    base = node.func.value
    if not (isinstance(base, ast.Attribute) and base.attr == "session_state"):
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and arg.value in _SENTINEL_KEYS:
        return str(arg.value)
    return None


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
    def __init__(self, src: str, rel: str = "", policy_consts: frozenset = frozenset()) -> None:
        self.src = src
        self.rel = rel
        # Names imported from stock_analyzer.constants INTO this file. Collected
        # up-front by scan() rather than during traversal, so the rule cannot
        # depend on import statements happening to be visited before the
        # comparisons that use them.
        self.policy_consts = policy_consts
        self.hits: list[tuple[str, str]] = []  # (rule, normalized_segment)
        # varname -> sentinel key it was read from, for SENTINEL_BARE_TRUTHINESS.
        self._sentinel_vars: dict[str, str] = {}
        # Keys whose three-state check is in scope on the current branch, so a
        # downstream bare test is already guarded and must NOT be flagged.
        self._guarded_keys: set[str] = set()
        # id() of `datetime.now()` Call nodes that are the receiver of an
        # `.astimezone()` call. Those ARE tz-aware, so flagging them is a false
        # positive — see the NAIVE_DATE_TODAY branch in visit_Call.
        self._tz_safe_calls: set[int] = set()

    def _seg(self, node: ast.AST) -> str:
        seg = ast.get_source_segment(self.src, node) or ""
        return " ".join(seg.split())  # normalize whitespace/newlines

    def visit_Assign(self, node: ast.Assign) -> None:
        # SENTINEL_BARE_TRUTHINESS, part 1: remember `_v = ss.get("<key>")`.
        key = _sentinel_read_key(node.value)
        if key and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            self._sentinel_vars[node.targets[0].id] = key
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        # An `if _coord_cache_state("<key>") != "ready":` branch handles the
        # missing/offline states explicitly, so any bare test on that key in the
        # SAME if/elif chain is downstream of a real guard. Recognising this here
        # rather than baselining the instance matters: a baseline entry is keyed
        # on the source segment, so deleting the guard would leave the segment
        # unchanged and the gate silently green. This way the guard is what
        # clears the hit, and removing it brings the hit back.
        guarded_here: set[str] = set()
        for sub in ast.walk(node.test):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == "_coord_cache_state"
                    and sub.args
                    and isinstance(sub.args[0], ast.Constant)):
                guarded_here.add(str(sub.args[0].value))
        self._guarded_keys |= guarded_here

        # SENTINEL_BARE_TRUTHINESS, part 2: `if _v:` / `if not _v:` on one.
        test = node.test
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            test = test.operand
        if isinstance(test, ast.Name) and test.id in self._sentinel_vars:
            if self._sentinel_vars[test.id] not in self._guarded_keys:
                self.hits.append((
                    "SENTINEL_BARE_TRUTHINESS",
                    "%s <- session_state.get(%r)" % (test.id, self._sentinel_vars[test.id]),
                ))
        self.generic_visit(node)
        self._guarded_keys -= guarded_here

    def visit_Compare(self, node: ast.Compare) -> None:
        """POLICY_DECISION_IN_RENDER — a threshold comparison in the untested
        entrypoints.

        Not a bug per se: every one of the 95 baselined instances (90
        distinct segments; 5 comparisons are written twice) may be
        perfectly correct today. It is a RATCHET. Each such comparison is an
        investment-policy decision (a breach, a tone classification, a
        staleness call) evaluated where nothing can unit-test it, so the only
        verification it ever gets is a screenshot. Baselined at the current
        count so nothing must be fixed now; a NEW one fails, which makes
        "extract the decision into a pure function in stock_analyzer/ and test
        it there" the path of least resistance rather than a good intention.
        """
        if self.rel in _RENDER_LAYER and self.policy_consts:
            names = {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}
            if names & self.policy_consts:
                self.hits.append(("POLICY_DECISION_IN_RENDER", self._seg(node)))
        self.generic_visit(node)

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
        # `datetime.now().astimezone()` is tz-AWARE — the .astimezone() supplies
        # the offset, and aware-minus-aware arithmetic is correct regardless of
        # which zone each side carries. This visitor reaches the OUTER
        # .astimezone() call before recursing into the inner now(), so mark the
        # inner one safe here rather than flagging it below.
        if (isinstance(node.func, ast.Attribute)
                and node.func.attr == "astimezone"
                and isinstance(node.func.value, ast.Call)):
            self._tz_safe_calls.add(id(node.func.value))
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
            elif node.func.attr == "now" and not node.args and not node.keywords:
                # datetime.now() with no tz argument — naive; datetime.now(tz)
                # with an argument is the correct form and must NOT be flagged.
                base = node.func.value
                base_name = getattr(base, "id", None) or getattr(base, "attr", None)
                if base_name in {"datetime"} and id(node) not in self._tz_safe_calls:
                    self.hits.append(("NAIVE_DATE_TODAY", self._seg(node)))
        self.generic_visit(node)


def policy_constants(tree: ast.AST) -> frozenset:
    """Names imported from `stock_analyzer.constants` into this module.

    EXTRACTED so it can be tested directly (2026-08-28 review). It was inline
    in `scan()`, and four separate mutations to it — dropping `asname`,
    requiring an exact module match, not passing the result through, and
    matching `ast.Import` instead of `ast.ImportFrom` — each took
    POLICY_DECISION_IN_RENDER to zero live hits while passing every test,
    because the tests hand-fed the visitor a constant set instead of exercising
    this. The rule's own implementation was violating the principle the rule
    exists to enforce: a decision that nothing can test.

    Collected up-front rather than during traversal, so the rule cannot depend
    on import statements happening to be visited before the comparisons using
    them.

    KNOWN LIMITS, deliberately not closed — this is a ratchet against
    accidental accretion, not an adversary barrier:
      * attribute form (`from stock_analyzer import constants` then
        `constants.X`, or `import stock_analyzer.constants as C`) collects
        nothing and takes the whole file dark. A one-line, invisible off-switch.
      * `from ...constants import *` yields the literal "*", which matches no
        comparison, with the same effect.
      * a threshold aliased through a local, read via getattr/a dict, or passed
        as a function argument is not a Compare against a collected name.
    The scan()-level tests in tests/test_check_antipatterns.py are what catch a
    collector that has silently stopped collecting; a zero-hit rule is GREEN,
    since the gate only fails on `n > allowed`.
    """
    return frozenset(
        (a.asname or a.name)
        for n in ast.walk(tree)
        if isinstance(n, ast.ImportFrom) and n.module and n.module.endswith("constants")
        for a in n.names
    )


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
        v = _Visitor(src, rel, policy_constants(tree))
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
            "  POLICY_DECISION_IN_RENDER → a policy threshold compared inside app.py/cron_runner.py, "
            "which no test imports. Extract the decision into a pure function in stock_analyzer/ and "
            "test it there; leave the entrypoint as render-only wiring.\n"
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
