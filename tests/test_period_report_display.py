"""Part 3 (2026-10-20 live screenshot): no "-$0" for a rounding residue, and the
period-report tables are sized so no row scrolls out of view."""
import ast
from pathlib import Path

from stock_analyzer import period_attribution as pa
from tests._ast_cache import parse_file_cached, source_segment

bs = chr(92)
_SRC = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")


def test_usd_plain_never_prints_signed_zero():
    # below half a cent (the strip's ok/warn tolerance): a plain "$0", never "-$0"
    for x in (-0.004, 0.004, 0.0, -0.0):
        assert pa.usd_plain(x) == "$0", x
        assert pa.usd_plain(x, plus=True) == "$0", x
    assert pa.usd_plain(-0.6) == "-$1"      # firing control: a real figure keeps its sign
    assert pa.usd_plain(2.4, plus=True) == "+$2"


def test_usd_plain_shows_cents_for_a_sub_dollar_figure():
    # same rule as _usd: the Unexplained chip is amber from half a cent, so the
    # table / waterfall / email cell beside it must not read "$0"
    assert pa.usd_plain(0.38) == "$0.38"
    assert pa.usd_plain(-0.38) == "-$0.38"
    assert pa.usd_plain(0.38, plus=True) == "+$0.38"
    assert pa.usd_plain(-0.38, plus=True) == "-$0.38"
    assert pa.usd_plain(-0.3) == "-$0.30"
    assert pa.usd_plain(0.49) == "$0.49"
    assert pa.usd_plain(0.5) == "$0.50"     # round(0.5) == 0 in Python: still cents
    assert pa.usd_plain(0.51) == "$1"       # rounds to a whole dollar: unchanged from before
    assert pa.usd_plain(1.4) == "$1"        # a dollar or more is unchanged
    assert pa.usd_plain(-1.4) == "-$1"
    assert "\\" not in pa.usd_plain(-0.38)  # not markdown: no backslash escape
    # edge: exactly the tolerance is shown, just below is not
    assert pa.usd_plain(pa._CENT_TOL) == "$0.01"
    assert pa.usd_plain(pa._CENT_TOL * 0.99) == "$0"


def test_markdown_usd_never_prints_signed_zero():
    # below half a cent (the strip's ok/warn tolerance) stays a plain "$0"
    for x in (0.004, -0.004, 0.0, -0.0):
        assert pa._usd(x) == bs + "$0", x
    assert pa._usd(-1500) == "-" + bs + "$1,500"


def test_markdown_usd_shows_cents_for_a_flagged_sub_dollar_figure():
    # the Unexplained chip turns amber at >= _CENT_TOL, so the figure beside it
    # must not read "$0" (live 2026-11-10: amber chip beside "$0")
    assert pa._usd(0.38) == bs + "$0.38"
    assert pa._usd(-0.38) == "-" + bs + "$0.38"
    assert pa._usd(-0.3) == "-" + bs + "$0.30"
    assert pa._usd(0.5) == bs + "$0.50"        # round(0.5) == 0 in Python: still cents
    assert pa._usd(1.4) == bs + "$1"           # a dollar or more keeps whole dollars
    assert pa._usd(-1.4) == "-" + bs + "$1"


def test_unexplained_chip_amount_matches_its_warn_state():
    # firing control: a sub-dollar residue above tolerance -> warn chip AND cents
    recon = {"status": "ok", "coverage": {"trading_days": 3, "account_days_valid": 3,
                                           "snapshot_days": 3},
             "unpriced": [], "residual": {"total": 0.38, "pct_of_start": None}}
    chips = {c["key"]: c for c in pa.trust_strip(recon, None)}
    assert chips["unexplained"]["state"] == "warn"
    assert chips["unexplained"]["label"] == "Unexplained " + bs + "$0.38"
    recon["residual"]["total"] = 0.004
    chips = {c["key"]: c for c in pa.trust_strip(recon, None)}
    assert chips["unexplained"]["state"] == "ok"
    assert chips["unexplained"]["label"] == "Unexplained " + bs + "$0"


def test_dollar_helpers_read_non_finite_as_unknown():
    # the Sunday email's money block formats with usd_plain: one NaN/inf must
    # read "unknown", not raise (round(nan) is ValueError, round(inf) OverflowError)
    for x in (float("nan"), float("inf"), float("-inf")):
        assert pa.usd_plain(x) == "unknown", x
        assert pa.usd_plain(x, plus=True) == "unknown", x
        assert pa._usd(x) == "unknown", x
    assert pa.usd_plain(-1313.4) == "-$1,313"   # firing control: finite values still format
    assert pa._usd(1313.4) == bs + "$1,313"


def test_period_report_tables_are_sized_to_their_rows():
    tree = parse_file_cached(Path(__file__).resolve().parent.parent / "app.py")
    hits = 0
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "dataframe" and n.args):
            seg = source_segment(_SRC, n) or ""
            if "Where the change came from" in seg or "Book gap at end" in seg:
                assert any(k.arg == "height" for k in n.keywords), seg[:80]
                hits += 1
    assert hits == 2


def test_book_side_extra_names_left_out_positions():
    # by design R_book also carries the move on an unpriced / journal-flagged
    # position, so the row must say so (2026-10-20: -$113 beside two $0 gaps)
    assert pa.book_side_extra({"unpriced": [], "journal_inconsistent": []}) == ""
    assert pa.book_side_extra({}) == ""
    assert pa.book_side_extra(None) == ""
    one = pa.book_side_extra({"unpriced": [("XYZ", "2026-10-14"), ("XYZ", "2026-10-15")]})
    assert "1 position left out above" in one       # one name over two days = 1
    two = pa.book_side_extra({"unpriced": [("XYZ", "d")], "journal_inconsistent": [("ABC", "d")]})
    assert "2 positions left out above" in two


def test_book_side_row_uses_the_extra():
    assert '_pa_mod.book_side_extra(_pr_recon)' in _SRC
    assert '"Book side (recorded book vs trade-journal replay"' in _SRC
    assert '+ _pr_book_side_extra + ")"' in _SRC
