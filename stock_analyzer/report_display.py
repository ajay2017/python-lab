"""Pure display helpers for the 📄 Reports page (Reports polish, 2026-11-14).

Display only: nothing here decides a number, a gate or a recommendation.  The
page (app.py) calls these and hands the strings to Streamlit, so the formatting
rules are unit-testable (no test imports app.py).

* `report_markdown`      AI section text -> markdown with real bullet lists and
                         `$` escaped (two bare `$` render as LaTeX).
* `money_table_markdown` / `waterfall_markdown`
                         a compact two-column money table with sign colours
                         (Streamlit `:green[...]` / `:red[...]` / `:gray[...]`
                         directives); privacy mask greys every colour so red /
                         green cannot reveal a gain or a loss.
* `tax_money_text` / `gain_css` / `tax_total_row`
                         Tax Report table cells, the gain colour and the total
                         row (privacy-aware).
"""
from __future__ import annotations

import math
import numbers

from stock_analyzer.debrief_advisor import escape_md_dollars
from stock_analyzer.period_attribution import usd_plain

MASK_TEXT = "••••••"
_NBSP = " "
_UNKNOWN = "unknown"


def _is_blank(x) -> bool:
    return x is None or (isinstance(x, float) and x != x)


# ─────────────────────────── AI section text ────────────────────────────────
def _bullet_body(stripped: str) -> "str | None":
    """The text after a bullet marker ("•", "- " or "* "), or None when the
    line isn't a bullet (or is an empty one).  "-5%" has no space: not a bullet."""
    if stripped.startswith("•"):
        body = stripped[1:].strip()
    elif stripped.startswith("- ") or stripped.startswith("* "):
        body = stripped[2:].strip()
    else:
        return None
    return body or None


def report_markdown(text) -> str:
    """AI section text -> markdown: `$` escaped, every "•" / "- " / "* " line a
    real list item.  Streamlit's markdown joins single-newline lines into one
    paragraph, so the model's one-bullet-per-line text needs a blank line before
    the list and list syntax to render as a list.  A blank line is also put after
    a list block when prose follows directly (else markdown folds that prose into
    the last item).  Prose lines are left as they are.  None / NaN -> ""."""
    if _is_blank(text):
        return ""
    out: "list[str]" = []
    prev_item = False
    for line in escape_md_dollars(text).split("\n"):
        body = _bullet_body(line.strip())
        if body is not None:
            if out and out[-1].strip() and not prev_item:
                out.append("")
            out.append("- " + body)
            prev_item = True
        else:
            if prev_item and line.strip():
                out.append("")
            out.append(line)
            prev_item = False
    return "\n".join(out)


# ─────────────────────────── chart tick labels ──────────────────────────────
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def week_tick_label(week_ending) -> str:
    """"wk to Nov 8" from an ISO date string (no leading zero on the day).  An
    unparseable value keeps its raw string, so a tick never disappears."""
    raw = "" if week_ending is None else str(week_ending)
    try:
        y, m, d = (int(p) for p in raw[:10].split("-"))
        from datetime import date as _date
        _date(y, m, d)                      # validates (rejects 2026-13-45)
        return f"wk to {_MONTHS[m - 1]} {d}"
    except (ValueError, IndexError):
        return raw


# ─────────────────────────── monthly review tiles ───────────────────────────
def avg_delta_text(avg) -> "str | None":
    """st.metric delta text, e.g. "+2.1 pp avg vs SPY".  It must START with the
    sign character: Streamlit colours a delta red only on a leading "-".  None
    (no average / NaN / not a number) -> None, so no delta is drawn."""
    if not _finite(avg):
        return None
    return f"{float(avg):+.1f} pp avg vs SPY"


def skipped_avg_state(viz, frozen) -> "tuple[float | None, bool]":
    """(average, saved) for the "Names you skipped" tile.  The skipped average is
    frozen into a report's viz snapshot only from the report that first saved it;
    an older report (no key) or the live-fallback path reads saved=False and is
    never recomputed.  A saved None means "no matured skipped names": saved=True,
    average None."""
    if not (frozen and isinstance(viz, dict) and "avg_skipped_names_alpha" in viz):
        return None, False
    v = viz.get("avg_skipped_names_alpha")
    return (float(v) if _finite(v) else None), True


def monthly_tiles_caption_suffix(bought_avg, skipped_avg, skipped_saved) -> str:
    """Sentence(s) appended to the monthly tiles' caption: what each average
    covers, plus why a tile shows no average (skipped figure not saved with an
    older report; no matured bought / skipped names yet)."""
    out = (" Bought average: vs SPY in percentage points over the matured surfacings "
           "you acted on. Skipped average: over every matured surfacing of the names "
           "you never bought.")
    if not skipped_saved:
        out += " Skipped names' average vs SPY wasn't saved with this month's report."
    if not _finite(bought_avg):
        out += " No matured bought names to average yet."
    if skipped_saved and not _finite(skipped_avg):
        out += " No matured skipped names to average yet."
    return out


# ─────────────────────────── money table ────────────────────────────────────
def _esc_cell(s) -> str:
    return str(s).replace("|", "\\|").replace("$", "\\$")


