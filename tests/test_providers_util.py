"""Regression tests for stock_analyzer/providers/_util.py — 2026-08-04 audit
finding: http_get_json only redacted API keys from the raise_for_status()
branch. A bare requests.get() failure (Timeout/ConnectionError/etc, before
raise_for_status() is ever reached) embeds the full request URL — including
the plaintext API key query param — in its own exception message, and that
was leaking into api_health on a routine timeout/DNS hiccup.
"""
from unittest.mock import patch

import pytest
import requests

from stock_analyzer.providers._util import http_get_json, _redact_url


def test_redact_url_strips_apikey_param():
    url = "https://finnhub.io/api/v1/quote?symbol=AAPL&token=SECRET123"
    assert "SECRET123" not in _redact_url(url)
    assert "token=***" in _redact_url(url)


def test_connection_error_redacts_api_key_from_message():
    leaky_url = "https://finnhub.io/api/v1/quote?symbol=AAPL&token=SECRET123"
    with patch("requests.get", side_effect=requests.ConnectionError(f"Failed: {leaky_url}")):
        with pytest.raises(requests.ConnectionError) as exc_info:
            http_get_json(leaky_url)
    assert "SECRET123" not in str(exc_info.value)


def test_timeout_error_redacts_api_key_from_message():
    leaky_url = "https://finnhub.io/api/v1/quote?symbol=AAPL&apikey=SECRET456"
    with patch("requests.get", side_effect=requests.Timeout(f"Timed out: {leaky_url}")):
        with pytest.raises(requests.Timeout) as exc_info:
            http_get_json(leaky_url)
    assert "SECRET456" not in str(exc_info.value)


def test_http_error_still_redacted_existing_behavior():
    """Not a regression — the pre-existing raise_for_status() redaction path
    must keep working unchanged."""
    import unittest.mock as mock
    leaky_url = "https://finnhub.io/api/v1/quote?symbol=AAPL&token=SECRET789"
    fake_resp = mock.Mock()
    fake_resp.raise_for_status.side_effect = requests.HTTPError(f"401 Client Error: {leaky_url}")
    with patch("requests.get", return_value=fake_resp):
        with pytest.raises(requests.HTTPError) as exc_info:
            http_get_json(leaky_url)
    assert "SECRET789" not in str(exc_info.value)
