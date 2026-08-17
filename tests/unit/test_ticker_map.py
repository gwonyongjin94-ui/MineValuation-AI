import json

import httpx
import pytest

from app.data.exceptions import UnknownTickerError
from app.data.ticker_map import TickerMap

RAW_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
}


def test_resolve_fetches_and_caches(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=RAW_TICKERS)

    cache_path = tmp_path / "company_tickers.json"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    ticker_map = TickerMap(user_agent="test", cache_path=cache_path, client=client)

    assert ticker_map.resolve("AAPL") == "0000320193"
    assert ticker_map.resolve("msft") == "0000789019"
    assert len(calls) == 1
    assert cache_path.exists()


def test_resolve_uses_cache_without_refetch(tmp_path):
    cache_path = tmp_path / "company_tickers.json"
    cache_path.write_text(json.dumps(RAW_TICKERS))

    def handler(request):
        raise AssertionError("should not hit network when cache exists")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    ticker_map = TickerMap(user_agent="test", cache_path=cache_path, client=client)

    assert ticker_map.resolve("AAPL") == "0000320193"


def test_unknown_ticker_raises(tmp_path):
    cache_path = tmp_path / "company_tickers.json"
    cache_path.write_text(json.dumps(RAW_TICKERS))
    ticker_map = TickerMap(user_agent="test", cache_path=cache_path)

    with pytest.raises(UnknownTickerError):
        ticker_map.resolve("ZZZZ")
