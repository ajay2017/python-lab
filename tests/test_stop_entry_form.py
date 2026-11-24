"""The 🏠 Home "set your own stop" form and the confirmed clear (C2 of
docs/plans/stop-unavailable-holdings.md). `app.py` is not importable, so the two
render functions are cut out of its AST and run against a recording stand-in for
`st` / `db`: what is refused is `young_holding_stop.validate_manual_stop`'s job
(tested in test_young_holding_stop.py); here we pin the wiring."""
import ast
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_analyzer import held_stop_wiring as hw
from stock_analyzer import young_holding_stop as y
from tests._ast_cache import parse_file_cached, source_segment

_APP_PATH = Path(__file__).resolve().parent.parent / "app.py"
_APP = _APP_PATH.read_text(encoding="utf-8")
B = chr(92)


def _fn(name):
    for node in ast.walk(parse_file_cached(_APP_PATH)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return source_segment(_APP, node)
    raise AssertionError(name)


class _Rerun(Exception):
    pass


def _recorder(kind):
    def f(self, text):
        self.log.append((kind, text))
    return f


class _FakeSt:
    """Records what the render functions emit; `values` feed the widgets."""

    def __init__(self, number=None, note="", clicks=(), readonly=False):
        self.number, self.note, self.clicks = number, note, set(clicks)
        self.session_state = {"_readonly": readonly}
        self.log = []
        self.buttons = {}
        self.popovers = []

    def fragment(self, fn):
        return fn

    @contextmanager
    def _ctx(self):
        yield

    def columns(self, spec):
        return [self._ctx() for _ in spec]

    def expander(self, label, expanded=False):
        self.log.append(("expander", label, expanded))
        return self._ctx()

    def popover(self, label, **kw):
        self.popovers.append((label, kw))
        self.log.append(("popover", label))
        return self._ctx()

    def number_input(self, label, **kw):
        self.log.append(("number_input", label, kw))
        return self.number

    def text_input(self, label, **kw):
        self.log.append(("text_input", label, kw))
        return self.note

    def button(self, label, key=None, **kw):
        self.buttons[key] = (label, kw)
        return key in self.clicks

    warning = _recorder("warning")
    info = _recorder("info")
    error = _recorder("error")
    success = _recorder("success")
    caption = _recorder("caption")

    def rerun(self):
        self.log.append(("rerun",))
        raise _Rerun

    def kinds(self, kind):
        return [e[1] for e in self.log if e[0] == kind]


class _FakeDb:
    def __init__(self, save_ok=True, clear_ok=True):
        self.saved, self.cleared = [], []
        self.save_ok, self.clear_ok = save_ok, clear_ok

    def save_manual_stop(self, ticker, stop_price, note=None, source_action=None):
        self.saved.append(dict(ticker=ticker, stop_price=stop_price, note=note,
                               source_action=source_action))
        return self.save_ok

    def clear_manual_stop(self, ticker):
        self.cleared.append(ticker)
        return self.clear_ok


def _ns(st, db, **extra):
    ns = {"st": st, "db": db, "pd": pd, "_young_holding_stop": y,
          "_f": lambda v, default=0.0: _safe(v, default), **extra}
    return ns


def _safe(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _form(st, db):
    ns = _ns(st, db)
    exec(_fn("_render_stop_entry_form"), ns)
    return ns["_render_stop_entry_form"]


def _run(fn, *a):
    try:
        fn(*a)
    except _Rerun:
        pass


# ── the form ─────────────────────────────────────────────────────────────────
def test_form_inputs_are_blank_by_default_and_titled_as_specified():
    st, db = _FakeSt(), _FakeDb()
    _form(st, db)("ANT", 100.0)
    num = next(e for e in st.log if e[0] == "number_input")
    assert num[1] == "Stop price ($)" and num[2]["value"] is None and num[2]["min_value"] == 0.0
    assert any(e[0] == "text_input" and e[1] == "Note (optional)" for e in st.log)
    assert st.kinds("caption") == [y.form_caption()]
    assert st.buttons["_sue_save_ANT"][0] == "💾 Save stop"
    assert db.saved == [] and st.kinds("error") == []


def test_blank_price_then_save_is_refused_and_nothing_is_written():
    st, db = _FakeSt(number=None, clicks={"_sue_save_ANT"}), _FakeDb()
    _form(st, db)("ANT", 100.0)
    assert st.kinds("error") == ["Enter a stop price above " + B + "$0."]
    assert db.saved == [] and ("rerun",) not in st.log


@pytest.mark.parametrize("typed", [100.0, 100.004, 120.0])
def test_a_stop_at_or_above_the_price_is_refused_live_and_on_save(typed):
    st, db = _FakeSt(number=typed, clicks={"_sue_save_ANT"}), _FakeDb()
    _form(st, db)("ANT", 100.0)
    errs = st.kinds("error")
    assert errs == ["A stop at or above today's price (" + B + "$100.00) would trigger at "
                    "once. Enter a price below " + B + "$100.00."]      # once, not twice
    assert db.saved == [] and ("rerun",) not in st.log


def test_a_valid_entry_shows_the_percent_before_saving():
    st, db = _FakeSt(number=80.0), _FakeDb()
    _form(st, db)("ANT", 100.0)
    assert st.kinds("caption")[-1] == "That is 20.0% below today's price (" + B + "$100.00)."
    assert db.saved == [] and st.kinds("error") == []


def test_save_writes_the_rounded_stop_with_the_note_and_source_then_reruns():
    st, db = _FakeSt(number=79.999, note="GTC at the broker", clicks={"_sue_save_ANT"}), _FakeDb()
    _run(_form(st, db), "ANT", 100.0)
    assert db.saved == [{"ticker": "ANT", "stop_price": 80.0, "note": "GTC at the broker",
                         "source_action": "stop_unavailable"}]
    assert st.kinds("success") == ["Stop " + B + "$80.00 saved for ANT."]
    assert st.log[-1] == ("rerun",)


def test_a_blank_note_is_saved_as_none():
    st, db = _FakeSt(number=80.0, note="", clicks={"_sue_save_ANT"}), _FakeDb()
    _run(_form(st, db), "ANT", 100.0)
    assert db.saved[0]["note"] is None


def test_a_failed_write_does_not_rerun_or_claim_success():
    st, db = _FakeSt(number=80.0, clicks={"_sue_save_ANT"}), _FakeDb(save_ok=False)
    _form(st, db)("ANT", 100.0)
    assert len(db.saved) == 1 and st.kinds("success") == [] and ("rerun",) not in st.log


def test_the_read_only_viewer_sees_the_save_button_disabled_with_the_brief_help_text():
    st, db = _FakeSt(number=80.0, readonly=True), _FakeDb()
    _form(st, db)("ANT", 100.0)
    kw = st.buttons["_sue_save_ANT"][1]
    assert kw["disabled"] is True and kw["help"] == "Read-only viewer — changes are disabled"
    st2 = _FakeSt(number=80.0, readonly=False)
    _form(st2, db)("ANT", 100.0)
    assert st2.buttons["_sue_save_ANT"][1]["disabled"] is False
    assert st2.buttons["_sue_save_ANT"][1]["help"] is None


def test_widget_keys_are_per_ticker():
    st, db = _FakeSt(), _FakeDb()
    _form(st, db)("ANT", 100.0)
    keys = [e[2]["key"] for e in st.log if e[0] in ("number_input", "text_input")]
    assert keys == ["_sue_price_ANT", "_sue_note_ANT"]


# ── the notices: form under part 1, confirmed clear on part 2 ───────────────────
def _notices(st, db, held, manual, port_df):
    calls = []
    ns = _ns(st, db, _render_stop_entry_form=lambda t, p: calls.append((t, p)),
             _held_stops_banner_text=lambda rows: None)
    exec(_fn("_render_held_stop_notices"), ns)
    _run(ns["_render_held_stop_notices"], held, manual, port_df)
    return calls


def _unavail(t="ANT", bars=3):
    return {**hw.unavailable_result(t, "x"), "bars": bars, "need": 15}


def _pdf(**prices):
    return pd.DataFrame({"Ticker": list(prices), "Price": list(prices.values())})


def test_a_collapsed_form_per_none_holding_under_the_amber_warning():
    st, db = _FakeSt(), _FakeDb()
    calls = _notices(st, db, {"ANT": _unavail("ANT"), "NEW": _unavail("NEW", 5)}, {},
                     _pdf(ANT=12.5, NEW=7.0))
    assert len(st.kinds("warning")) == 1 and "No stop yet for 2 holdings." in st.kinds("warning")[0]
    assert [e[1:] for e in st.log if e[0] == "expander"] == [
        ("✋ Set your own stop for ANT", False), ("✋ Set your own stop for NEW", False)]
    assert calls == [("ANT", 12.5), ("NEW", 7.0)]
    assert st.kinds("info") == []


def test_a_none_holding_without_a_usable_price_gets_a_caption_not_a_form():
    for px in (None, float("nan"), 0):
        st, db = _FakeSt(), _FakeDb()
        calls = _notices(st, db, {"ANT": _unavail()}, {}, _pdf(ANT=px) if px is not None
                         else pd.DataFrame({"Ticker": [], "Price": []}))
        assert calls == []
        assert any("No price for ANT right now" in c for c in st.kinds("caption"))


def test_manual_holding_gets_the_calm_info_and_a_confirming_clear_popover_not_a_form():
    st, db = _FakeSt(), _FakeDb()
    calls = _notices(st, db, {"ANT": _unavail()}, {"ANT": {"stop_price": 80.0}}, _pdf(ANT=100.0))
    assert calls == [] and len(st.kinds("info")) == 1
    assert "your manual stop " + B + "$80.00 is in force" in st.kinds("info")[0]
    label, kw = st.popovers[0]
    assert len(st.popovers) == 1 and label == "↩️ Clear my stop"
    assert kw["disabled"] is False and kw["help"] is None
    # the confirm text sits inside the popover, the only button is the confirm
    assert st.kinds("warning") == [y.clear_confirm_markdown(
        y.split_stop_rows({"ANT": _unavail()}, {"ANT": {"stop_price": 80.0}})["manual"][0])]
    assert list(st.buttons) == ["_sue_clear_yes_ANT"]
    clabel, ckw = st.buttons["_sue_clear_yes_ANT"]
    assert clabel == "Yes, remove my stop" and ckw["type"] == "primary" and ckw["disabled"] is False


def test_opening_the_popover_alone_clears_nothing():
    st, db = _FakeSt(), _FakeDb()
    _notices(st, db, {"ANT": _unavail()}, {"ANT": {"stop_price": 80.0}}, _pdf(ANT=100.0))
    assert db.cleared == [] and ("rerun",) not in st.log and st.kinds("error") == []


def test_confirm_button_clears_then_reruns():
    st, db = _FakeSt(clicks={"_sue_clear_yes_ANT"}), _FakeDb()
    _notices(st, db, {"ANT": _unavail()}, {"ANT": {"stop_price": 80.0}}, _pdf(ANT=100.0))
    assert db.cleared == ["ANT"] and st.log[-1] == ("rerun",)


def test_a_failed_clear_shows_the_error_and_does_not_rerun():
    st, db = _FakeSt(clicks={"_sue_clear_yes_ANT"}), _FakeDb(clear_ok=False)
    _notices(st, db, {"ANT": _unavail()}, {"ANT": {"stop_price": 80.0}}, _pdf(ANT=100.0))
    assert db.cleared == ["ANT"] and ("rerun",) not in st.log
    assert len(st.kinds("error")) == 1 and "The stop for ANT wasn't cleared" in st.kinds("error")[0]
    assert "Nothing changed." in st.kinds("error")[0]


def test_popover_and_confirm_are_disabled_for_the_read_only_viewer_and_write_nothing():
    st, db = _FakeSt(readonly=True), _FakeDb()
    _notices(st, db, {"ANT": _unavail()}, {"ANT": {"stop_price": 80.0}}, _pdf(ANT=100.0))
    kw = st.popovers[0][1]
    assert kw["disabled"] is True and kw["help"] == "Read-only viewer — changes are disabled"
    assert st.buttons["_sue_clear_yes_ANT"][1]["disabled"] is True
    assert db.cleared == [] and ("rerun",) not in st.log


def test_the_clear_confirm_widget_keys_are_per_ticker():
    st, db = _FakeSt(), _FakeDb()
    held = {"ANT": _unavail("ANT"), "NEW": _unavail("NEW", 5)}
    _notices(st, db, held, {"ANT": {"stop_price": 80.0}, "NEW": {"stop_price": 5.0}},
             _pdf(ANT=100.0, NEW=7.0))
    assert list(st.buttons) == ["_sue_clear_yes_ANT", "_sue_clear_yes_NEW"]


def test_unreadable_manual_table_never_shows_a_manual_stop_but_the_form_stays():
    st, db = _FakeSt(), _FakeDb()
    calls = _notices(st, db, {"ANT": _unavail()}, None, _pdf(ANT=100.0))
    assert st.kinds("info") == [] and st.popovers == [] and st.buttons == {}
    assert calls == [("ANT", 100.0)]


def test_nothing_renders_when_every_stop_is_on_a_full_basis():
    st, db = _FakeSt(), _FakeDb()
    full = {"X": {"ticker": "X", "stop": 90.0, "basis": "full", "label": "Trailing Stop",
                  "note": ""}}
    calls = _notices(st, db, full, {}, _pdf(X=100.0))
    assert calls == [] and st.log == []


# ── wiring that the stand-ins cannot see ─────────────────────────────────────
def test_the_form_is_a_fragment_called_only_from_the_home_notices():
    src = _fn("_render_stop_entry_form")
    assert "@st.fragment" in _APP.split("def _render_stop_entry_form")[0][-40:]
    assert _APP.count("_render_stop_entry_form(") == 2          # the def and the one call
    assert "_render_stop_entry_form(" in _fn("_render_held_stop_notices")
    assert "validate_manual_stop(" in src and "save_manual_stop(" in src
    assert "FORM_SOURCE_ACTION" in src and "_pending_page" not in src


def test_user_guide_has_the_new_listings_paragraph_with_the_floors_from_constants():
    assert "**New listings.** A stop needs **{_TIP_STOP_MIN_BARS} sessions**" in _APP
    assert "composite score needs **{_TIP_TECH_MIN_BARS}**" in _APP
    assert "HELD_STOP_MIN_BARS as _TIP_STOP_MIN_BARS" in _APP
    assert "TECHNICAL_MIN_BARS as _TIP_TECH_MIN_BARS" in _APP
