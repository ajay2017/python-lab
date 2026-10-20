"""G-31 (S-D, add room under the whole-position caps) carries a readout footnote."""
from stock_analyzer.gate_ledger_readout import readout_footnotes


def test_g31_footnote_present_and_mentions_leverage():
    notes = readout_footnotes("G-31")
    assert len(notes) == 1
    txt = notes[0]
    assert "2026-10-19" in txt and "leverage" in txt and "9.6%" in txt


def test_unrelated_gate_has_no_g31_text():
    assert all("S-D" not in n for n in readout_footnotes("G-23"))
