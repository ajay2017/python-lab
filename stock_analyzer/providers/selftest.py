"""
Offline provider smoke-test — validates the keyed adapters against LIVE APIs
without running the full Streamlit app.

The keys live in Streamlit secrets when deployed; for this offline test set them
as environment variables first (so they never touch code or chat):

    # PowerShell
    $env:FINNHUB_API_KEY = "..."; $env:FMP_API_KEY = "..."
    python -m stock_analyzer.providers.selftest AAPL MSFT

    # bash
    FINNHUB_API_KEY=... FMP_API_KEY=... python -m stock_analyzer.providers.selftest AAPL MSFT

It prints each provider's live_prices (and FMP history shape) so you can eyeball
that the canonical schema is populated before the orchestrator is wired in.
Uses a few API calls only (well within free-tier daily limits).
"""

import sys

from stock_analyzer.providers.yfinance_provider import YFinanceProvider
from stock_analyzer.providers.finnhub_provider import FinnhubProvider
from stock_analyzer.providers.fmp_provider import FMPProvider


def main(argv: list[str]) -> int:
    tickers = [t.upper() for t in argv[1:]] or ["AAPL", "MSFT"]
    print(f"Tickers: {tickers}\n")

    for Prov in (YFinanceProvider, FinnhubProvider, FMPProvider):
        p = Prov()
        print(f"=== {p.name}  (configured={p.is_configured()}, caps={sorted(p.capabilities)})")
        if not p.is_configured():
            print("   skipped — key not set\n")
            continue
        try:
            prices = p.live_prices(tickers)
            for t in tickers:
                print(f"   {t}: {prices.get(t, '— (none)')}")
        except Exception as exc:
            print(f"   live_prices ERROR: {exc}")
        # FMP also serves history — show the frame shape.
        if hasattr(p, "price_history") and "history" in p.capabilities:
            try:
                df = p.price_history(tickers[0], "3mo")
                print(f"   history({tickers[0]}): {len(df)} rows, "
                      f"cols={list(df.columns)}, last_close={float(df['Close'].iloc[-1]):.2f}")
            except Exception as exc:
                print(f"   price_history ERROR: {exc}")
        print()

    # ── Orchestrator end-to-end (live-price-primary + cross-check) ───────────
    # Exercises the REAL failover/cross-check paths regardless of the master
    # switch, so we can validate them before flipping DATA_MULTISOURCE_ENABLED.
    from stock_analyzer.providers import orchestrator as orch
    from stock_analyzer import constants as C
    orch.reset()
    print(f"=== orchestrator   (live-price order: {C.DATA_LIVE_PRICE_ORDER})")
    lp = orch.get_live_prices(tickers)
    for t in tickers:
        print(f"   {t}: {lp.get(t, '— (none)')}")
    prim = lp.get(tickers[0], {})
    xc = orch.crosscheck_price(tickers[0], prim.get("price"), prim.get("prev_close"))
    print(f"   cross-check {tickers[0]}: {xc}")
    print(f"   (prev_close tol={C.DATA_XCHECK_PREVCLOSE_TOL_PCT}% strict, "
          f"live tol={C.DATA_XCHECK_LIVE_TOL_PCT}% loose)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
