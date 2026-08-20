import httpx
import pytest

from app.data.market_data import MarketDataError, fetch_risk_free_rate


def _client(response_text: str, status_code: int = 200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text=response_text)

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