def _amount_md(value, plus: bool, mask: bool) -> str:
    """One amount as a coloured markdown directive (never raw `$`)."""
    if _is_blank(value):
        return f":gray[{_UNKNOWN}]"
    txt = usd_plain(value, plus=plus)
    if txt == _UNKNOWN:                    # inf / non-finite: unknown, never a crash
        return f":gray[{_UNKNOWN}]"
    if mask:
        return f":gray[{MASK_TEXT}]"
    esc = txt.replace("$", "\\$")
    if txt == "$0":                        # sub-cent residue reads "$0": no colour
        return f":gray[{esc}]"
    return f":green[{esc}]" if value > 0 else f":red[{esc}]"


def money_table_markdown(header: str, rows, *, mask: bool) -> str:
    """Two-column markdown pipe table `| header | $ |`, amounts right-aligned.

    rows: dicts {label, value (float|None), plus, bold, indent, note}.  Green =
    adds, red = takes away, grey = zero / unknown.  `mask=True` shows •••••• for
    every real amount, all grey (a colour would still reveal gain vs loss).  Every
    `$` and `|` in a label is escaped; bold wraps label and amount; indent puts four
    no-break spaces and "↳ " before the label (outside the bold: `**` followed by a
    space-like character does not open emphasis)."""
    lines = [f"| {_esc_cell(header)} | $ |", "|---|--:|"]
    for r in rows or []:
        label = _esc_cell(r.get("label", ""))
        amount = _amount_md(r.get("value"), bool(r.get("plus")), mask)
        if r.get("indent"):
            label = "↳ " + label
        if r.get("bold"):
            label = f"**{label}**"
            amount = f"**{amount}**"
        if r.get("indent"):
            label = _NBSP * 4 + label
        note = r.get("note")
        if note:
            label += f" :gray[({_esc_cell(note)})]"
        lines.append(f"| {label} | {amount} |")
    return "\n".join(lines)


def waterfall_markdown(wf_rows, *, mask: bool) -> str:
    """`period_attribution.waterfall_rows` output as a money table."""
    return money_table_markdown(
        "Where the change came from",
        [{
            "label": w["label"], "value": w["value"],
            "plus": not w["total"],
            "bold": bool(w["total"] or w["breakdown"]),
            "indent": bool(w["indent"]), "note": w.get("note") or "",
        } for w in (wf_rows or [])],
        mask=mask,
    )


# ─────────────────────────── Tax Report cells ───────────────────────────────
def tax_money_text(v, *, mask: bool) -> str:
    """"$1,520.00" / "-$583.88"; None -> "—"; mask -> •••••• for a real value."""
    if not _finite(v):
        return "—"
    if mask:
        return MASK_TEXT
    v = round(float(v), 2)
    if v == 0:
        return "$0.00"                      # never "-$0.00"
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def gain_css(v, *, mask: bool) -> str:
    """Cell colour for a gain; none for zero / unknown / privacy mode (a colour
    would reveal gain vs loss)."""
    if mask or not _finite(v):
        return ""
    v = round(float(v), 2)
    if v > 0:
        return "color: #4ade80"
    if v < 0:
        return "color: #f87171"
    return ""


def _finite(x) -> bool:
    return (isinstance(x, numbers.Real) and not isinstance(x, bool)
            and math.isfinite(x))


def tax_total_row(rows) -> dict:
    """Totals over the ledger rows: proceeds / cost / gain summed over the lots
    whose gain is known, so all three totals cover the same lots and
    Proceeds - Cost = Gain on the Total line; how many gains are unknown (those
    lots are left out of every total, never counted as 0) and how many lots are
    wash sales (status "violation")."""
    rows = list(rows or [])
    known = [r for r in rows if _finite(r.get("gain"))]

    def _sum(key: str) -> float:
        return sum(float(r.get(key)) for r in known if _finite(r.get(key)))

    n_wash = 0
    for r in rows:
        ws = r.get("wash_sale_status")
        if isinstance(ws, dict) and ws.get("status") == "violation":
            n_wash += 1
    return {
        "proceeds": _sum("proceeds"),
        "cost": _sum("cost"),
        "gain": _sum("gain"),
        "n_unknown_gain": sum(1 for r in rows if not _finite(r.get("gain"))),
        "n_wash": n_wash,
    }


def tax_total_table_row(rows) -> dict:
    """The Tax Report table's final "Total" row, keyed by the table's columns.
    When some gains are unknown the Ticker cell says how many lots the total
    leaves out; the Wash Sale cell counts wash sales ("" when none)."""
    t = tax_total_row(rows)
    n_unk, n_wash = t["n_unknown_gain"], t["n_wash"]
    return {
        "Ticker": (f"Total (excl. {n_unk} lot{'s' if n_unk != 1 else ''} with unknown cost)"
                   if n_unk > 0 else "Total"),
        "Buy Date": "", "Sell Date": "", "Shares": None,
        "Proceeds": t["proceeds"], "Cost": t["cost"], "Gain": t["gain"],
        "Days": None, "Term": "",
        "Wash Sale": f"{n_wash} wash sale(s)" if n_wash > 0 else "",
    }
