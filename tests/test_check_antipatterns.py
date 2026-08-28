"""Tests for scripts/check_antipatterns.py — the recurring-defect CI gate added
2026-08-04. Locks in the AST detection semantics (a false negative would let a
new bug-class instance ship; a false positive would erode trust in the gate)
and the baseline round-trip. The gate script lives in scripts/, not the package,
so it's loaded by path.
"""
import ast
import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_antipatterns.py"
_spec = importlib.util.spec_from_file_location("check_antipatterns", _SCRIPT)
ca = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ca)


def _rules(code: str) -> set:
    v = ca._Visitor(code)
    v.visit(ast.parse(code))
    return {rule for rule, _ in v.hits}


class TestOfflineSentinel:
    @pytest.mark.parametrize("code", [
        'x = st.session_state.get("k") or []',
        'x = d.get("k") or {}',
        'x = obj.get(key) or ()',
        'x = (held_data.get(t) or {}).get("m") or {}',  # nested — both flagged
    ])
    def test_flags_get_or_empty(self, code):
        assert "OFFLINE_SENTINEL_COLLAPSE" in _rules(code)

    @pytest.mark.parametrize("code", [
        'x = st.session_state.get("k")',        # explicit read — the safe path
        'x = st.session_state.get("k", [])',     # default arg is fine
        'x = some_list or []',                    # not a .get() — not the class
        'x = d.get("k") or compute()',            # non-empty fallback — not it
    ])
    def test_ignores_safe_reads(self, code):
        assert "OFFLINE_SENTINEL_COLLAPSE" not in _rules(code)

    # ── 2026-08-24 audit: the ternary (IfExp) form is the same collapse as
    # `or []`, just spelled differently — a live instance (app.py's F-252
    # broker-drift cross-reference) went undetected until this was added. ──

    @pytest.mark.parametrize("code", [
        'x = d.get("k", []) if isinstance(d, dict) else []',   # get-then-empty
        'x = [] if not isinstance(d, dict) else d.get("k", [])',  # empty-then-get
        'x = d.get("k") if cond else {}',                       # bare .get(), dict default
        'x = () if cond else obj.get(key)',                     # tuple default, reversed
    ])
    def test_flags_ternary_get_or_empty(self, code):
        assert "OFFLINE_SENTINEL_COLLAPSE" in _rules(code)

    @pytest.mark.parametrize("code", [
        'x = d.get("k") if cond else compute()',   # non-empty fallback — not it
        'x = other if cond else d.get("k")',        # neither side is empty
        'x = a if cond else b',                     # no .get() at all
    ])
    def test_ignores_safe_ternaries(self, code):
        assert "OFFLINE_SENTINEL_COLLAPSE" not in _rules(code)


class TestUnsafeHtml:
    @pytest.mark.parametrize("code", [
        'st.markdown(f"<b>{x}</b>", unsafe_allow_html=True)',        # f-string
        'st.markdown("<b>" + x + "</b>", unsafe_allow_html=True)',    # concat
        'st.markdown(html_str, unsafe_allow_html=True)',             # bare name
        'st.markdown("<b>{}</b>".format(x), unsafe_allow_html=True)',  # .format
    ])
    def test_flags_dynamic_html(self, code):
        assert "UNSAFE_HTML_DYNAMIC" in _rules(code)

    @pytest.mark.parametrize("code", [
        'st.markdown("<b>static</b>", unsafe_allow_html=True)',   # literal — safe
        'st.markdown(f"<b>{x}</b>")',                              # no unsafe flag
        'st.markdown(f"<b>{x}</b>", unsafe_allow_html=False)',     # flag False
    ])
    def test_ignores_safe_html(self, code):
        assert "UNSAFE_HTML_DYNAMIC" not in _rules(code)


class TestDateMath:
    def test_flags_utcnow(self):
        assert "NAIVE_UTCNOW" in _rules("t = datetime.utcnow()")

    @pytest.mark.parametrize("code", ["t = date.today()", "t = datetime.today()"])
    def test_flags_naive_today(self, code):
        assert "NAIVE_DATE_TODAY" in _rules(code)

    @pytest.mark.parametrize("code", [
        "t = datetime.now(_ET)",       # tz-aware — the safe path
        "t = now_et()",                # the shared helper
        "t = today_et()",
        "t = some.today(x)",            # .today(arg) — not the bare-call class
    ])
    def test_ignores_tz_aware(self, code):
        r = _rules(code)
        assert "NAIVE_UTCNOW" not in r and "NAIVE_DATE_TODAY" not in r


