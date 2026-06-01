"""Market-data provider package. See base.py for the abstraction and the
converged multi-source design (memory project_second_data_source)."""

from stock_analyzer.providers.base import (
    DataProvider, ProviderUnavailable,
    CAP_LIVE_PRICE, CAP_HISTORY, CAP_BUNDLE, CAP_INDICES, CAP_RISK_FREE,
)
from stock_analyzer.providers.yfinance_provider import YFinanceProvider
from stock_analyzer.providers.finnhub_provider import FinnhubProvider
from stock_analyzer.providers.fmp_provider import FMPProvider

#: Registry mapping DataProvider.name → class, for the orchestrator to build the
#: failover chain from constants.DATA_PROVIDER_ORDER.
PROVIDER_REGISTRY = {
    YFinanceProvider.name: YFinanceProvider,
    FinnhubProvider.name:  FinnhubProvider,
    FMPProvider.name:      FMPProvider,
}

__all__ = [
    "DataProvider", "ProviderUnavailable",
    "CAP_LIVE_PRICE", "CAP_HISTORY", "CAP_BUNDLE", "CAP_INDICES", "CAP_RISK_FREE",
    "YFinanceProvider", "FinnhubProvider", "FMPProvider", "PROVIDER_REGISTRY",
]
