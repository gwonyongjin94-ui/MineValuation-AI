"""Comparable Company Analysis ("comps") - peer trading multiples applied
to the target company's own metrics, per the same industry-classification
approach as wacc.py's beta table. A REFERENCE estimate only: `analyze()`
returns this as `comps_estimate` alongside the DCF result and never
substitutes one for the other - the same side-by-side principle as
fundamental_growth_estimate and wacc_estimate.

    EV/EBITDA, EV/Revenue, P/E computed for each peer (EBITDA proxied as
    operating_income + D&A - see fcff.py's module docstring for why
    there is no separate EBITDA tag). Peer median multiple x target's
    own metric = implied enterprise/equity value.

Peer lists are curated, documented constants (major public companies per
industry), not a live SEC discovery query. SEC's own browse-edgar
SIC-search endpoint was tried and rejected: its XML has a long-standing
serialization bug where the company name comes back literally as
"ARRAY(0x...)" instead of a string, and results carry no size/relevance
ranking - the first N matches for a SIC code are essentially alphabetical
noise (shell companies as often as real peers). Ranking candidates by
size would mean fetching every one's market cap before filtering, which
does not fit a single request's latency budget. Same tradeoff already
accepted for wacc.py's industry beta table - see its module docstring.

Peer prices come from the same Yahoo Finance chart API wacc.py-adjacent
market_data.py uses for the risk-free rate lookup's sibling function,
fetch_current_price() - see that module's docstring for the "unofficial
endpoint" caveat.
"""

import statistics

from pydantic import BaseModel

from app.data.exceptions import SECClientError, UnknownTickerError
from app.data.market_data import MarketDataError, fetch_current_price
from app.data.models import FinancialStatement
from app.data.sec_client import SECClient
from app.data.ticker_map import TickerMap
from app.financials.normalizer import normalize

# (SIC prefix, peer tickers). Checked in order, most specific prefix
# first; first match wins - same convention as wacc.py's
# INDUSTRY_UNLEVERED_BETA. A curated handful of large, liquid public
# companies per industry, not exhaustive - covers the industries seen in
# this project's spiked/tested companies plus common large-cap sectors.
INDUSTRY_PEERS: list[tuple[str, list[str]]] = [
    ("2834", ["JNJ", "PFE", "MRK", "ABBV", "LLY", "BMY"]),
    ("2836", ["AMGN", "GILD", "REGN", "VRTX", "BIIB"]),
    ("283", ["JNJ", "PFE", "MRK", "ABBV", "LLY", "BMY"]),
    ("2911", ["XOM", "CVX", "COP", "MPC"]),
    ("1311", ["XOM", "CVX", "COP", "EOG", "OXY"]),
    ("3674", ["NVDA", "AVGO", "QCOM", "TXN", "AMD", "INTC"]),
    ("357", ["AAPL", "DELL", "HPQ", "NTAP"]),
    ("7372", ["MSFT", "ORCL", "CRM", "ADBE", "SAP", "INTU"]),
    ("7370", ["GOOGL", "META", "NFLX"]),
    ("3721", ["BA", "LMT", "RTX", "NOC", "GD"]),
    ("3711", ["GM", "F", "TSLA", "STLA"]),
    ("2086", ["KO", "PEP", "KDP", "MNST"]),
    ("2840", ["PG", "CL", "KMB", "CHD"]),
    ("5211", ["HD", "LOW"]),
    ("541", ["KR", "WMT", "COST", "TGT"]),
    ("531", ["WMT", "TGT", "COST"]),
    ("533", ["WMT", "TGT", "COST"]),
    ("581", ["MCD", "YUM", "SBUX", "CMG"]),
    ("3841", ["MDT", "SYK", "BSX", "ABT"]),
    ("4813", ["VZ", "T", "TMUS"]),
    ("48", ["VZ", "T", "TMUS", "CMCSA"]),
    ("22", ["NKE", "VFC", "RL", "PVH"]),
    ("23", ["NKE", "VFC", "RL", "PVH"]),
    ("3559", ["HON", "CAT", "DE", "ETN"]),
    ("35", ["HON", "CAT", "DE", "ETN"]),
    ("781", ["DIS", "NFLX", "WBD"]),
    ("483", ["DIS", "CMCSA", "FOXA"]),
    ("602", ["JPM", "BAC", "WFC", "C", "USB"]),
    ("603", ["JPM", "BAC", "C"]),
    ("631", ["MET", "PRU", "AFL"]),
    ("632", ["TRV", "CB", "PGR", "ALL"]),
    ("633", ["TRV", "CB", "PGR", "ALL"]),
    ("6199", ["V", "MA", "PYPL", "AXP"]),
    ("671", ["V", "MA", "PYPL", "AXP"]),
]

