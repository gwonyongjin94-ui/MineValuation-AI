"""Wires SEC client -> normalizer -> metrics -> DCF -> margin of safety.

Transport-agnostic on purpose: it raises the data-layer (SECClientError,
UnknownTickerError) and valuation-layer (UnsupportedValuationError)
exceptions as-is rather than translating them to HTTP concerns - that
translation belongs to app/api/analysis.py, so this function stays
reusable outside FastAPI (a script, a test, a future CLI).
"""

from datetime import date

from pydantic import BaseModel

from app.data.models import CompanyInfo, FinancialStatement
from app.data.sec_client import SECClient
from app.data.ticker_map import TickerMap
from app.financials.metrics import YearMetrics, compute_metrics
from app.financials.normalizer import build_company_info, normalize
from app.valuation.assumptions import ValuationAssumptions
from app.valuation.dcf import UnsupportedValuationError
from app.valuation.margin_of_safety import MarginOfSafetyResult, compute_margin_of_safety


class AnalysisResult(BaseModel):
    company: CompanyInfo
    financials: list[FinancialStatement]
    metrics: list[YearMetrics]
    margin_of_safety: MarginOfSafetyResult | None
    unsupported_reason: str | None
    sources: list[str]
    warnings: list[str]


def analyze(
    ticker: str,
    market_price: float,
    as_of_date: date,
    assumptions: ValuationAssumptions,
    client: SECClient,
    ticker_map: TickerMap,
) -> AnalysisResult:
    cik = ticker_map.resolve(ticker)
    submissions = client.get_submissions(cik)
    company_facts = client.get_company_facts(cik)

    company = build_company_info(company_facts, submissions)
    statements = normalize(company_facts, submissions)
    metrics = compute_metrics(statements)

    warnings = [
        f"FY{statement.fiscal_year}: {warning}"
        for statement in statements
        for warning in statement.warnings
    ]

    margin_of_safety = None
    unsupported_reason = None
    try:
        margin_of_safety = compute_margin_of_safety(
            statements, assumptions, market_price, as_of_date
        )
    except UnsupportedValuationError as exc:
        unsupported_reason = str(exc)

    return AnalysisResult(
        company=company,
        financials=statements,
        metrics=metrics,
        margin_of_safety=margin_of_safety,
        unsupported_reason=unsupported_reason,
        sources=[
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
        ],
        warnings=warnings,
    )
