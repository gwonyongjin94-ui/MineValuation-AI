import httpx
import pytest

from app.data.market_data import MarketDataError, fetch_current_price, fetch_risk_free_rate


def _client(response_text: str, status_code: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=response_text)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _json_client(json_body: dict, status_code: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=json_body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_risk_free_rate_parses_latest_observation():
    csv = "observation_date,DGS10\n2026-08-17,4.72\n2026-08-18,4.71\n"

    rate = fetch_risk_free_rate(_client(csv))

    assert rate == pytest.approx(0.0471)


def test_fetch_risk_free_rate_skips_holiday_dot_rows():
    csv = "observation_date,DGS10\n2026-08-15,4.70\n2026-08-16,.\n2026-08-17,.\n"

    rate = fetch_risk_free_rate(_client(csv))

    assert rate == pytest.approx(0.0470)


def test_fetch_risk_free_rate_raises_on_http_error():
    with pytest.raises(MarketDataError):
        fetch_risk_free_rate(_client("", status_code=503))


def test_fetch_risk_free_rate_raises_on_empty_response():
    with pytest.raises(MarketDataError):
        fetch_risk_free_rate(_client("observation_date,DGS10\n"))


def test_fetch_risk_free_rate_raises_when_all_rows_are_dots():
    with pytest.raises(MarketDataError):
        fetch_risk_free_rate(_client("observation_date,DGS10\n2026-08-16,.\n2026-08-17,.\n"))


def test_fetch_current_price_parses_yahoo_chart_response():
    body = {"chart": {"result": [{"meta": {"regularMarketPrice": 316.83}}]}}

    price = fetch_current_price("AAPL", _json_client(body))

    assert price == pytest.approx(316.83)


def test_fetch_current_price_raises_on_http_error():
    with pytest.raises(MarketDataError):
        fetch_current_price("AAPL", _json_client({}, status_code=404))


def test_fetch_current_price_raises_on_malformed_response():
    with pytest.raises(MarketDataError):
        fetch_current_price("AAPL", _json_client({"chart": {"result": []}}))


def test_fetch_current_price_raises_when_price_missing():
    body = {"chart": {"result": [{"meta": {}}]}}
    with pytest.raises(MarketDataError):
        fetch_current_price("AAPL", _json_client(body))
