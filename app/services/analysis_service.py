"""Wires SEC client -> normalizer -> metrics -> DCF -> margin of safety,
plus optional qualitative risk extraction (10-K text and/or a
user-pasted earnings call transcript), optional FinBERT sentiment
scoring of that same text, an optional WACC estimate from real market
data (app/valuation/wacc.py), an optional comparable-company analysis
(app/valuation/comps.py), and an Owner Earnings DCF (per Buffett -
app/valuation/owner_earnings.py). Growth/WACC/comps are reference
figures only - never substituted into `assumptions` or
`margin_of_safety`. DCF-FCFF, Owner Earnings DCF, and comps each
produce their own conservative-to-optimistic value-per-share range;
app/valuation/consensus.py computes the overlap across whichever of
them are available for this request as `valuation_consensus`.

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
from app.data.market_data import MarketDataError, fetch_fx_rate, fetch_risk_free_rate
from app.data.models import CompanyInfo, FinancialStatement
from app.data.sec_client import SECClient
from app.data.ticker_map import TickerMap
from app.financials.metrics import YearMetrics, compute_metrics
from app.financials.normalizer import (
    build_company_info,
    convert_statements_to_usd,
    normalize,
    statement_currency,
)
from app.qualitative.risk_extraction import (
    QualitativeAnalysisError,
    QualitativeRiskAnalysis,
    RiskSeverity,
    extract_risks,
    run_cross_model_extraction,
)
from app.qualitative.sentiment import SentimentSummary, score_sentiment
from app.valuation.assumptions import ValuationAssumptions
from app.valuation.comps import CompsEstimate, estimate_comps
from app.valuation.consensus import ValuationConsensus, ValueRange, compute_consensus
from app.valuation.dcf import UnsupportedValuationError
from app.valuation.growth import FundamentalGrowthEstimate, estimate_fundamental_growth_rate
from app.valuation.margin_of_safety import MarginOfSafetyResult, compute_margin_of_safety
from app.valuation.owner_earnings import OwnerEarningsDCFResult, run_owner_earnings_dcf_valuation
from app.valuation.wacc import FALLBACK_RISK_FREE_RATE, WACCEstimate, estimate_wacc

# Not a numeric MOS adjustment - deliberately. There's no defensible formula
# for "how many dollars of intrinsic value one high-severity qualitative risk
# is worth", and inventing one would repeat exactly the kind of unfounded
# number this project has avoided everywhere else (no auto-derived tax rate,
# always a range instead of a point estimate). Instead this threshold only
# decides whether a warning is added - the quantitative MOS and the
# qualitative risks are always reported side by side, never merged.
HIGH_SEVERITY_WARNING_THRESHOLD = 2


class AnalysisResult(BaseModel):
    company: CompanyInfo
    financials: list[FinancialStatement]
    metrics: list[YearMetrics]
    margin_of_safety: MarginOfSafetyResult | None
    unsupported_reason: str | None
    fundamental_growth_estimate: FundamentalGrowthEstimate
    wacc_estimate: WACCEstimate | None
    comps_estimate: CompsEstimate | None
    owner_earnings_estimate: OwnerEarningsDCFResult | None
    valuation_consensus: ValuationConsensus
    qualitative_analyses: list[QualitativeRiskAnalysis]
    sentiment_analyses: list[SentimentSummary]
    sources: list[str]
    warnings: list[str]


def _run_qualitative_extraction(
    anthropic_client,
    text: str,
    source_label: str,
    source_accession_number: str | None,
    cross_validate: bool,
) -> tuple[list[QualitativeRiskAnalysis], list[str]]:
    if not cross_validate:
        analysis = extract_risks(anthropic_client, text, source_label, source_accession_number)
        return [analysis], []

    cross_result = run_cross_model_extraction(
        anthropic_client, text, source_label, source_accession_number
    )
    warnings: list[str] = []
    if cross_result.failed_models:
        total = len(cross_result.failed_models) + len(cross_result.analyses)
        warnings.append(
            f"{source_label} cross-model validation: {len(cross_result.failed_models)} of "
            f"{total} model(s) failed - " + "; ".join(cross_result.failed_models)
        )
    elif cross_result.disagreement:
        low, high = cross_result.high_severity_count_range
        warnings.append(
            f"{source_label} cross-model disagreement: high-severity risk count ranged "
            f"{low}-{high} across models - review qualitative_analyses"
        )
    return cross_result.analyses, warnings


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
    include_sentiment: bool = False,
    sentiment_classifier=None,
    cross_validate: bool = False,
    compute_wacc: bool = False,
    compute_comps: bool = False,
    market_data_client=None,
) -> AnalysisResult:
    if (analyze_10k or earnings_call_text) and anthropic_client is None:
        raise QualitativeAnalysisError(
            "qualitative analysis requested but no Anthropic client configured"
        )
    if (compute_wacc or compute_comps) and market_data_client is None:
        raise MarketDataError(
            "compute_wacc/compute_comps requested but no market data client configured"
        )

    cik = ticker_map.resolve(ticker)
    submissions = client.get_submissions(cik)
    company_facts = client.get_company_facts(cik)

    company = build_company_info(company_facts, submissions)
    statements = normalize(company_facts, submissions)

    warnings = [
        f"FY{statement.fiscal_year}: {warning}"
        for statement in statements
        for warning in statement.warnings
    ]

    # An IFRS foreign private issuer (Form 20-F, e.g. NVO) reports its
    # financials in its home currency, not USD - `market_price` is always
    # assumed USD (that's how it's quoted for a US-listed ticker), so
    # every monetary fact must be converted before anything downstream
    # (FCFF, DCF, WACC, comps, owner earnings) reads it. Applied once,
    # here, right after normalize() - not inside it, which stays
    # offline/pure on purpose - so every consumer of `statements` below
    # gets already-USD figures with no currency-awareness of its own.
    reporting_currency = statement_currency(statements[-1]) if statements else None
    if reporting_currency and reporting_currency != "USD":
        if market_data_client is None:
            raise MarketDataError(
                f"{ticker} reports in {reporting_currency}, not USD - a market data client "
                "is required to fetch a live FX rate for currency conversion"
            )
        fx_rate = fetch_fx_rate(reporting_currency, "USD", market_data_client)
        statements = convert_statements_to_usd(statements, fx_rate, reporting_currency)
        warnings.append(
            f"financial statements converted from {reporting_currency} to USD at a live "
            f"spot rate of {fx_rate:.4f} (fetched just now, not the rate on each fact's "
            "own filing date) - see LIMITATIONS.md"
        )

    metrics = compute_metrics(statements)
    fundamental_growth_estimate = estimate_fundamental_growth_rate(
        statements, assumptions.tax_rate, market_price=market_price
    )

    wacc_estimate = None
    if compute_wacc and statements:
        try:
            risk_free_rate = fetch_risk_free_rate(market_data_client)
        except MarketDataError as exc:
            # FRED's live fetch has a confirmed real failure mode (see
            # wacc.py's module docstring) - fall back to a dated constant
            # with an explicit warning rather than losing the whole
            # estimate over one unreachable data point.
            risk_free_rate = FALLBACK_RISK_FREE_RATE
            warnings.append(
                f"risk-free rate unavailable ({exc}) - used a fallback constant "
                f"({FALLBACK_RISK_FREE_RATE:.1%}, may be stale) for wacc_estimate"
            )
        latest_statement = max(statements, key=lambda s: s.period_end)
        wacc_estimate = estimate_wacc(
            latest_statement, market_price, risk_free_rate, assumptions.tax_rate
        )

    comps_estimate = None
    if compute_comps and statements:
        try:
            latest_statement = max(statements, key=lambda s: s.period_end)
            comps_estimate = estimate_comps(
                ticker, latest_statement, client, ticker_map, market_data_client
            )
        except MarketDataError as exc:
            warnings.append(f"comps estimate unavailable: {exc}")

    margin_of_safety = None
    unsupported_reason = None
    try:
        margin_of_safety = compute_margin_of_safety(
            statements, assumptions, market_price, as_of_date
        )
    except UnsupportedValuationError as exc:
        unsupported_reason = str(exc)

    owner_earnings_estimate = None
    try:
        owner_earnings_estimate = run_owner_earnings_dcf_valuation(statements, assumptions)
    except UnsupportedValuationError as exc:
        warnings.append(f"Owner Earnings DCF unavailable: {exc}")

    value_ranges = []
    if margin_of_safety is not None and None not in (
        margin_of_safety.intrinsic_value_low,
        margin_of_safety.intrinsic_value_high,
    ):
        value_ranges.append(
            ValueRange(
                method="DCF (FCFF)",
                low=margin_of_safety.intrinsic_value_low,
                high=margin_of_safety.intrinsic_value_high,
            )
        )
    if owner_earnings_estimate is not None and None not in (
        owner_earnings_estimate.value_per_share_low,
        owner_earnings_estimate.value_per_share_high,
    ):
        value_ranges.append(
            ValueRange(
                method="DCF (Owner Earnings)",
                low=owner_earnings_estimate.value_per_share_low,
                high=owner_earnings_estimate.value_per_share_high,
            )
        )
    if comps_estimate is not None and None not in (
        comps_estimate.value_per_share_low,
        comps_estimate.value_per_share_high,
    ):
        value_ranges.append(
            ValueRange(
                method="Comps",
                low=comps_estimate.value_per_share_low,
                high=comps_estimate.value_per_share_high,
            )
        )
    valuation_consensus = compute_consensus(value_ranges)

    sources = [
        f"https://data.sec.gov/submissions/CIK{cik}.json",
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
    ]

    qualitative_analyses: list[QualitativeRiskAnalysis] = []
    sentiment_analyses: list[SentimentSummary] = []
    if analyze_10k:
        filings = [f for f in list_recent_filings(submissions) if f.form == "10-K"]
        if filings:
            document = fetch_filing_document(client, cik, filings[0])
            new_analyses, cross_warnings = _run_qualitative_extraction(
                anthropic_client, document.text, "10-K", document.accession_number, cross_validate
            )
            qualitative_analyses.extend(new_analyses)
            warnings.extend(cross_warnings)
            if include_sentiment:
                sentiment_analyses.append(
                    score_sentiment(document.text, "10-K", classifier=sentiment_classifier)
                )
            sources.append(document.document_url)

    if earnings_call_text:
        new_analyses, cross_warnings = _run_qualitative_extraction(
            anthropic_client,
            earnings_call_text,
            "Earnings call (user-provided)",
            None,
            cross_validate,
        )
        qualitative_analyses.extend(new_analyses)
        warnings.extend(cross_warnings)
        if include_sentiment:
            sentiment_analyses.append(
                score_sentiment(
                    earnings_call_text,
                    "Earnings call (user-provided)",
                    classifier=sentiment_classifier,
                )
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
        fundamental_growth_estimate=fundamental_growth_estimate,
        wacc_estimate=wacc_estimate,
        comps_estimate=comps_estimate,
        owner_earnings_estimate=owner_earnings_estimate,
        valuation_consensus=valuation_consensus,
        qualitative_analyses=qualitative_analyses,
        sentiment_analyses=sentiment_analyses,
        sources=sources,
        warnings=warnings,
    )
