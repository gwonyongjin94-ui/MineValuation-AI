"""Wires SEC client -> normalizer -> metrics -> DCF -> margin of safety,
plus optional qualitative risk extraction (10-K text and/or a
user-pasted earnings call transcript), optional FinBERT sentiment
scoring of that same text, an optional WACC estimate from real market
data (app/valuation/wacc.py), an optional comparable-company analysis
(app/valuation/comps.py), an Owner Earnings DCF (per Buffett -
app/valuation/owner_earnings.py), an H-Model DCF with linearly fading
growth (app/valuation/h_model.py), and a Residual Income valuation
(app/valuation/residual_income.py). Growth/WACC/comps are reference
figures only - never substituted into `assumptions` or
`margin_of_safety`, UNLESS `use_wacc_as_discount_rate` is explicitly
set, which overrides `assumptions.discount_rate` with the CAPM-derived
WACC before any valuation method runs (see below). DCF-FCFF, Owner
Earnings DCF, H-Model DCF, Residual Income, and comps each produce
their own conservative-to-optimistic value-per-share range;
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
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from app.config import get_settings
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
from app.memory.decision_log import (
    append_record,
    format_track_record,
    now_utc,
    read_records,
    recent_lessons,
)
from app.memory.models import DecisionRecord, Stance
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
from app.valuation.h_model import HModelEstimate, run_h_model_estimate
from app.valuation.margin_of_safety import MarginOfSafetyResult, compute_margin_of_safety
from app.valuation.owner_earnings import OwnerEarningsDCFResult, run_owner_earnings_dcf_valuation
from app.valuation.residual_income import ResidualIncomeEstimate, run_residual_income_estimate
from app.valuation.wacc import FALLBACK_RISK_FREE_RATE, WACCEstimate, estimate_wacc

# Not a numeric MOS adjustment - deliberately. There's no defensible formula
# for "how many dollars of intrinsic value one high-severity qualitative risk
# is worth", and inventing one would repeat exactly the kind of unfounded
# number this project has avoided everywhere else (no auto-derived tax rate,
# always a range instead of a point estimate). Instead this threshold only
# decides whether a warning is added - the quantitative MOS and the
# qualitative risks are always reported side by side, never merged.
HIGH_SEVERITY_WARNING_THRESHOLD = 2

# How many past lessons get injected into a qualitative prompt when
# include_track_record is set. Small on purpose: these compete for
# attention with the filing text itself (60-70k tokens for a real 10-K),
# and the point is calibration, not a second corpus to reason over.
TRACK_RECORD_LESSON_LIMIT = 5


def build_decision_record(
    result: "AnalysisResult",
    *,
    ticker: str,
    as_of_date: date,
    market_price: float,
    assumptions: ValuationAssumptions,
    used_wacc_as_discount_rate: bool,
) -> DecisionRecord:
    """Maps a finished AnalysisResult to the append-only log's schema.

    Lives here rather than in app/memory/ so that package stays a leaf
    that a standalone script can import without pulling in FastAPI, the
    SEC client, or this module - see decision_log.py's docstring.

    `assumptions` is passed explicitly rather than read off `result`
    because use_wacc_as_discount_rate rewrites it mid-analysis; the
    caller has the version that actually ran.
    """
    mos = result.margin_of_safety
    if mos is None or mos.margin_of_safety is None:
        stance = Stance.UNSUPPORTED
    elif mos.margin_of_safety > 0:
        stance = Stance.UNDERVALUED
    else:
        stance = Stance.OVERVALUED

    # Counts by severity only - no labels, no quotes, nothing derived
    # from earnings_call_text. See app/memory/models.py on why.
    risk_counts: dict[str, int] = {}
    for analysis in result.qualitative_analyses:
        for risk in analysis.risks:
            risk_counts[risk.severity.value] = risk_counts.get(risk.severity.value, 0) + 1

    consensus = result.valuation_consensus
    return DecisionRecord(
        decision_id=uuid4().hex,
        logged_at=now_utc(),
        ticker=ticker.upper(),
        as_of_date=as_of_date,
        market_price=market_price,
        valuation_category=result.company.valuation_category.value,
        stance=stance,
        assumptions=assumptions,
        used_wacc_as_discount_rate=used_wacc_as_discount_rate,
        intrinsic_value_per_share=mos.intrinsic_value_per_share if mos else None,
        intrinsic_value_low=mos.intrinsic_value_low if mos else None,
        intrinsic_value_high=mos.intrinsic_value_high if mos else None,
        margin_of_safety=mos.margin_of_safety if mos else None,
        consensus_methods=[r.method for r in consensus.ranges],
        consensus_low=consensus.overlap_low,
        consensus_high=consensus.overlap_high,
        qualitative_sources=[a.source_label for a in result.qualitative_analyses],
        qualitative_risk_counts=risk_counts,
        unsupported_reason=result.unsupported_reason,
        warning_count=len(result.warnings),
    )


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
    h_model_estimate: HModelEstimate | None
    residual_income_estimate: ResidualIncomeEstimate | None
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
    track_record: str | None = None,
) -> tuple[list[QualitativeRiskAnalysis], list[str]]:
    if not cross_validate:
        analysis = extract_risks(
            anthropic_client,
            text,
            source_label,
            source_accession_number,
            track_record=track_record,
        )
        return [analysis], []

    cross_result = run_cross_model_extraction(
        anthropic_client,
        text,
        source_label,
        source_accession_number,
        track_record=track_record,
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
    use_wacc_as_discount_rate: bool = False,
    market_data_client=None,
    log_decision: bool = False,
    include_track_record: bool = False,
    decision_log_path: Path | None = None,
) -> AnalysisResult:
    # Implies compute_wacc rather than requiring the caller to set both -
    # there's no real use case for "override the discount rate with WACC"
    # without also computing that WACC.
    compute_wacc = compute_wacc or use_wacc_as_discount_rate

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

    # The one place a reference figure DOES get substituted into the real
    # valuation math, and only because the caller explicitly asked for
    # it. Must happen before margin_of_safety/owner_earnings/h_model/
    # residual_income below - all of them read assumptions.discount_rate.
    if use_wacc_as_discount_rate:
        original_discount_rate = assumptions.discount_rate
        if wacc_estimate is not None and wacc_estimate.wacc is not None:
            try:
                assumptions = assumptions.model_copy(
                    update={"discount_rate": wacc_estimate.wacc}
                )
                warnings.append(
                    f"discount_rate overridden with CAPM-derived WACC "
                    f"({wacc_estimate.wacc:.2%}) instead of the requested "
                    f"{original_discount_rate:.2%} - use_wacc_as_discount_rate was set"
                )
            except ValueError as exc:
                warnings.append(
                    f"could not use WACC as discount_rate ({exc}) - kept "
                    f"{original_discount_rate:.2%}"
                )
        else:
            warnings.append(
                "use_wacc_as_discount_rate requested but wacc_estimate.wacc is "
                f"unavailable - kept assumptions.discount_rate ({original_discount_rate:.2%})"
            )

    # Residual Income needs a cost of equity specifically (it discounts
    # equity-level residual income, not firm-level FCFF) - CAPM's real,
    # company-specific figure when compute_wacc produced one, otherwise
    # assumptions.discount_rate as a documented approximation (this
    # project already treats that as a single flat proxy everywhere
    # else, so falling back to it here is consistent, not a new
    # simplification). See residual_income.py's module docstring.
    if wacc_estimate is not None and wacc_estimate.cost_of_equity is not None:
        cost_of_equity_for_rim = wacc_estimate.cost_of_equity
    else:
        cost_of_equity_for_rim = assumptions.discount_rate
        warnings.append(
            "residual_income_estimate uses assumptions.discount_rate as a cost-of-equity "
            "proxy (compute_wacc not requested, or CAPM cost of equity unavailable) - "
            "not a real per-company cost of equity"
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

    h_model_estimate = None
    try:
        h_model_estimate = run_h_model_estimate(statements, assumptions)
    except UnsupportedValuationError as exc:
        warnings.append(f"H-Model DCF unavailable: {exc}")

    residual_income_estimate = None
    try:
        residual_income_estimate = run_residual_income_estimate(
            statements, assumptions, cost_of_equity_for_rim
        )
    except UnsupportedValuationError as exc:
        warnings.append(f"Residual Income valuation unavailable: {exc}")

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
    if h_model_estimate is not None and None not in (
        h_model_estimate.value_per_share_low,
        h_model_estimate.value_per_share_high,
    ):
        value_ranges.append(
            ValueRange(
                method="DCF (H-Model)",
                low=h_model_estimate.value_per_share_low,
                high=h_model_estimate.value_per_share_high,
            )
        )
    if residual_income_estimate is not None and None not in (
        residual_income_estimate.value_per_share_low,
        residual_income_estimate.value_per_share_high,
    ):
        value_ranges.append(
            ValueRange(
                method="Residual Income",
                low=residual_income_estimate.value_per_share_low,
                high=residual_income_estimate.value_per_share_high,
            )
        )
    valuation_consensus = compute_consensus(value_ranges)

    sources = [
        f"https://data.sec.gov/submissions/CIK{cik}.json",
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
    ]

    # Resolved once for both qualitative sources below, so a request with
    # a 10-K and an earnings call reads the log once, not twice.
    log_path = decision_log_path or get_settings().resolved_decision_log_path
    track_record = None
    if include_track_record:
        past_records, log_warnings = read_records(log_path)
        warnings.extend(log_warnings)
        lessons = recent_lessons(past_records, ticker=ticker, limit=TRACK_RECORD_LESSON_LIMIT)
        if lessons:
            track_record = format_track_record(lessons)
            warnings.append(
                f"qualitative prompts include {len(lessons)} past lesson(s) from the "
                "decision log (include_track_record) - these affect only the LLM's "
                "risk weighting, never the computed valuation numbers"
            )
        else:
            warnings.append(
                "include_track_record requested but the decision log has no reflections "
                f"for {ticker.upper()} yet - prompts sent unchanged"
            )

    qualitative_analyses: list[QualitativeRiskAnalysis] = []
    sentiment_analyses: list[SentimentSummary] = []
    if analyze_10k:
        filings = [f for f in list_recent_filings(submissions) if f.form == "10-K"]
        if filings:
            document = fetch_filing_document(client, cik, filings[0])
            new_analyses, cross_warnings = _run_qualitative_extraction(
                anthropic_client,
                document.text,
                "10-K",
                document.accession_number,
                cross_validate,
                track_record=track_record,
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
            track_record=track_record,
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

    result = AnalysisResult(
        company=company,
        financials=statements,
        metrics=metrics,
        margin_of_safety=margin_of_safety,
        unsupported_reason=unsupported_reason,
        fundamental_growth_estimate=fundamental_growth_estimate,
        wacc_estimate=wacc_estimate,
        comps_estimate=comps_estimate,
        owner_earnings_estimate=owner_earnings_estimate,
        h_model_estimate=h_model_estimate,
        residual_income_estimate=residual_income_estimate,
        valuation_consensus=valuation_consensus,
        qualitative_analyses=qualitative_analyses,
        sentiment_analyses=sentiment_analyses,
        sources=sources,
        warnings=warnings,
    )

    if log_decision:
        # Last thing before returning, so the log only ever records
        # analyses that actually completed. A disk failure here must not
        # cost the caller a result they already paid SEC/LLM calls for -
        # it degrades to a warning on the result, same as every other
        # non-essential step in this function.
        try:
            append_record(
                build_decision_record(
                    result,
                    ticker=ticker,
                    as_of_date=as_of_date,
                    market_price=market_price,
                    assumptions=assumptions,
                    used_wacc_as_discount_rate=use_wacc_as_discount_rate,
                ),
                log_path,
            )
        except OSError as exc:
            result.warnings.append(f"could not append to the decision log at {log_path}: {exc}")

    return result
