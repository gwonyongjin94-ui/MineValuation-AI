"""Risk-free rate from FRED (DGS10, 10-Year Treasury Constant Maturity) -
the one genuinely external, non-SEC, live-fetched data source in this
app. Needed by app/valuation/wacc.py for cost-of-equity/cost-of-debt
estimates - CAPM's risk-free rate isn't derivable from any single
company's own SEC filings by definition (it's a government bond yield,
not a fact about the company). No API key required: FRED serves this
series as a plain CSV.
"""

import httpx

DGS10_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"


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


def build_default_market_data_client() -> httpx.Client:
    return httpx.Client(timeout=10.0)
