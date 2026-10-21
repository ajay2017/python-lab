"""C8: the Sunday Weekly Debrief email's "Where the money went" block.

The block is the SAME rows as 💰 Account → Reports → Last week
(`period_attribution.waterfall_rows`, built once in the debrief package) formatted
with the same `usd_plain(value, plus=not total)` the page's `_m_usd` wraps, minus
the privacy mask.  These tests drive the real engine -> package -> email chain."""
from __future__ import annotations

import ast
import html as _h
import re
from pathlib import Path

import pytest

from stock_analyzer import debrief_advisor as da
from stock_analyzer import notify
from stock_analyzer import period_attribution as pa
from stock_analyzer import period_inputs as pi
from tests._ast_cache import parse_file_cached, source_segment
from tests.test_period_attribution import FRI, INT_EV, MON, D, week_world

ROOT = Path(__file__).resolve().parent.parent
SUN = D("2026-10-18")
_ROW = re.compile(r"<tr style='[^']*'><td [^>]*>(.*?)</td><td [^>]*>(.*?)</td></tr>", re.S)


def _core(drop_friday=False, flows=()):
    t, a, dl = week_world()
    if drop_friday:
        a = a[a["snapshot_date"] != FRI.isoformat()]
    inp = {"trades_df": t, "account_snapshots_df": a, "daily_snapshots_df": dl,
           "flows": None if flows is None else list(flows), "income_events": INT_EV}
    return pi.compute_period_core(inp, D("2026-10-12"), SUN, spy_prices_by_date={FRI: 100.0, MON: 101.0},
                                  reference_leverage=2.0), t


def _pkg(core, t):
    return da.build_debrief_package(SUN, core, None, t)


def _debrief(pkg, **kw):
    d = {"week_ending": str(SUN), "generated_at": "2026-10-19T15:00:00+00:00",
         "performance_pct": pkg["performance_pct"], "spy_pct": pkg["spy_pct"],
         "alpha_pct": pkg["alpha_pct"], "metrics_version": 2,
         "section_facts": "Facts.", "section_decisions": "", "section_patterns": "", "section_watchnext": ""}
    d.update(kw)
    return d


def _email(pkg, **kw):
    return notify.render_debrief_email(_debrief(pkg, **kw), period=pkg)


def _cells(html):
    block = html.split("Where the money went", 1)[1].split("</table>", 1)[0]
    out = []
    for lab, val in _ROW.findall(block):
        out.append((_h.unescape(re.sub(r"<[^>]+>", "", lab)), _h.unescape(re.sub(r"<[^>]+>", "", val))))
    return out


def _expected(rows):
    out = []
    for r in rows:
        label = ("↳ " if r["indent"] else "") + r["label"] + (f" ({r['note']})" if r["note"] else "")
        out.append((label, pa.usd_plain(r["value"], plus=not r["total"])))
    return out


# --- the block equals the Reports rows -------------------------------------------------

def test_block_rows_equal_reports_rows_in_order_and_end_with_the_net_change():
    core, t = _core()
    pkg = _pkg(core, t)
    assert pkg["waterfall_rows"] == pa.waterfall_rows(core["recon"])
    cells = _cells(_email(pkg))
    assert cells == _expected(pa.waterfall_rows(core["recon"]))
    assert cells[-1] == ("Net equity change", pa.usd_plain(core["recon"]["equity_change"], plus=False))
    assert cells[-1][1] == "-$115"


def test_caption_names_the_recon_dates_and_the_adds_up_promise():
    core, t = _core()
    html = _email(_pkg(core, t))
    assert "2026-10-09 close → 2026-10-12 close · the unshaded lines add up to the net-equity change" in html
    assert "every line adds up" not in html
    assert ">Where the money went<" in html and "positions only" not in html
    assert "differ from the return above" not in html          # dates agree here


def test_the_caption_promise_is_true_unshaded_rows_sum_to_the_net_change():
    # The shaded rows (breakdown subtotal + its indented split) restate lines
    # above; every OTHER non-total row must sum to the total row (review 2026-10-20).
    core, t = _core()
    rows = pa.waterfall_rows(core["recon"])
    unshaded = [r["value"] for r in rows if not (r["indent"] or r["breakdown"] or r["total"])]
    total = [r["value"] for r in rows if r["total"]][0]
    assert None not in unshaded
    assert sum(unshaded) == pytest.approx(total, abs=0.01)


