"""P2+P3 wiring pins for docs/plans/grow-today-owns-buys.md: app.py must not
reference the retired buy-candidates lane. Lives apart from
tests/test_grow_today_owns_buys.py because app.py at HEAD still contains the
old lane until the combined P2+P3 commit lands."""
from pathlib import Path

import pytest

from tests.test_grow_today_owns_buys import _PKG, _retired_hits

pytestmark = pytest.mark.fast


def test_retired_lane_is_gone_from_app_py():
    app = _PKG.parent / "app.py"
    assert _retired_hits(app) == []


def test_retired_lane_ast_guard_fires_on_a_sample(tmp_path):
    """Firing control: the guard used above must actually flag each retired
    shape, or the app.py assertion would pass vacuously."""
    f = tmp_path / "sample.py"
    _dash = chr(0x2014)
    f.write_text(
        chr(10).join([
            "x = brief['buy_candidates']",
            "y = _buy_candidates(1)",
            "z = pair_add_gate.buy_lane_only(a, b)",
            f"w = 'BUY {_dash} Scanner Pick'",
        ]), encoding="utf-8")
    kinds = {h[1] for h in _retired_hits(f)}
    assert kinds == {"buy_candidates", "_buy_candidates", "buy_lane_only",
                     f"BUY {_dash} Scanner Pick"}
