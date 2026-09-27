"""
Asset-type vocabulary — the ONE definition every consumer (`data.py`,
`bundle_loader.py`, `broker_sync.py`, `db.py`) reads, instead of each
inventing its own (the "overloaded producer state" trap this project has hit
before — see memory `feedback_overloaded_producer_state`).

Phase 1 of the ETF-support initiative (`docs/plans/etf-multi-asset-support.md`
§11). These are enum LABELS, not decision thresholds — deliberately NOT in
`constants.py`.

Vocabulary is exactly `{"stock", "etf"}` for Phase 1 (owner-confirmed
2026-09-27): SnapTrade's `adr` instrument kind maps to `"stock"`, yfinance's
`MUTUALFUND` quote type maps to `"etf"` (mutual funds reuse the ETF strategy
almost verbatim per the plan doc's §8 "Future extensibility" — expense
ratio/NAV/category, minus intraday premium/discount), and everything else
unknown maps to `"stock"` — the fail-safe default. An unrecognized input is
NEVER classified as `"etf"`: that direction of error would route a genuinely
unmeasurable instrument through the equity composite/fundamentals gate
(fabricating a score), while the reverse error (a real ETF scored as a stock)
is the exact bug Phase 0 already fixed via the `bq_available`/`val_available`
flags — a stock wrongly defaulted to "stock" still gets scored correctly
because it IS a stock.
"""
from __future__ import annotations

ASSET_TYPE_STOCK = "stock"
ASSET_TYPE_ETF = "etf"


def from_quote_type(quote_type: "str | None") -> str:
    """yfinance `.info["quoteType"]` -> `"stock"` | `"etf"`.

    `"ETF"` or `"MUTUALFUND"` (case-insensitive) -> `"etf"`. Anything else,
    including `None`, empty string, or an unrecognized quote type (e.g.
    `"EQUITY"`, `"CRYPTOCURRENCY"`, `"CURRENCY"`, `"INDEX"`) -> `"stock"`,
    the fail-safe default.
    """
    if not quote_type:
        return ASSET_TYPE_STOCK
    qt = str(quote_type).strip().upper()
    if qt in ("ETF", "MUTUALFUND"):
        return ASSET_TYPE_ETF
    return ASSET_TYPE_STOCK


def from_broker_kind(kind: "str | None") -> str:
    """SnapTrade `instrument.kind` -> `"stock"` | `"etf"`.

    `"etf"` (case-insensitive) -> `"etf"`. `"stock"`, `"adr"`, `None`, or
    anything else -> `"stock"`, the fail-safe default. `adr` -> `"stock"` is
    an explicit owner-confirmed mapping (an ADR is a single company's shares,
    not a fund — it belongs in the equity strategy, not the ETF one).
    """
    if not kind:
        return ASSET_TYPE_STOCK
    k = str(kind).strip().lower()
    if k == "etf":
        return ASSET_TYPE_ETF
    return ASSET_TYPE_STOCK


def normalize(raw: "str | None") -> str:
    """Coerce any persisted/legacy value to the vocabulary.

    This is the NULL-backfill helper DB loaders call: a legacy row predating
    this column (or a row whose DDL simply hasn't been applied yet) reads
    `None`/missing, and must backfill to `"stock"` — never `"etf"`, since
    that would silently promote an unclassified holding into the (currently
    withhold-only, Phase 0) ETF path. Anything not EXACTLY `"etf"` (after
    stripping whitespace and normalizing case) -> `"stock"`.
    """
    if raw is None:
        return ASSET_TYPE_STOCK
    val = str(raw).strip().lower()
    if val == ASSET_TYPE_ETF:
        return ASSET_TYPE_ETF
    return ASSET_TYPE_STOCK