def test_caption_says_so_when_the_return_and_the_block_use_different_dates():
    core, t = _core()
    pkg = dict(_pkg(core, t), dates_agree=False, return_d0="2026-10-08", return_d1="2026-10-12")
    html = _email(pkg)
    assert "measured over the account records' own dates, which differ from the return above" in html


def test_restating_rows_are_grey_and_never_red_or_green():
    core, t = _core()
    block = _email(_pkg(core, t)).split("Where the money went", 1)[1].split("</table>", 1)[0]
    for tr in re.findall(r"<tr style='[^']*'>.*?</tr>", block, re.S):
        if "↳ " in tr or "(the two lines above)" in tr:
            assert "background:#f8fafc;color:#6b7280" in tr
            assert "#dc2626" not in tr and "#059669" not in tr


def test_block_sits_after_the_tiles_and_before_the_narrative():
    core, t = _core()
    html = _email(_pkg(core, t))
    assert html.index("same figure as") < html.index("Where the money went") < html.index("Facts.")


# --- formatting parity with the Reports page ---------------------------------------------

@pytest.mark.parametrize("value", [-460.2, 0, 4.7, -1313.4, None, 0.2, -0.3, 1234.5])
@pytest.mark.parametrize("total", [False, True])
def test_dollar_cell_text_equals_the_reports_formatter(value, total):
    pkg = {"result_status": "ok", "d0": "a", "d1": "b", "unmeasured": [],
           "waterfall_rows": [{"label": "x", "value": value, "indent": False, "total": total,
                               "breakdown": False, "note": ""}]}
    cell = _cells(notify.render_debrief_email({"metrics_version": 2, "performance_pct": 1.0}, period=pkg))[0][1]
    assert cell == pa.usd_plain(value, plus=not total)
    if value is None:
        assert cell == "unknown"


def test_known_examples_match_the_page():
    assert [pa.usd_plain(v, plus=True) for v in (-460.2, 0, 4.7, -1313.4)] == ["-$460", "$0", "+$5", "-$1,313"]


def test_losses_are_red_gains_green_zero_default():
    pkg = {"result_status": "ok", "d0": "a", "d1": "b",
           "waterfall_rows": [
               {"label": "l", "value": -5.0, "indent": False, "total": False, "breakdown": False, "note": ""},
               {"label": "g", "value": 5.0, "indent": False, "total": False, "breakdown": False, "note": ""},
               {"label": "z", "value": 0.0, "indent": False, "total": False, "breakdown": False, "note": ""}]}
    html = notify.render_debrief_email({"metrics_version": 2, "performance_pct": 1.0}, period=pkg)
    tds = re.findall(r"<td style='[^']*text-align:right;([^']*)'>", html.split("Where the money went")[1])
    assert "color:#dc2626" in tds[0] and "color:#059669" in tds[1] and "color" not in tds[2]


def test_indent_breakdown_and_total_styling_and_notes():
    core, t = _core()
    html = _email(_pkg(core, t))
    block = html.split("Where the money went", 1)[1]
    assert "↳ on borrowed money" in block
    assert re.search(r"<tr style='background:#f8fafc;color:#6b7280;'><td [^>]*font-style:italic", block)
    assert "<tr style='font-weight:700;'>" in block
    assert "<span style='color:#9ca3af'>(posts monthly)</span>" in block


# --- sentinels ------------------------------------------------------------------------------

def test_pre_coverage_week_shows_positions_only_without_total_or_flows():
    core, t = _core(drop_friday=True)
    pkg = _pkg(core, t)
    assert pkg["result_status"] == "pre_coverage" and pkg["performance_pct"] is None
    html = _email(pkg)
    assert "Where the money went — positions only" in html
    assert "The account's own change can't be measured this week" in html
    assert "Net equity change" not in html and "Deposits" not in html and "Unexplained" not in html
    assert _cells(html) == _expected(pa.waterfall_rows(core["recon"]))
    # it follows the amber unknown line
    assert html.index("Account result unknown") < html.index("Where the money went")


