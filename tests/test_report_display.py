"""stock_analyzer.report_display: pure display helpers for the 📄 Reports page."""
import re

from stock_analyzer import report_display as rd

NB = " "


# ─── report_markdown ─────────────────────────────────────────────────────────

def test_bullet_dot_with_and_without_space():
    out = rd.report_markdown("• one\n•two")
    assert out == "- one\n- two"


def test_dash_and_star_bullets():
    assert rd.report_markdown("- a\n* b") == "- a\n- b"


def test_prose_then_bullets_gets_blank_line_before_list():
    out = rd.report_markdown("Intro line\n• one\n• two")
    assert out == "Intro line\n\n- one\n- two"


def test_blank_line_already_present_is_not_doubled():
    assert rd.report_markdown("Intro\n\n• one") == "Intro\n\n- one"


def test_prose_after_list_is_separated_from_the_last_item():
    out = rd.report_markdown("• one\nTail prose")
    assert out == "- one\n\nTail prose"


def test_pure_prose_is_unchanged():
    t = "What happened.\nSecond line."
    assert rd.report_markdown(t) == t


def test_negative_number_without_space_is_not_a_bullet():
    assert rd.report_markdown("-5% on the week") == "-5% on the week"
    assert rd.report_markdown("-$40 loss") == "-\\$40 loss"


def test_bold_line_is_not_a_star_bullet():
    assert rd.report_markdown("**Heading**") == "**Heading**"


def test_dollars_are_escaped_and_idempotent():
    out = rd.report_markdown("• Lost $40 on X, gained $10 on Y")
    assert out == "- Lost \\$40 on X, gained \\$10 on Y"
    assert rd.report_markdown(out) == out


def test_none_and_nan_read_empty():
    assert rd.report_markdown(None) == ""
    assert rd.report_markdown(float("nan")) == ""


def test_empty_bullet_marker_is_left_alone():
    assert rd.report_markdown("•") == "•"


def test_single_bullet_line_at_start_has_no_leading_blank():
    assert rd.report_markdown("• only") == "- only"


# ─── week_tick_label ─────────────────────────────────────────────────────────

def test_week_tick_label_no_leading_zero():
    assert rd.week_tick_label("2026-11-08") == "wk to Nov 8"
    assert rd.week_tick_label("2026-10-25") == "wk to Oct 25"
    assert rd.week_tick_label("2026-11-01T00:00:00") == "wk to Nov 1"


def test_week_tick_label_unparseable_keeps_raw():
    assert rd.week_tick_label("garbage") == "garbage"
    assert rd.week_tick_label("2026-13-45") == "2026-13-45"
    assert rd.week_tick_label("") == ""
    assert rd.week_tick_label(None) == ""


# ─── monthly tiles ───────────────────────────────────────────────────────────

def test_avg_delta_text_starts_with_the_sign():
    assert rd.avg_delta_text(2.1) == "+2.1 pp avg vs SPY"
    assert rd.avg_delta_text(-0.8) == "-0.8 pp avg vs SPY"
    assert rd.avg_delta_text(-0.8)[0] == "-"
    assert rd.avg_delta_text(0.0) == "+0.0 pp avg vs SPY"


def test_avg_delta_text_none_nan_and_junk_draw_no_delta():
    assert rd.avg_delta_text(None) is None
    assert rd.avg_delta_text(float("nan")) is None
    assert rd.avg_delta_text("abc") is None


def test_skipped_avg_state_saved_value():
    assert rd.skipped_avg_state({"avg_skipped_names_alpha": -0.8}, True) == (-0.8, True)


def test_skipped_avg_state_saved_none_is_saved_without_a_figure():
    assert rd.skipped_avg_state({"avg_skipped_names_alpha": None}, True) == (None, True)


def test_skipped_avg_state_older_report_or_live_fallback_is_not_saved():
    assert rd.skipped_avg_state({"flow": {}}, True) == (None, False)      # key absent
    assert rd.skipped_avg_state({"avg_skipped_names_alpha": -0.8}, False) == (None, False)   # live path
    assert rd.skipped_avg_state(None, True) == (None, False)


def test_monthly_caption_suffix_always_says_what_each_average_covers():
    t = rd.monthly_tiles_caption_suffix(2.1, -0.8, True)
    assert "Bought average: vs SPY in percentage points over the matured surfacings you acted on." in t
    assert "Skipped average: over every matured surfacing of the names you never bought." in t
    assert "wasn't saved" not in t and "No matured" not in t


def test_monthly_caption_suffix_not_saved_clause():
    t = rd.monthly_tiles_caption_suffix(2.1, None, False)
    assert "Skipped names' average vs SPY wasn't saved with this month's report." in t
    assert "No matured skipped names" not in t      # unsaved is not "saved but empty"


def test_monthly_caption_suffix_unavailable_but_expected_clauses():
    assert " No matured bought names to average yet." in rd.monthly_tiles_caption_suffix(None, -0.8, True)
    assert " No matured bought names to average yet." in rd.monthly_tiles_caption_suffix(float("nan"), -0.8, True)
    t = rd.monthly_tiles_caption_suffix(2.1, None, True)
    assert " No matured skipped names to average yet." in t and "No matured bought" not in t
    both = rd.monthly_tiles_caption_suffix(None, None, True)
    assert "No matured bought names" in both and "No matured skipped names" in both


