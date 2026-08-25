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