def test_offline_engine_has_no_block_at_all():
    pkg = da.build_debrief_package(SUN, None, None, None)
    assert pkg["waterfall_rows"] == []
    html = notify.render_debrief_email({"metrics_version": 2, "performance_pct": None}, period=pkg)
    assert "Where the money went" not in html
    assert "Where the money went" not in notify.render_debrief_email({"metrics_version": 2}, period=None)


def test_v1_email_has_no_block_even_if_period_carries_rows():
    core, t = _core()
    pkg = _pkg(core, t)
    v1 = {"week_ending": str(SUN), "generated_at": "2026-10-19T15:00:00+00:00", "performance_pct": 2.0,
          "spy_pct": 1.0, "alpha_pct": 1.0, "section_facts": "F", "section_decisions": "",
          "section_patterns": "", "section_watchnext": ""}
    html = notify.render_debrief_email(v1, period=pkg)
    assert "Where the money went" not in html
    assert html == notify.render_debrief_email(v1)


# --- escaping / unmeasured -----------------------------------------------------------------------

def test_label_note_and_ticker_are_escaped():
    pkg = {"result_status": "ok", "d0": "<i>", "d1": "b", "unmeasured": ["<b>x</b>"],
           "waterfall_rows": [{"label": "<b>x</b>", "value": 1.0, "indent": False, "total": False,
                               "breakdown": False, "note": "<u>n</u>"}]}
    html = notify.render_debrief_email({"metrics_version": 2, "performance_pct": 1.0}, period=pkg)
    block = html.split("Where the money went", 1)[1]
    assert "<b>x</b>" not in block and "<u>n</u>" not in block and "<i>" not in block
    assert block.count("&lt;b&gt;x&lt;/b&gt;") == 2 and "&lt;u&gt;n&lt;/u&gt;" in block


def test_unmeasured_line_only_when_names_exist():
    core, t = _core()
    clean = _email(_pkg(core, t))
    assert "Not measured" not in clean
    pkg = _pkg(core, t)
    pkg["unmeasured"] = ["MRVL", "AAA"]
    html = _email(pkg)
    assert ("Not measured (P&amp;L not included above): MRVL, AAA — couldn't be priced or replayed "
            "on every day.") in html


# --- header date ------------------------------------------------------------------------------------

def test_header_shows_the_et_day_not_the_utc_slice():
    core, t = _core()
    html = _email(_pkg(core, t), generated_at="2026-10-21T01:30:00+00:00")
    assert "Generated 2026-10-20" in html and "Generated 2026-10-21" not in html


def test_header_for_a_missing_timestamp_is_a_dash():
    core, t = _core()
    assert "Generated —" in _email(_pkg(core, t), generated_at=None)


# --- one source of truth, pinned at both ends ---------------------------------------------------------

def test_reports_page_and_email_share_the_rows_and_the_formatter():
    app = ROOT / "app.py"
    text = app.read_text(encoding="utf-8")
    tree = parse_file_cached(app)
    wf = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
          and n.func.attr == "waterfall_rows" and isinstance(n.func.value, ast.Name)
          and n.func.value.id == "_pa_mod"]
    assert len(wf) == 1
    # the page's dollar cell is _m_usd(value, plus=not total), and _m_usd wraps usd_plain(v, plus=plus)
    seg = source_segment(text, next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                                    and n.name == "_m_usd"))
    assert "_pa_mod.usd_plain(v, plus=plus)" in seg
    assert '_m_usd(_w["value"], plus=not _w["total"])' in text
    # the email: the package builds its rows with the same function; notify formats with the same rule
    da_tree = parse_file_cached(ROOT / "stock_analyzer" / "debrief_advisor.py")
    assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "waterfall_rows"
               for n in ast.walk(da_tree))
    nt = parse_file_cached(ROOT / "stock_analyzer" / "notify.py")
    mb = next(n for n in ast.walk(nt) if isinstance(n, ast.FunctionDef) and n.name == "_money_block")
    calls = [ast.unparse(n) for n in ast.walk(mb) if isinstance(n, ast.Call)]
    assert any(c.startswith("_pa.usd_plain(") and "plus=not total" in c for c in calls)
