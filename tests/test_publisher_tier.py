"""D-7: publisher tier matches on word boundaries, not substrings."""
import pytest

from stock_analyzer.data import curate_news_items, publisher_tier

pytestmark = pytest.mark.fast


@pytest.mark.parametrize("pub", [
    "Reuters", "AP", "AP News", "Associated Press", "The Wall Street Journal",
    "WSJ", "Bloomberg", "CNBC", "Barron's", "Barrons.com", "Financial Times",
])
def test_tier1(pub):
    assert publisher_tier(pub) == 1


@pytest.mark.parametrize("pub", [
    "MarketWatch", "Yahoo Finance", "Seeking Alpha", "Zacks", "Benzinga",
    "Motley Fool", "The Motley Fool", "Forbes", "Business Insider",
    "Investing.com", "TheStreet", "Nasdaq",
])
def test_tier2(pub):
    assert publisher_tier(pub) == 2


@pytest.mark.parametrize("pub", [
    "AppleInsider", "TheWrap", "MT Newswires", "Capital Market Laboratories",
    "Snapchat Daily", "", None,
])
def test_tier3(pub):
    assert publisher_tier(pub) == 3


def test_curate_assigns_tier_via_publisher_tier():
    raw = [{"title": "Apple ships a thing", "publisher": "AppleInsider",
            "link": "http://x", "providerPublishTime": 1_700_000_000}]
    out = curate_news_items({"AAPL": {"news_raw": raw}}, max_items=None)
    assert out[0]["tier"] == 3
