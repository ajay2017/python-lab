"""Wiring pins: the Trade Journal's Decision Context answers are reset after
every saved trade, and a 📒 button with no intent resets "Did you follow the
signal?" instead of leaving an earlier answer. app.py is not importable in
tests, so these read its source (AST)."""
import ast
from pathlib import Path

from tests._ast_cache import parse_file_cached, source_segment

_SRC = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")
_TREE = parse_file_cached(Path(__file__).resolve().parent.parent / "app.py")


def _func(name):
    for n in ast.walk(_TREE):
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError(f"{name} not found")


def _str_subscript_assigns(node):
    """(key, value-node) for every st.session_state["k"] = v inside node."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if (isinstance(t, ast.Subscript)
                        and isinstance(t.value, ast.Attribute)
                        and t.value.attr == "session_state"
                        and isinstance(t.slice, ast.Constant)):
                    out.append((t.slice.value, n.value))
    return out


def test_reset_helper_stages_every_context_key_via_pending():
    keys = {k: v for k, v in _str_subscript_assigns(_func("_reset_tj_decision_context"))}
    for base in ("_tj_followed", "_tj_signal_seen", "_tj_lesson_category",
                 "_tj_lesson", "_tj_deviation_reason"):
        assert base not in keys, f"{base} must not be assigned directly"
        if base == "_tj_signal_seen":
            continue
        assert f"{base}_pending" in keys, base
    assert keys["_tj_followed_pending"].value == "— (skip)"
    # signal_seen is DELETED (flag), never blanked: a blank would stick and
    # defeat the widget's auto-fill default for every later trade.
    assert keys["_tj_signal_seen_clear"].value is True
    assert "_tj_signal_seen_pending" not in keys


def test_both_confirm_save_handlers_call_the_reset():
    # Every db.save_trade of a journal-form trade is a Confirm handler; the
    # reset must sit in the same `if <saved>` block as the success cleanup.
    hits = 0
    for n in ast.walk(_TREE):
        if not isinstance(n, ast.If):
            continue
        # innermost block: the submit-signature assignment is a DIRECT statement
        direct = [source_segment(_SRC, s) or "" for s in n.body
                  if isinstance(s, ast.Assign)]
        # Confirm handlers stamp the sig with their own (_ps_/_pb_) ticker; the
        # form's submit stage stamps a dedup sig BEFORE confirm (not a save).
        if any('"_tj_last_submit_sig"' in d and ("(_ps_ticker" in d or "(_pb_ticker" in d)
               for d in direct):
            body_src = "\n".join(source_segment(_SRC, s) or "" for s in n.body)
            assert "_reset_tj_decision_context()" in body_src
            hits += 1
    assert hits == 2


def test_button_without_intent_sets_skip():
    fn = _func("_render_trade_button")
    src = source_segment(_SRC, fn)
    followed_if = next(
        n for n in ast.walk(fn)
        if isinstance(n, ast.If) and isinstance(n.test, ast.Name)
        and n.test.id == "followed_intent"
    )
    assert followed_if.orelse, "no else branch for followed_intent=None"
    else_src = "\n".join(source_segment(_SRC, s) for s in followed_if.orelse)
    assert '"_tj_followed_pending"' in else_src and "— (skip)" in else_src
    assert 'session_state["_tj_followed"]' not in src


def test_pending_keys_consumed_before_widgets_render():
    consume = _SRC.index('"_tj_deviation_reason_pending"') if False else \
        _SRC.index('for _tj_dc_key in ("_tj_lesson_category"')
    widget = _SRC.index('key="_tj_lesson_category"')
    assert consume < widget


def test_signal_seen_clear_runs_before_a_button_staged_value_applies():
    clear = _SRC.index('st.session_state.pop("_tj_signal_seen_clear", False)')
    pop_key = _SRC.index('st.session_state.pop("_tj_signal_seen", None)', clear)
    apply_pending = _SRC.index('if "_tj_signal_seen_pending" in st.session_state:')
    widget = _SRC.index('key="_tj_signal_seen"')
    assert clear < pop_key < apply_pending < widget
