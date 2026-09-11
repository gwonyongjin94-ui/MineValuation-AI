"""External (non-SEC) market data: the risk-free rate from FRED
(app/valuation/wacc.py), peer current prices for comps
(app/valuation/comps.py), FX rates for IFRS foreign private issuers
that report in a non-USD currency (app/financials/normalizer.py's
convert_statements_to_usd()), and daily price history for outcome
resolution (app/memory/) - things this app needs that cannot come
from a company's own SEC filings by definition (a Treasury yield,
another company's live trading price, a currency's exchange rate).
"""

from datetime import UTC, date, datetime

import httpx
from pydantic import BaseModel

DGS10_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


class MarketDataError(Exception):
    """Failed to fetch or parse external (non-SEC) market data."""


class PricePoint(BaseModel):
    date: date
    close: float


def fetch_risk_free_rate(client: httpx.Client) -> float:
    """Latest available 10-year Treasury yield, as a decimal (4.71% -> 0.0471).

    FRED reports "." on days with no observation (market holidays) - walks
    backward from the most recent row to the latest actual number rather
    than failing or guessing.
    """
    try:
        response = client.get(DGS10_CSV_URL)
    except httpx.RequestError as exc:
        raise MarketDataError(f"failed to reach FRED: {exc}") from exc
    if response.status_code >= 400:
        raise MarketDataError(f"FRED returned HTTP {response.status_code}")

    lines = [ln for ln in response.text.strip().splitlines() if ln]
    if len(lines) < 2:
        raise MarketDataError("FRED returned no observations")

    for line in reversed(lines[1:]):
        _, _, value = line.rpartition(",")
        if value in ("", "."):
            continue
        try:
            return float(value) / 100
        except ValueError:
            continue

    raise MarketDataError("FRED returned no usable 10-year Treasury observation")


def fetch_current_price(ticker: str, client: httpx.Client) -> float:
    """Latest traded price for `ticker`, via Yahoo Finance's chart API.

    Unofficial/undocumented endpoint (no API key, no official terms of
    use) - verified live and stable through this project's development
    (used for the 30-DJIA-constituent reference spreadsheet before this
    module existed), but flagged here as the one dependency with no
    guaranteed uptime/format contract, unlike FRED or SEC EDGAR.
    """
    url = YAHOO_CHART_URL.format(ticker=ticker.upper())
    try:
        response = client.get(url, params={"range": "5d", "interval": "1d"})
    except httpx.RequestError as exc:
        raise MarketDataError(f"failed to reach Yahoo Finance for {ticker}: {exc}") from exc
    if response.status_code >= 400:
        raise MarketDataError(f"Yahoo Finance returned HTTP {response.status_code} for {ticker}")

    try:
        data = response.json()
        price = data["chart"]["result"][0]["meta"]["regularMarketPrice"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise MarketDataError(f"could not parse Yahoo Finance response for {ticker}") from exc

    if not isinstance(price, int | float):
        raise MarketDataError(f"Yahoo Finance returned no usable price for {ticker}")
    return float(price)


def fetch_price_history(ticker: str, client: httpx.Client, range_: str = "2y") -> list[PricePoint]:
    """Daily closing prices for `ticker`, oldest first.

    Same endpoint fetch_current_price() already uses - it only ever
    parsed `meta.regularMarketPrice` (the latest tick), so an earlier
    design note recorded "no historical range available" as a hard
    limitation of this data source. That was a limitation of the
    parsing, not the source: checked live, `range=1y` returns 251 daily
    closes alongside the same meta block. Outcome resolution
    (app/memory/) needs the series, not just the latest price, so this
    reads the `timestamp` / `indicators.quote[0].close` arrays instead.

    Yahoo reports `null` closes for some sessions (halts, early closes);
    those points are dropped rather than interpolated - a gap in the
    series is real missing data, and this project doesn't invent values
    to fill one.
    """
    url = YAHOO_CHART_URL.format(ticker=ticker.upper())
    try:
        response = client.get(url, params={"range": range_, "interval": "1d"})
    except httpx.RequestError as exc:
        raise MarketDataError(f"failed to reach Yahoo Finance for {ticker}: {exc}") from exc
    if response.status_code >= 400:
        raise MarketDataError(f"Yahoo Finance returned HTTP {response.status_code} for {ticker}")

    try:
        result = response.json()["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise MarketDataError(
            f"could not parse Yahoo Finance price history for {ticker}"
        ) from exc

    history = [
        PricePoint(date=datetime.fromtimestamp(ts, tz=UTC).date(), close=float(close))
        for ts, close in zip(timestamps, closes, strict=False)
        if isinstance(close, int | float)
    ]
    if not history:
        raise MarketDataError(f"Yahoo Finance returned no usable price history for {ticker}")
    return history


def close_on_or_after(history: list[PricePoint], target: date) -> PricePoint | None:
    """First trading day at or after `target` - the price a decision made
    on `target` would actually have been resolvable against.

    Returns None when `target` is past the end of the series (the outcome
    hasn't happened yet), which is how an unresolved decision stays
    unresolved instead of being scored against a stale price.
    """
    for point in history:
        if point.date >= target:
            return point
    return None


def fetch_fx_rate(from_currency: str, to_currency: str, client: httpx.Client) -> float:
    """Live spot rate to convert one unit of `from_currency` into
    `to_currency`.

    Reuses fetch_current_price() rather than a separate HTTP path - Yahoo
    Finance prices FX pairs as ordinary tickers ("DKKUSD=X"), verified
    live to return a plausible real DKK->USD rate through the exact same
    chart-API endpoint this module already uses for equities.
    """
    if from_currency == to_currency:
        return 1.0
    return fetch_current_price(f"{from_currency}{to_currency}=X", client)


def build_default_market_data_client() -> httpx.Client:
    return httpx.Client(timeout=10.0, headers={"User-Agent": "Mozilla/5.0"})