# ─── money_table_markdown ────────────────────────────────────────────────────

def _row(label="L", value=1.0, plus=True, bold=False, indent=False, note=""):
    return {"label": label, "value": value, "plus": plus, "bold": bold,
            "indent": indent, "note": note}


def _body(md):
    return md.split("\n")[2:]


def test_header_and_alignment_row():
    md = rd.money_table_markdown("Where", [], mask=False)
    assert md.split("\n") == ["| Where | $ |", "|---|--:|"]


def test_signs_and_colours():
    md = rd.money_table_markdown("H", [
        _row("up", 212.0), _row("down", -642.0), _row("zero", 0.0), _row("none", None),
    ], mask=False)
    up, down, zero, none = _body(md)
    assert up == "| up | :green[+\\$212] |"
    assert down == "| down | :red[-\\$642] |"
    assert zero == "| zero | :gray[\\$0] |"
    assert none == "| none | :gray[unknown] |"


def test_plus_false_drops_the_plus_sign():
    md = rd.money_table_markdown("H", [_row("tot", 148.0, plus=False)], mask=False)
    assert ":green[\\$148]" in md and "+" not in md.split("\n")[2]


def test_sub_cent_residue_reads_zero_in_grey_not_green():
    md = rd.money_table_markdown("H", [_row("tiny", 0.001)], mask=False)
    assert ":gray[\\$0]" in md


def test_nan_and_inf_read_unknown():
    md = rd.money_table_markdown("H", [_row("a", float("nan")), _row("b", float("inf"))], mask=False)
    assert _body(md) == ["| a | :gray[unknown] |", "| b | :gray[unknown] |"]


def test_mask_greys_everything_and_hides_digits():
    md = rd.money_table_markdown("H", [
        _row("up", 212.0), _row("down", -642.0), _row("zero", 0.0), _row("none", None),
    ], mask=True)
    body = "\n".join(_body(md))
    assert not re.search(r"\d", body)
    assert ":red[" not in body and ":green[" not in body
    assert body.count(":gray[••••••]") == 3
    assert ":gray[unknown]" in body        # None is not a figure: stays readable


def test_bold_wraps_label_and_amount():
    md = rd.money_table_markdown("H", [_row("Net equity change", -642.0, plus=False, bold=True)], mask=False)
    assert _body(md)[0] == "| **Net equity change** | **:red[-\\$642]** |"


def test_indent_prefix_uses_literal_nbsp_characters():
    md = rd.money_table_markdown("H", [_row("on borrowed money", -585.0, indent=True)], mask=False)
    line = _body(md)[0]
    assert line.startswith("| " + NB * 4 + "↳ on borrowed money |")
    assert "&nbsp;" not in line


def test_bold_indent_keeps_nbsp_outside_the_bold_markers():
    md = rd.money_table_markdown("H", [_row("x", 1.0, indent=True, bold=True)], mask=False)
    line = _body(md)[0]
    assert line.startswith("| " + NB * 4 + "**↳ x**")


def test_note_is_grey_after_the_label():
    md = rd.money_table_markdown("H", [_row("Deposits / withdrawals", 0.0, note="none recorded")], mask=False)
    assert _body(md)[0].startswith("| Deposits / withdrawals :gray[(none recorded)] |")


def test_pipes_and_dollars_in_labels_are_escaped():
    md = rd.money_table_markdown("H", [_row("a | b costs $5", 1.0)], mask=False)
    assert "a \\| b costs \\$5" in md


def test_no_bare_dollar_survives_anywhere():
    md = rd.money_table_markdown("Cost $", [
        _row("x $1 and $2", 1234.0, note="$3"), _row("y", -5.0, bold=True),
    ], mask=False)
    assert not re.search(r"(?<!\\)\$", md.replace("| $ |", "|  |"))


# ─── waterfall_markdown ──────────────────────────────────────────────────────

def _wf(label, value, indent=False, total=False, breakdown=False, note=""):
    return {"label": label, "value": value, "indent": indent, "total": total,
            "breakdown": breakdown, "note": note}


def test_waterfall_maps_flags_onto_the_table():
    rows = [
        _wf("Price moves", -1104.0),
        _wf("Moves at prior close", -892.0, breakdown=True, note="the two lines above"),
        _wf("on your own money", -307.0, indent=True),
        _wf("Net equity change", -642.0, total=True),
    ]
    lines = rd.waterfall_markdown(rows, mask=False).split("\n")
    assert lines[0] == "| Where the change came from | $ |"
    assert lines[2] == "| Price moves | :red[-\\$1,104] |"
    # breakdown row: bold, signed like a normal line, note outside the bold
    assert lines[3] == "| **Moves at prior close** :gray[(the two lines above)] | **:red[-\\$892]** |"
    assert lines[4].startswith("| " + NB * 4 + "↳ on your own money |")
    # total: bold and NOT plus-signed
    assert lines[5] == "| **Net equity change** | **:red[-\\$642]** |"