# Below this count, a median is still computed (never silently withheld -
# same "compute it, but warn" pattern as select_base_fcff() with too few
# years) but flagged as a thin/low-confidence sample.
LOW_CONFIDENCE_PEER_COUNT = 3


class PeerMultiple(BaseModel):
    ticker: str
    market_price: float
    ev_to_ebitda: float | None = None
    ev_to_revenue: float | None = None
    price_to_earnings: float | None = None
    warnings: list[str] = []


class CompsEstimate(BaseModel):
    industry_sic_prefix: str
    peers: list[PeerMultiple]
    median_ev_to_ebitda: float | None = None
    median_ev_to_revenue: float | None = None
    median_price_to_earnings: float | None = None
    implied_value_per_share_ebitda: float | None = None
    implied_value_per_share_revenue: float | None = None
    implied_value_per_share_earnings: float | None = None
    # min/max across whichever of the three implied values above are
    # computable - the "conservative to optimistic" range for this
    # method, on the same footing as DCF's/Owner Earnings' own ranges
    # (see app/valuation/consensus.py). Not a confidence interval - just
    # how far apart the three multiple-based methods land.
    value_per_share_low: float | None = None
    value_per_share_high: float | None = None
    warnings: list[str] = []


def _value(fact) -> float | None:
    return fact.value if fact is not None else None


def peers_for_sic(sic: str | None) -> tuple[str, list[str]]:
    if sic:
        for prefix, tickers in INDUSTRY_PEERS:
            if sic.startswith(prefix):
                return prefix, tickers
    return "", []


def _enterprise_value(statement: FinancialStatement, price: float, shares: float) -> float:
    debt = (_value(statement.short_term_debt) or 0.0) + (_value(statement.long_term_debt) or 0.0)
    cash = _value(statement.cash) or 0.0
    return price * shares + debt - cash


def _peer_multiple(ticker: str, statement: FinancialStatement, price: float) -> PeerMultiple:
    warnings: list[str] = []
    shares = _value(statement.shares_outstanding)
    if shares is None or shares <= 0:
        return PeerMultiple(
            ticker=ticker, market_price=price, warnings=["shares_outstanding not found"]
        )

    ev = _enterprise_value(statement, price, shares)

    operating_income = _value(statement.operating_income)
    d_and_a = _value(statement.depreciation_amortization)
    ev_to_ebitda = None
    if operating_income is None or d_and_a is None:
        warnings.append("operating_income/depreciation_amortization not found for EV/EBITDA")
    else:
        ebitda = operating_income + d_and_a
        if ebitda > 0:
            ev_to_ebitda = ev / ebitda
        else:
            warnings.append("EBITDA is zero or negative - EV/EBITDA not meaningful")

    revenue = _value(statement.revenue)
    ev_to_revenue = None
    if revenue is None or revenue <= 0:
        warnings.append("revenue not found - cannot compute EV/Revenue")
    else:
        ev_to_revenue = ev / revenue

    net_income = _value(statement.net_income)
    price_to_earnings = None
    if net_income is None or net_income <= 0:
        warnings.append("net_income not found or non-positive - P/E not meaningful")
    else:
        price_to_earnings = price / (net_income / shares)

    return PeerMultiple(
        ticker=ticker,
        market_price=price,
        ev_to_ebitda=ev_to_ebitda,
        ev_to_revenue=ev_to_revenue,
        price_to_earnings=price_to_earnings,
        warnings=warnings,
    )


