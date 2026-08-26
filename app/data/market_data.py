"""External (non-SEC) market data: the risk-free rate from FRED
(app/valuation/wacc.py), peer current prices for comps
(app/valuation/comps.py), and FX rates for IFRS foreign private issuers
that report in a non-USD currency (app/financials/normalizer.py's
convert_statements_to_usd()) - things this app needs that cannot come
from a company's own SEC filings by definition (a Treasury yield,
another company's live trading price, a currency's exchange rate).
"""

import httpx

DGS10_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


class MarketDataError(Exception):
    """Failed to fetch or parse external (non-SEC) market data."""


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