class TestBaselineRoundTrip:
    def test_serialize_load_round_trip(self, tmp_path, monkeypatch):
        scanned = {"a.py": Counter({("NAIVE_UTCNOW", "datetime.utcnow()"): 2})}
        baseline_file = tmp_path / "baseline.json"
        monkeypatch.setattr(ca, "BASELINE", baseline_file)
        baseline_file.write_text(
            json.dumps({"instances": ca._serialize(scanned)}), encoding="utf-8"
        )
        loaded = ca._load_baseline()
        assert loaded["a.py"][("NAIVE_UTCNOW", "datetime.utcnow()")] == 2

    def test_real_repo_is_green_against_committed_baseline(self):
        # The committed baseline must cover the current tree — a red default
        # would make the gate meaningless. Same invariant CI runs.
        scanned = ca.scan()
        baseline = ca._load_baseline()
        new = []
        for rel, ctr in scanned.items():
            base = baseline.get(rel, Counter())
            for key, n in ctr.items():
                if n > base.get(key, 0):
                    new.append((rel, key))
        assert not new, f"baseline out of date — regenerate: {new[:5]}"


class TestSentinelBareTruthiness:
    """The D9 form (2026-08-26 app review): the read is clean, and the None is
    destroyed one line later by truthiness. Structurally invisible to
    TestOfflineSentinel's rule, which is why it needed its own."""

    def test_flags_bare_if_on_a_sentinel_read(self):
        code = (
            '_sg = st.session_state.get("_div_recs_cache")\n'
            'if _sg:\n'
            '    pass\n'
        )
        assert "SENTINEL_BARE_TRUTHINESS" in _rules(code)

    def test_flags_negated_form(self):
        code = (
            '_sg = st.session_state.get("_reduce_calls")\n'
            'if not _sg:\n'
            '    pass\n'
        )
        assert "SENTINEL_BARE_TRUTHINESS" in _rules(code)

    @pytest.mark.parametrize("code", [
        # Not a documented coordination key — no None-on-failure contract.
        '_v = st.session_state.get("some_other_key")\nif _v:\n    pass\n',
        # A supplied default is the sibling rule's business, not this one.
        '_v = st.session_state.get("_div_recs_cache", [])\nif _v:\n    pass\n',
        # An explicit `is None` check is the correct idiom.
        '_v = st.session_state.get("_div_recs_cache")\nif _v is None:\n    pass\n',
        # Not a session_state read at all.
        '_v = d.get("_div_recs_cache")\nif _v:\n    pass\n',
    ])
    def test_ignores_non_instances(self, code):
        assert "SENTINEL_BARE_TRUTHINESS" not in _rules(code)

    def test_a_coord_cache_state_guard_clears_the_hit(self):
        """The guard is what silences the rule — NOT a baseline entry. Baselining
        is keyed on the source segment, so deleting the guard would leave the
        segment identical and the gate silently green; this way the hit returns."""
        guarded = (
            '_sg = st.session_state.get("_div_recs_cache")\n'
            'if _coord_cache_state("_div_recs_cache") != "ready":\n'
            '    pass\n'
            'elif _sg:\n'
            '    pass\n'
        )
        assert "SENTINEL_BARE_TRUTHINESS" not in _rules(guarded)

    def test_removing_that_guard_brings_the_hit_back(self):
        unguarded = (
            '_sg = st.session_state.get("_div_recs_cache")\n'
            'if False:\n'
            '    pass\n'
            'elif _sg:\n'
            '    pass\n'
        )
        assert "SENTINEL_BARE_TRUTHINESS" in _rules(unguarded)

    def test_a_guard_on_a_DIFFERENT_key_does_not_clear_the_hit(self):
        """Guard-awareness must be key-specific, or one guard would launder every
        bare test in the same if/elif chain."""
        code = (
            '_sg = st.session_state.get("_div_recs_cache")\n'
            'if _coord_cache_state("_reduce_calls") != "ready":\n'
            '    pass\n'
            'elif _sg:\n'
            '    pass\n'
        )
        assert "SENTINEL_BARE_TRUTHINESS" in _rules(code)

    def test_guard_scope_does_not_leak_past_the_chain(self):
        """A guarded chain must not silence a later, unrelated bare test."""
        code = (
            '_sg = st.session_state.get("_div_recs_cache")\n'
            'if _coord_cache_state("_div_recs_cache") != "ready":\n'
            '    pass\n'
            'elif _sg:\n'
            '    pass\n'
            'if _sg:\n'
            '    pass\n'
        )
        assert "SENTINEL_BARE_TRUTHINESS" in _rules(code)

    def test_guard_does_not_launder_a_bare_test_inside_its_own_branch(self):
        """Reviewer finding (2026-08-26): the guard clears bare tests in the SAME
        chain, but generic_visit also descends into the guard branch's own body —
        which is the degraded branch, precisely where a bare test IS the defect.
        Documented as a known limit; this test pins the behaviour so it cannot
        change silently."""
        code = (
            '_sg = st.session_state.get("_div_recs_cache")\n'
            'if _coord_cache_state("_div_recs_cache") != "ready":\n'
            '    if _sg:\n'
            '        pass\n'
        )
        # KNOWN LIMIT, not an assertion that this is correct: the nested test is
        # cleared. If a future change makes the guard body-aware, flip this to
        # `in` — do not delete the case.
        assert "SENTINEL_BARE_TRUTHINESS" not in _rules(code)

    def test_a_bare_test_in_a_sibling_function_is_not_cleared(self):
        """_sentinel_vars is file-scoped, not per-function. Pins that a read in one
        function and a bare test in another still flags (the conservative
        direction) rather than silently crossing over into a miss."""
        code = (
            'def a():\n'
            '    _sg = st.session_state.get("_div_recs_cache")\n'
            'def b():\n'
            '    if _sg:\n'
            '        pass\n'
        )
        assert "SENTINEL_BARE_TRUTHINESS" in _rules(code)


