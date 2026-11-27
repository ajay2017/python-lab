"""Today's Brief's three cards (tone, Today's Actions, pullback gauge): the equal-height
CSS in stock_analyzer/brief_chips.py and AST pins on app.py's wiring (app.py is never
imported by a test)."""
import ast
import re
from pathlib import Path

import pytest

from stock_analyzer import brief_chips as bc
from tests._ast_cache import parse_file_cached

pytestmark = pytest.mark.fast

_APP = Path(__file__).parent.parent / "app.py"
_SRC = _APP.read_text(encoding="utf-8")

_TIP = (
    "This multiplier reflects each position's own beta under a simulated pullback — "
    "a different calculation from the single \"Portfolio Beta\" figure on "
    "🔗 Risk Analysis, so the two can legitimately differ."
)


# ── the CSS ──────────────────────────────────────────────────────────────────

def _rules(css: str):
    """[(selector_text, body)] for each `selector { body }` in the style text."""
    inner = css.removeprefix("<style>").removesuffix("</style>")
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", inner)]


def _top_level_selectors(selector_text: str):
    """Split a selector list on commas that are not inside parentheses."""
    out, depth, cur = [], 0, ""
    for ch in selector_text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return out


def test_css_is_one_style_block_with_rules():
    css = bc.chips_css()
    assert css.startswith("<style>") and css.endswith("</style>")
    assert len(_rules(css)) >= 6


def test_every_selector_is_scoped_to_the_chips_row_or_the_gauge_card():
    prefixes = (f".st-key-{bc.CHIPS_KEY}", f".st-key-{bc.FRAG_KEY}")
    for selector_text, _body in _rules(bc.chips_css()):
        for sel in _top_level_selectors(selector_text):
            assert sel.startswith(prefixes), f"unscoped selector: {sel!r}"


def test_style_text_holds_no_tag_like_text():
    # st.html's DOMPurify pass (3.4.0, SAFE_FOR_XML) drops a style element whose
    # text contains "<" followed by a letter, "/" or "!".
    inner = bc.chips_css().removeprefix("<style>").removesuffix("</style>")
    assert not re.search(r"<[/\w!]", inner)
    assert "<" not in inner


def test_the_shared_floor_and_card_look_are_in_the_css():
    css = bc.chips_css()
    assert bc.CARD_MIN_HEIGHT_PX == 165
    assert css.count("min-height: 165px") == 2          # HTML cards and the gauge card
    frag = next(body for sel, body in _rules(css) if sel == f".st-key-{bc.FRAG_KEY}")
    for want in ("background: #111827", "border: 1px solid #31333f",
                 "border-radius: 12px", "padding: 14px 20px", "flex: 1 1 auto"):
        assert want in frag, want


def test_columns_and_the_divs_up_to_a_card_grow_as_flex_columns():
    css = bc.chips_css()
    grown = [body for sel, body in _rules(css)
             if ":has(.brief-card, .st-key-brief_frag_chip)" in sel and "display: flex" in body]
    assert len(grown) == 2
    assert all("flex-direction: column" in b for b in grown)
    # the divs between the column and the card also stretch their children, because
    # one Streamlit wrapper is a centred flex row
    assert "align-items: stretch" in grown[1] and "flex: 1 1 auto" in grown[1]
    # the markdown container's negative bottom margin is cancelled
    assert any("stMarkdownContainer" in sel and "margin-bottom: 0" in body
               for sel, body in _rules(css))


# ── app.py wiring ────────────────────────────────────────────────────────────

def _columns_calls(tree):
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "columns" and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "st" and n.args
        and ast.unparse(n.args[0]) == "[4, 3, 5]"
    ]


def test_columns_are_4_3_5_in_tone_actions_gauge_order_inside_the_keyed_row():
    tree = parse_file_cached(_APP)
    cols = _columns_calls(tree)
    assert len(cols) == 1
    assert "st.columns([5, 5, 3])" not in _SRC
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign) and n.value is cols[0]]
    assert len(assigns) == 1
    assert [e.id for e in assigns[0].targets[0].elts] == ["_tone_col", "_act_col", "_frag_col"]
    # the columns call sits inside `with st.container(key=brief_chips.CHIPS_KEY):`
    keyed = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.With)
        and any("brief_chips.CHIPS_KEY" in ast.unparse(i.context_expr) for i in n.items)
    ]
    assert len(keyed) == 1
    assert assigns[0] in list(ast.walk(keyed[0]))


def test_the_style_is_emitted_with_st_html_before_the_row():
    tree = parse_file_cached(_APP)
    html_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "st.html"
        and n.args and ast.unparse(n.args[0]) == "brief_chips.chips_css()"
    ]
    assert len(html_calls) == 1
    row = _columns_calls(tree)[0]
    assert html_calls[0].lineno < row.lineno
    assert "st.markdown(brief_chips.chips_css()" not in _SRC


def test_card_class_in_app_matches_the_css_class():
    assert bc.CARD_CLASS == "brief-card"
    assert _SRC.count("<div class='brief-card' ") == 3        # tone, actions, offline actions
    # the inline floor moved into the shared CSS
    for marker in ("padding:14px 20px;min-height:165px", "min-height:96px"):
        assert marker not in _SRC


def test_gauge_card_is_a_keyed_unbordered_container_drawn_only_when_something_shows():
    tree = parse_file_cached(_APP)
    conts = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "st.container"
        and any(kw.arg == "key" and ast.unparse(kw.value) == "brief_chips.FRAG_KEY"
                for kw in n.keywords)
    ]
    assert len(conts) == 1
    assert any(kw.arg == "border" and ast.unparse(kw.value) == "False" for kw in conts[0].keywords)
    # it sits under `if _frag_ok or not port_df.empty:` so no empty box is drawn
    guards = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.If) and ast.unparse(n.test) == "_frag_ok or not port_df.empty"
    ]
    assert len(guards) == 1 and conts[0] in list(ast.walk(guards[0]))


def test_gauge_caption_is_now_an_escaped_title_tooltip_and_no_longer_a_caption():
    tree = parse_file_cached(_APP)
    # (1) no st.caption carries the text any more
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "st.caption":
            for c in ast.walk(n):
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    assert "multiplier reflects" not in c.value
    # (2) the verbatim text is the argument of _safe_html(...) ...
    safe_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and ast.unparse(n.func) == "_safe_html"
        and n.args and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == _TIP
    ]
    assert len(safe_calls) == 1
    # (3) ... inside the title attribute of a cursor:help span
    assign = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_fg_tip_html"
                                             for t in n.targets)
    )
    assert safe_calls[0] in list(ast.walk(assign))
    consts = "".join(c.value for c in ast.walk(assign.value)
                     if isinstance(c, ast.Constant) and isinstance(c.value, str))
    assert "<span title=" in consts and "cursor:help" in consts
    # (4) the tip follows the lead line in the right-hand HTML
    assert "{_fg_icon} {_fg_lead}{_fg_tip_html}</div>" in _SRC


def test_tone_unread_note_is_inside_the_tone_card_not_a_caption():
    assert "st.caption(\"⚠️ Market tone couldn't be read this run" not in _SRC
    # C4b: the note comes from tone_card_view (an unread read is HELD, never "a flat
    # day"), still the last line inside the card.
    assert "treated as a flat day" not in _SRC
    assert "the bear-day check didn't run" not in _SRC
    assert "_tone_view['note']" in _SRC
    assert "tone_freshness.tone_card_view(_market_context" in _SRC
