"""C3 of docs/plans/macro-sector-gate.md: resolve_sector translates a provider
label to the curated vocabulary at the source, sector_gate_context reports a
"translated" label_source, and the Watchlist soft note words it."""
import pandas as pd
import pytest

from stock_analyzer.constants import UNCLASSIFIED_SECTOR
from stock_analyzer.portfolio import TICKER_SECTORS, resolve_sector
from stock_analyzer.sector_fit import sector_gate_context
from stock_analyzer.sector_vocab import CURATED_SECTORS, to_curated
from stock_analyzer.watchlist_advisor import _portfolio_risk_gate
from tests.conftest import make_port_df

UNCURATED = "ZZZUNCUR"   # a ticker guaranteed absent from TICKER_SECTORS


def test_uncurated_test_ticker_really_is_uncurated():
    assert UNCURATED not in TICKER_SECTORS


# ── resolve_sector ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("Financial Services", "Financials"),
    ("  financial services ", "Financials"),
    ("Healthcare & Biotech", "Healthcare"),
    ("Basic Materials", "Materials"),
    ("Consumer Defensive", "Consumer Staples & Retail"),
    ("Technology", "Technology"),                       # unmapped: kept raw
    ("Communication Services", "Communication Services"),  # unmapped on purpose
    ("  Technology ", "Technology"),                    # stripped raw
])
def test_resolve_sector_uncurated_ticker(raw, expected):
    assert resolve_sector(UNCURATED, raw) == expected


def test_curated_ticker_wins_over_any_fallback():
    assert resolve_sector("JPM", "Technology") == TICKER_SECTORS["JPM"]
    assert resolve_sector("jpm", "Healthcare & Biotech") == TICKER_SECTORS["JPM"]


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_resolve_sector_blank_is_other(blank):
    assert resolve_sector(UNCURATED, blank) == UNCLASSIFIED_SECTOR


def test_resolve_sector_never_returns_literal_none():
    assert resolve_sector(UNCURATED, None) != "None"


# ── sector_gate_context ──────────────────────────────────────────────────────

def test_context_curated():
    ctx = sector_gate_context("JPM", "Technology", None)
    assert ctx["label_source"] == "curated"
    assert ctx["sector"] == TICKER_SECTORS["JPM"]
    assert ctx["provider_label"] == "Technology"


def test_context_translated():
    ctx = sector_gate_context(UNCURATED, " Financial Services ", None)
    assert ctx["label_source"] == "translated"
    assert ctx["sector"] == "Financials"
    assert ctx["classified"] is True
    assert ctx["provider_label"] == "Financial Services"


def test_context_provider_when_untranslatable():
    ctx = sector_gate_context(UNCURATED, "Technology", None)
    assert ctx["label_source"] == "provider"
    assert ctx["sector"] == "Technology"
    assert ctx["provider_label"] == "Technology"


@pytest.mark.parametrize("blank", [None, "", "Watchlist", "nan", "Unknown"])
def test_context_none(blank):
    ctx = sector_gate_context(UNCURATED, blank, None)
    assert ctx["label_source"] == "none"
    assert ctx["classified"] is False
    assert ctx["sector"] == UNCLASSIFIED_SECTOR
    assert ctx["provider_label"] is None
    assert ctx["weight_pct"] == 0.0


def test_translated_candidate_weight_sums_curated_and_translated_held_rows():
    held = [("JPM", None), ("ZZZHELD", "Financial Services"), ("AAPL", None)]
    rows = [{"ticker": t, "weight": w,
             "sector": resolve_sector(t, raw)}
            for (t, raw), w in zip(held, (10.0, 7.0, 20.0))]
    port_df = make_port_df(rows)
    assert list(port_df["Sector"]) == ["Financials", "Financials",
                                       TICKER_SECTORS["AAPL"]]
    ctx = sector_gate_context(UNCURATED, "Financial Services", port_df)
    assert ctx["label_source"] == "translated"
    assert ctx["weight_pct"] == pytest.approx(17.0)


def test_held_and_candidate_with_same_label_share_a_bucket():
    assert (resolve_sector("ZZZHELD", "Financial Services")
            == sector_gate_context(UNCURATED, "Financial Services", None)["sector"])


# ── scanner bucket invariant ─────────────────────────────────────────────────

# Bucket labels that deliberately do NOT translate (sector_vocab, owner ruling
# U1): they straddle two curated sectors, so an entry on an uncurated name with
# one of these labels is HELD by the macro gate (state "unknown"), never passed.
_DELIBERATELY_UNMAPPED_BUCKETS = {"AI & Data Platforms", "EV & Clean Energy"}


def test_every_scanner_bucket_label_translates_or_is_allowlisted():
    from tests.test_scanner import _SECTOR_UNIVERSE_SNAPSHOT
    labels = set(_SECTOR_UNIVERSE_SNAPSHOT)
    assert _DELIBERATELY_UNMAPPED_BUCKETS <= labels
    untranslatable = {b for b in labels if to_curated(b) is None}
    assert untranslatable == _DELIBERATELY_UNMAPPED_BUCKETS, (
        "a scanner bucket label neither translates via sector_vocab.to_curated "
        f"nor is on the allowlist: {sorted(untranslatable ^ _DELIBERATELY_UNMAPPED_BUCKETS)}")
    for b in labels - _DELIBERATELY_UNMAPPED_BUCKETS:
        assert to_curated(b) in CURATED_SECTORS


# ── watchlist soft note ──────────────────────────────────────────────────────

def _ctx(src, sector, raw=None):
    return {"sector_weight_pct": 5.0, "sector_of_ticker": sector,
            "sector_label_source": src, "sector_provider_label": raw}


def test_translated_note_names_both_labels():
    gate = _portfolio_risk_gate(1.0, _ctx("translated", "Financials", "Financial Services"))
    assert gate["severity"] == "soft"
    assert ("Sector taken from the provider label (Financial Services → Financials); "
            "add it to the curated sector map to confirm.") in gate["reason"]
    assert "undercount" not in gate["reason"]


def test_translated_note_with_identical_label_does_not_print_an_arrow():
    gate = _portfolio_risk_gate(1.0, _ctx("translated", "Healthcare", "healthcare"))
    assert "(Healthcare)" in gate["reason"] and "→" not in gate["reason"]


def test_provider_keeps_the_d2_undercount_note():
    gate = _portfolio_risk_gate(1.0, _ctx("provider", "Technology", "Technology"))
    assert "may undercount" in gate["reason"]
    assert "→" not in gate["reason"]


def test_curated_and_none_carry_no_note():
    assert _portfolio_risk_gate(1.0, _ctx("curated", "Semiconductors")) is None