def test_waterfall_plus_flag_is_not_total():
    lines = rd.waterfall_markdown([_wf("Dividends", 9.0), _wf("Net", 9.0, total=True)], mask=False).split("\n")
    assert ":green[+\\$9]" in lines[2]
    assert ":green[\\$9]" in lines[3] and "+" not in lines[3]


def test_waterfall_masked_hides_all_digits_in_amounts():
    rows = [_wf("Dividends", 9.0), _wf("Net", -642.0, total=True)]
    lines = rd.waterfall_markdown(rows, mask=True).split("\n")
    assert ":gray[••••••]" in lines[2] and ":gray[••••••]" in lines[3]
    assert not any(c in lines[2].split("|")[2] + lines[3].split("|")[2] for c in "0123456789")


def test_waterfall_empty_is_header_only():
    assert rd.waterfall_markdown([], mask=False).count("\n") == 1


# ─── tax helpers ─────────────────────────────────────────────────────────────

def test_tax_money_text():
    assert rd.tax_money_text(1520.0, mask=False) == "$1,520.00"
    assert rd.tax_money_text(936.12, mask=False) == "$936.12"
    assert rd.tax_money_text(-583.88, mask=False) == "-$583.88"
    assert rd.tax_money_text(0.0, mask=False) == "$0.00"
    assert rd.tax_money_text(-0.001, mask=False) == "$0.00"
    assert rd.tax_money_text(None, mask=False) == "—"
    assert rd.tax_money_text(float("nan"), mask=False) == "—"


def test_tax_money_text_mask():
    assert rd.tax_money_text(1520.0, mask=True) == "••••••"
    assert rd.tax_money_text(-5.0, mask=True) == "••••••"
    assert rd.tax_money_text(None, mask=True) == "—"


def test_gain_css():
    assert rd.gain_css(78.5, mask=False) == "color: #4ade80"
    assert rd.gain_css(-0.5, mask=False) == "color: #f87171"
    assert rd.gain_css(0.0, mask=False) == ""
    assert rd.gain_css(None, mask=False) == ""
    assert rd.gain_css(float("nan"), mask=False) == ""
    assert rd.gain_css(78.5, mask=True) == ""
    assert rd.gain_css(-78.5, mask=True) == ""


def _lot(proceeds, cost, gain, status=None):
    return {"proceeds": proceeds, "cost": cost, "gain": gain,
            "wash_sale_status": ({"status": status} if status else None)}


def test_tax_total_row_sums_and_counts():
    rows = [
        _lot(936.12, 1520.0, -583.88, "violation"),
        _lot(612.4, 655.11, -42.71, "clean"),
        _lot(566.8, 488.3, 78.5),
    ]
    t = rd.tax_total_row(rows)
    assert round(t["proceeds"], 2) == 2115.32
    assert round(t["cost"], 2) == 2663.41
    assert round(t["gain"], 2) == -548.09
    assert t["n_unknown_gain"] == 0
    assert t["n_wash"] == 1


def test_tax_total_row_unknown_gain_is_left_out_and_counted():
    rows = [_lot(100.0, 80.0, 20.0), _lot(50.0, None, None, "pending")]
    t = rd.tax_total_row(rows)
    assert t["gain"] == 20.0 and t["n_unknown_gain"] == 1
    # the unknown-gain lot is left out of EVERY total (Proceeds - Cost = Gain)
    assert t["proceeds"] == 100.0 and t["cost"] == 80.0
    assert t["proceeds"] - t["cost"] == t["gain"]
    assert t["n_wash"] == 0


def test_tax_total_table_row_cells():
    rows = [_lot(936.12, 1520.0, -583.88, "violation"), _lot(566.8, 488.3, 78.5)]
    t = rd.tax_total_table_row(rows)
    assert t["Ticker"] == "Total"
    assert t["Wash Sale"] == "1 wash sale(s)"
    assert round(t["Gain"], 2) == -505.38
    assert set(t) == {"Ticker", "Buy Date", "Sell Date", "Shares", "Proceeds", "Cost",
                      "Gain", "Days", "Term", "Wash Sale"}


def test_tax_total_table_row_unknown_and_no_wash():
    t = rd.tax_total_table_row([_lot(100.0, 80.0, 20.0), _lot(50.0, None, None)])
    assert t["Ticker"] == "Total (excl. 1 lot with unknown cost)"
    t2 = rd.tax_total_table_row([_lot(1.0, None, None), _lot(2.0, None, None), _lot(100.0, 80.0, 20.0)])
    assert t2["Ticker"] == "Total (excl. 2 lots with unknown cost)"
    assert t["Wash Sale"] == ""


def test_tax_total_row_empty():
    assert rd.tax_total_row([]) == {"proceeds": 0.0, "cost": 0.0, "gain": 0.0,
                                    "n_unknown_gain": 0, "n_wash": 0}
    assert rd.tax_total_row(None)["n_wash"] == 0