def _median_or_none(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def estimate_comps(
    target_ticker: str,
    target_statement: FinancialStatement,
    client: SECClient,
    ticker_map: TickerMap,
    market_data_client,
) -> CompsEstimate:
    sic = target_statement.company.sic
    prefix, peer_tickers = peers_for_sic(sic)
    peer_tickers = [t for t in peer_tickers if t.upper() != target_ticker.upper()]

    if not peer_tickers:
        return CompsEstimate(
            industry_sic_prefix=prefix,
            peers=[],
            warnings=[f"no curated peer list for SIC {sic!r}"],
        )

    peers: list[PeerMultiple] = []
    skipped: list[str] = []
    for ticker in peer_tickers:
        try:
            cik = ticker_map.resolve(ticker)
            submissions = client.get_submissions(cik)
            company_facts = client.get_company_facts(cik)
            statements = normalize(company_facts, submissions)
            if not statements:
                skipped.append(f"{ticker}: no statements available")
                continue
            latest = max(statements, key=lambda s: s.period_end)
            price = fetch_current_price(ticker, market_data_client)
        except (SECClientError, UnknownTickerError, MarketDataError) as exc:
            skipped.append(f"{ticker}: {exc}")
            continue
        peers.append(_peer_multiple(ticker, latest, price))

    warnings = [f"peer {s} - skipped" for s in skipped]

    ev_ebitda_values = [p.ev_to_ebitda for p in peers if p.ev_to_ebitda]
    ev_revenue_values = [p.ev_to_revenue for p in peers if p.ev_to_revenue]
    pe_values = [p.price_to_earnings for p in peers if p.price_to_earnings]
    median_ev_ebitda = _median_or_none(ev_ebitda_values)
    median_ev_revenue = _median_or_none(ev_revenue_values)
    median_pe = _median_or_none(pe_values)

    for label, values in (
        ("EV/EBITDA", ev_ebitda_values),
        ("EV/Revenue", ev_revenue_values),
        ("P/E", pe_values),
    ):
        if 0 < len(values) < LOW_CONFIDENCE_PEER_COUNT:
            warnings.append(
                f"{label} median from only {len(values)} peer(s) (of {len(peer_tickers)} "
                "in the curated list) - low-confidence sample"
            )

    target_shares = _value(target_statement.shares_outstanding)
    target_debt = (
        _value(target_statement.short_term_debt) or 0.0
    ) + (_value(target_statement.long_term_debt) or 0.0)
    target_cash = _value(target_statement.cash) or 0.0
    target_operating_income = _value(target_statement.operating_income)
    target_d_and_a = _value(target_statement.depreciation_amortization)
    target_revenue = _value(target_statement.revenue)
    target_net_income = _value(target_statement.net_income)

    implied_ebitda = None
    implied_revenue = None
    implied_earnings = None
    if not target_shares:
        warnings.append("target shares_outstanding not found - cannot imply per-share value")
    else:
        if median_ev_ebitda is not None and None not in (target_operating_income, target_d_and_a):
            target_ebitda = target_operating_income + target_d_and_a
            implied_ev = median_ev_ebitda * target_ebitda
            implied_ebitda = (implied_ev - target_debt + target_cash) / target_shares
        if median_ev_revenue is not None and target_revenue:
            implied_ev = median_ev_revenue * target_revenue
            implied_revenue = (implied_ev - target_debt + target_cash) / target_shares
        if median_pe is not None and target_net_income:
            implied_earnings = median_pe * (target_net_income / target_shares)

    implied_values = [
        v for v in (implied_ebitda, implied_revenue, implied_earnings) if v is not None
    ]
    value_low = min(implied_values) if implied_values else None
    value_high = max(implied_values) if implied_values else None

    return CompsEstimate(
        industry_sic_prefix=prefix,
        peers=peers,
        median_ev_to_ebitda=median_ev_ebitda,
        median_ev_to_revenue=median_ev_revenue,
        median_price_to_earnings=median_pe,
        implied_value_per_share_ebitda=implied_ebitda,
        implied_value_per_share_revenue=implied_revenue,
        implied_value_per_share_earnings=implied_earnings,
        value_per_share_low=value_low,
        value_per_share_high=value_high,
        warnings=warnings,
    )
