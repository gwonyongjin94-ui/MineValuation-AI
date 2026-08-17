"""Wires SEC client -> normalizer -> metrics -> DCF -> margin of safety,
plus optional qualitative risk extraction (10-K text and/or a
user-pasted earnings call transcript).

Transport-agnostic on purpose: it raises the data-layer (SECClientError,
UnknownTickerError), valuation-layer (UnsupportedValuationError), and
qualitative-layer (QualitativeAnalysisError) exceptions as-is rather
than translating them to HTTP concerns - that translation belongs to
app/api/analysis.py, so this function stays reusable outside FastAPI (a
script, a test, a future CLI).
"""

from datetime import date

from pydantic import BaseModel

from app.data.filing_documents import fetch_filing_document, list_recent_filings
from app.data.models import CompanyInfo, FinancialStatement
from app.data.sec_client import SECClient
from app.data.ticker_map import TickerMap
from app.financials.metrics import YearMetrics, compute_metrics
from app.financials.normalizer import build_company_info, normalize
from app.qualitative.risk_extraction import (
    QualitativeAnalysisError,
    QualitativeRiskAnalysis,
    RiskSeverity,
    extract_risks,
)
from app.valuation.assumptions import ValuationAssumptions
from app.valuation.dcf import UnsupportedValuationError
from app.valuation.margin_of_safety import MarginOfSafetyResult, compute_margin_of_safety

# Not a numeric MOS adjustment - deliberately. There's no defensible formula
# for "how many dollars of intrinsic value one high-severity qualitative risk
# is worth", and inventing one would repeat exactly the kind of unfounded
# number this project has avoided everywhere else (no auto-derived tax rate,
# no computed WACC, always a range instead of a point estimate). Instead this
# threshold only decides whether a warning is added - the quantitative MOS
# and the qualitative risks are always reported side by side, never merged.
HIGH_SEVERITY_WARNING_THRESHOLD = 2


class AnalysisResult(BaseModel):
    company: CompanyInfo
    financials: list[FinancialStatement]
    metrics: list[YearMetrics]
    margin_of_safety: MarginOfSafetyResult | None
    unsupported_reason: str | None
    qualitative_analyses: list[QualitativeRiskAnalysis]
    sources: list[str]
    warnings: list[str]


def analyze(
    ticker: str,
    market_price: float,
    as_of_date: date,
    assumptions: ValuationAssumptions,
    client: SECClient,
    ticker_map: TickerMap,
    analyze_10k: bool = False,
    earnings_call_text: str | None = None,
    anthropic_client=None,
) -> AnalysisResult:
    if (analyze_10k or earnings_call_text) and anthropic_client is None:
        raise QualitativeAnalysisError(
            "qualitative analysis requested but no Anthropic client configured"
        )

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

    sources = [
        f"https://data.sec.gov/submissions/CIK{cik}.json",
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
    ]

    qualitative_analyses: list[QualitativeRiskAnalysis] = []
    if analyze_10k:
        filings = [f for f in list_recent_filings(submissions) if f.form == "10-K"]
        if filings:
            document = fetch_filing_document(client, cik, filings[0])
            qualitative_analyses.append(
                extract_risks(
                    anthropic_client,
                    document.text,
                    "10-K",
                    source_accession_number=document.accession_number,
                )
            )
            sources.append(document.document_url)

    if earnings_call_text:
        qualitative_analyses.append(
            extract_risks(anthropic_client, earnings_call_text, "Earnings call (user-provided)")
        )

    high_severity_count = sum(
        1
        for analysis in qualitative_analyses
        for risk in analysis.risks
        if risk.severity == RiskSeverity.HIGH
    )
    if high_severity_count >= HIGH_SEVERITY_WARNING_THRESHOLD:
        warnings.append(
            f"{high_severity_count} high-severity qualitative risk(s) identified - "
            "review qualitative_analyses before relying on the quantitative margin "
            "of safety alone"
        )

    return AnalysisResult(
        company=company,
        financials=statements,
        metrics=metrics,
        margin_of_safety=margin_of_safety,
        unsupported_reason=unsupported_reason,
        qualitative_analyses=qualitative_analyses,
        sources=sources,
        warnings=warnings,
    )