def _rules_in(code: str, rel: str, consts=("COMPOSITE_BUY",)) -> set:
    """_rules() but for a file-scoped rule — POLICY_DECISION_IN_RENDER depends
    on WHICH file the code is in, which the plain helper cannot express."""
    v = ca._Visitor(code, rel, frozenset(consts))
    v.visit(ast.parse(code))
    return {r for r, _ in v.hits}


class TestPolicyDecisionInRender:
    """A RATCHET, not a bug detector (2026-08-28). Every one of the 95
    baselined instances (90 distinct segments) may be correct today. The point is that app.py is
    38,197 lines that NO test imports, so a policy threshold evaluated there
    gets no verification beyond a screenshot — which is precisely why
    rendering/decision-adjacent defects are the ones that reach production
    while the pure-logic package catches its own. Baselined so nothing must be
    fixed now; a NEW one fails, making extraction the path of least resistance
    instead of a good intention that competes with shipping.
    """

    _DECISION = "x = score >= COMPOSITE_BUY"

    def test_fires_in_the_untested_entrypoints(self):
        assert "POLICY_DECISION_IN_RENDER" in _rules_in(self._DECISION, "app.py")
        assert "POLICY_DECISION_IN_RENDER" in _rules_in(self._DECISION, "cron_runner.py")

    def test_does_not_fire_in_the_pure_logic_package(self):
        """The load-bearing half. Thresholds SHOULD be compared in
        stock_analyzer/ — that is where decisions belong and where they are
        tested. A rule that flagged those would push logic the wrong way."""
        for rel in ("stock_analyzer/daily_briefing.py", "stock_analyzer/risk.py"):
            assert "POLICY_DECISION_IN_RENDER" not in _rules_in(self._DECISION, rel)

    def test_only_policy_constants_count_not_arbitrary_names(self):
        """A comparison against a local or a literal is not a policy decision."""
        assert "POLICY_DECISION_IN_RENDER" not in _rules_in("x = score >= 65", "app.py")
        assert "POLICY_DECISION_IN_RENDER" not in _rules_in("x = a >= b", "app.py")

    def test_detects_the_constant_on_either_side_and_in_compound_tests(self):
        for code in ("x = COMPOSITE_BUY <= score",
                     "x = lo < COMPOSITE_BUY < hi",
                     "x = (score >= COMPOSITE_BUY * 0.9)"):
            assert "POLICY_DECISION_IN_RENDER" in _rules_in(code, "app.py"), code

    def test_an_empty_constant_set_cannot_fire(self):
        """A file that imports no policy constants has no policy decision to
        make — guards against flagging every comparison in the entrypoint."""
        assert "POLICY_DECISION_IN_RENDER" not in _rules_in(
            self._DECISION, "app.py", consts=()
        )

    def test_the_real_render_layer_scope_is_exactly_the_two_entrypoints(self):
        """Pinned by name: silently widening this to stock_analyzer/ would
        invert the rule's intent, and silently narrowing it would disable it."""
        assert ca._RENDER_LAYER == {"app.py", "cron_runner.py"}


