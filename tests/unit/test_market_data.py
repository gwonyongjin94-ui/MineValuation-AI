from datetime import date

import httpx
import pytest

from app.data.market_data import (
    MarketDataError,
    PricePoint,
    close_on_or_after,
    fetch_current_price,
    fetch_fx_rate,
    fetch_price_history,
    fetch_risk_free_rate,
)


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


def test_fetch_fx_rate_same_currency_short_circuits_without_a_request():
    # No HTTP call needed for USD->USD - passing a client that would
    # error if actually hit proves the short-circuit happens first.
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not make an HTTP request for same-currency conversion")

    client = httpx.Client(transport=httpx.MockTransport(handler))

    assert fetch_fx_rate("USD", "USD", client) == 1.0


def test_fetch_fx_rate_builds_the_yahoo_pair_ticker():
    # DKK->USD reproduces the real Novo Nordisk (NVO) case - checked live
    # that Yahoo's chart API accepts "DKKUSD=X" as an ordinary ticker.
    seen_urls = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        body = {"chart": {"result": [{"meta": {"regularMarketPrice": 0.156}}]}}
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    rate = fetch_fx_rate("DKK", "USD", client)

    assert rate == pytest.approx(0.156)
    assert "DKKUSD=X" in seen_urls[0]


def _history_body(points: list[tuple[int, float | None]]) -> dict:
    return {
        "chart": {
            "result": [
                {
                    "timestamp": [ts for ts, _ in points],
                    "indicators": {"quote": [{"close": [close for _, close in points]}]},
                }
            ]
        }
    }


def test_fetch_price_history_parses_timestamp_and_close_arrays():
    # 2026-01-02 and 2026-01-05 UTC.
    body = _history_body([(1767312000, 100.0), (1767571200, 105.5)])

    history = fetch_price_history("AAPL", _json_client(body))

    assert [p.date for p in history] == [date(2026, 1, 2), date(2026, 1, 5)]
    assert [p.close for p in history] == [pytest.approx(100.0), pytest.approx(105.5)]


def test_fetch_price_history_drops_null_closes_rather_than_interpolating():
    # Yahoo reports null closes for halted/early-close sessions - a gap is
    # real missing data, not something to fill in.
    body = _history_body([(1767312000, 100.0), (1767398400, None), (1767571200, 105.5)])

    history = fetch_price_history("AAPL", _json_client(body))

    assert len(history) == 2
    assert [p.date for p in history] == [date(2026, 1, 2), date(2026, 1, 5)]


def test_fetch_price_history_raises_when_every_close_is_null():
    body = _history_body([(1767312000, None)])

    with pytest.raises(MarketDataError, match="no usable price history"):
        fetch_price_history("AAPL", _json_client(body))


def test_fetch_price_history_raises_on_malformed_response():
    with pytest.raises(MarketDataError, match="could not parse"):
        fetch_price_history("AAPL", _json_client({"chart": {"result": [{}]}}))


def test_close_on_or_after_rolls_forward_to_the_next_trading_day():
    history = [
        PricePoint(date=date(2026, 3, 13), close=250.0),
        PricePoint(date=date(2026, 3, 16), close=252.8),
    ]

    # 2026-03-15 is a Sunday - resolves to Monday's close, not Friday's.
    assert close_on_or_after(history, date(2026, 3, 15)).date == date(2026, 3, 16)
    # An exact trading day resolves to itself.
    assert close_on_or_after(history, date(2026, 3, 13)).close == pytest.approx(250.0)


def test_close_on_or_after_returns_none_past_the_end_of_the_series():
    # An outcome that hasn't happened yet stays unresolved rather than
    # being scored against the latest available (stale) price.
    history = [PricePoint(date=date(2026, 3, 13), close=250.0)]

    assert close_on_or_after(history, date(2030, 1, 1)) is None
