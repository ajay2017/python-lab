"""Part 3 (2026-10-20 live screenshot): no "-$0" for a rounding residue, and the
period-report tables are sized so no row scrolls out of view."""
import ast
from pathlib import Path

from stock_analyzer import period_attribution as pa

bs = chr(92)
_SRC = (Path(__file__).resolve().parent.parent / "app.py").read_text(encoding="utf-8")


def test_usd_plain_never_prints_signed_zero():
    for x in (-0.004, -0.3, -0.49, 0.3, 0.0):
        assert pa.usd_plain(x) == "$0", x
        assert pa.usd_plain(x, plus=True) == "$0", x
    assert pa.usd_plain(-0.6) == "-$1"      # firing control: a real figure keeps its sign
    assert pa.usd_plain(2.4, plus=True) == "+$2"


def test_markdown_usd_never_prints_signed_zero():
    assert pa._usd(-0.3) == bs + "$0"
    assert pa._usd(-1500) == "-" + bs + "$1,500"


def test_period_report_tables_are_sized_to_their_rows():
    tree = ast.parse(_SRC)
    hits = 0
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "dataframe" and n.args):
            seg = ast.get_source_segment(_SRC, n) or ""
            if "Where the change came from" in seg or "Book gap at end" in seg:
                assert any(k.arg == "height" for k in n.keywords), seg[:80]
                hits += 1
    assert hits == 2