class TestPolicyConstCollectorAgainstTheRealRepo:
    """BLOCKING review finding, 2026-08-28 — the tests above cover the VISITOR
    and hand it a hardcoded constant set, so they said nothing about the
    COLLECTOR in scan(). Four separate mutations to that collector passed all
    six of them while taking the rule to ZERO live hits:

        (a.asname or a.name) -> a.name          loses 37 aliased names
        endswith("constants") -> == "constants" 0 hits
        policy_consts not passed to _Visitor    0 hits
        ast.ImportFrom -> ast.Import            0 hits

    And the failure is SILENTLY GREEN: main() only fails on `n > allowed`, so a
    rule that stops finding anything passes. That is the same shape as the
    .venv fail-open in feedback_hook_enforcement — a gate whose broken state is
    indistinguishable from a clean one.

    These tests assert against real scan() output, which is the only thing that
    can catch a collector that has quietly stopped collecting.
    """

    # scan() walks the whole package; cached so three assertions cost one pass.
    _CACHE = {}

    def _scan(self):
        if "r" not in self._CACHE:
            self._CACHE["r"] = ca.scan()
        return self._CACHE["r"]

    def test_the_rule_still_finds_real_instances_in_app_py(self):
        """The general fix for silent-fail-open on this rule: if app.py ever
        reports ZERO policy decisions, the collector broke — the file has ~92
        and they are not going to vanish in one commit."""
        hits = sum(n for (rule, _), n in self._scan().get("app.py", {}).items()
                   if rule == "POLICY_DECISION_IN_RENDER")
        assert hits > 50, (
            f"app.py reports {hits} POLICY_DECISION_IN_RENDER hits. A collapse to "
            "near-zero means scan()'s policy-constant collector stopped working, "
            "not that the debt was paid off."
        )

    def test_the_collector_resolves_aliased_imports(self):
        """`from ... import X as _Y` — dropping `asname` silently loses most of
        the rule's reach in app.py with no other test failing.

        Calls the REAL ca.policy_constants(). An earlier version of this test
        re-implemented the collection logic inline, so it tested a COPY and
        passed happily while the real collector was mutated — the vacuous-test
        trap, inside the test written to prevent it. That is also why the
        collector had to be extracted from scan() to be callable at all."""
        import ast as _ast
        import pathlib
        tree = _ast.parse(
            (pathlib.Path(ca.ROOT) / "app.py").read_text(encoding="utf-8-sig")
        )
        consts = ca.policy_constants(tree)
        assert "COMPOSITE_BUY" in consts, "plain import not collected"
        aliased = {c for c in consts if c.startswith("_")}
        assert len(aliased) > 20, (
            f"only {len(aliased)} aliased policy constants collected — `asname` "
            "dropped? app.py aliases dozens (_C_SOFT, _CW, _RO_VIX, ...), and "
            "losing them silently removes most of the rule's reach."
        )

    def test_the_pure_logic_package_stays_unflagged_in_a_real_scan(self):
        """Scope, against the real tree rather than a synthetic snippet: a rule
        that crept into stock_analyzer/ would push decisions the wrong way."""
        offenders = [
            rel for rel, counter in self._scan().items()
            if rel not in ca._RENDER_LAYER
            and any(rule == "POLICY_DECISION_IN_RENDER" for rule, _ in counter)
        ]
        assert not offenders, f"rule fired outside the render layer: {offenders}"
