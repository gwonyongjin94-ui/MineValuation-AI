from collections.abc import Iterator
from datetime import date

import anthropic
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr

from app.config import get_settings
from app.data.exceptions import SECClientError, UnknownTickerError
from app.data.market_data import build_default_market_data_client
from app.data.models import CompanyInfo, FinancialStatement
from app.data.sec_client import SECClient, build_default_client
from app.data.ticker_map import TickerMap, build_default_ticker_map
from app.financials.metrics import YearMetrics
from app.qualitative.risk_extraction import QualitativeAnalysisError, QualitativeRiskAnalysis
from app.qualitative.sentiment import SentimentSummary
from app.qualitative.sentiment import is_available as sentiment_is_available
from app.services.analysis_service import analyze
from app.valuation.assumptions import BaseFCFMethod, ValuationAssumptions
from app.valuation.comps import CompsEstimate
from app.valuation.consensus import ValuationConsensus
from app.valuation.growth import FundamentalGrowthEstimate
from app.valuation.margin_of_safety import MarginOfSafetyResult
from app.valuation.owner_earnings import OwnerEarningsDCFResult
from app.valuation.wacc import WACCEstimate

router = APIRouter()

# Documented, overridable defaults - never used inside the DCF/FCFF math
# itself (that always takes assumptions as explicit parameters). Every
# response echoes back whichever assumptions actually produced it.
DEFAULT_ASSUMPTIONS = ValuationAssumptions(
    fcff_growth_rate=0.05,
    discount_rate=0.09,
    terminal_growth_rate=0.025,
    tax_rate=0.21,
)

_ERROR_STATUS: dict[type[Exception], int] = {
    UnknownTickerError: 404,
    QualitativeAnalysisError: 502,
}
_DEFAULT_SEC_ERROR_STATUS = 502


def get_sec_client() -> Iterator[SECClient]:
    client = build_default_client()
    try:
        yield client
    finally:
        client.close()


def get_ticker_map() -> TickerMap:
    return build_default_ticker_map()


def get_anthropic_client() -> anthropic.Anthropic | None:
    api_key = get_settings().anthropic_api_key
    return anthropic.Anthropic(api_key=api_key) if api_key else None


def get_sentiment_classifier():
    # None -> score_sentiment() lazily loads the real FinBERT pipeline.
    # Exists as a dependency (rather than passing None inline) so tests
    # can override it with a fake classifier without importing transformers.
    return None


def get_market_data_client() -> Iterator[httpx.Client]:
    client = build_default_market_data_client()
    try:
        yield client
    finally:
        client.close()


class AssumptionsRequest(BaseModel):
    fcff_growth_rate: float | None = None
    discount_rate: float | None = None
    terminal_growth_rate: float | None = None
    tax_rate: float | None = None
    forecast_years: int | None = None
    base_fcf_method: BaseFCFMethod | None = None


class AnalyzeRequest(BaseModel):
    ticker: str
    market_price: float = Field(gt=0)
    as_of_date: date | None = None
    assumptions: AssumptionsRequest | None = None
    # Qualitative analysis is opt-in and costs real money per LLM call, so
    # it never runs unless explicitly requested. Earnings calls aren't SEC
    # data and V2 has no automated free source for them - the caller
    # pastes the transcript text directly (see docs/DATA_SPIKE_NOTES.md's
    # V2 section for why this was chosen over a paid third-party API).
    analyze_10k: bool = False
    earnings_call_text: str | None = None
    # Free and local (no per-call cost, unlike the LLM), but needs the
    # optional [sentiment] extra (transformers/torch, ~1GB) installed on
    # the server - opt-in rather than automatic so a server without it
    # doesn't silently fail whenever analyze_10k/earnings_call_text is used.
    include_sentiment: bool = False
    # Runs extraction on two Claude tiers instead of one and flags
    # disagreement - doubles the LLM cost, so opt-in. Confirmed live that
    # claude-sonnet-5 can fail (degenerate output) on our real ~60-70k-token
    # filing sizes while claude-haiku-4-5 succeeds - the result degrades to
    # whichever model(s) succeeded rather than failing the whole request;
    # see docs/DATA_SPIKE_NOTES.md's V2 section.
    cross_validate: bool = False
    # Lets a caller supply their own Anthropic key for this one request
    # instead of relying on the server's ANTHROPIC_API_KEY - e.g. a
    # multi-tenant deployment where each caller pays for their own LLM
    # usage. SecretStr keeps it out of repr()/model_dump()/exception text
    # by construction, so an accidental future `log.info(request)` or
    # error dump can't leak it. It is used only to build an Anthropic
    # client scoped to this single request (see analyze_ticker() below);
    # nothing about the request - this key, earnings_call_text, or any
    # other field - is written to a database, a file, or a log anywhere
    # in this app, so it (and any personal information a caller pastes
    # into earnings_call_text) does not outlive the request/response
    # cycle it was submitted in.
    anthropic_api_key: SecretStr | None = None
    # Estimates discount_rate (WACC) per-company from real market data
    # (Damodaran's bottom-up industry beta + synthetic-rating cost of
    # debt, plus a live-fetched 10-year Treasury yield - see
    # app/valuation/wacc.py) instead of the same flat discount_rate for
    # every ticker. Adds one extra request to FRED, so opt-in - and, like
    # fundamental_growth_estimate, this is a reference figure only: it is
    # never substituted for assumptions.discount_rate.
    compute_wacc: bool = False
    # Comparable-company analysis: peer trading multiples (EV/EBITDA,
    # EV/Revenue, P/E) from a curated per-industry peer list (same
    # SIC-bucket approach as compute_wacc's beta table - see
    # app/valuation/comps.py), applied to this company's own metrics.
    # Adds one request per peer to both SEC EDGAR and Yahoo Finance, so
    # opt-in - and, like fundamental_growth_estimate/wacc_estimate, this
    # is a reference figure only, never merged into margin_of_safety.
    compute_comps: bool = False


class AnalyzeResponse(BaseModel):
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
    assumptions: ValuationAssumptions
    sources: list[str]
    warnings: list[str]


def _resolve_assumptions(overrides: AssumptionsRequest | None) -> ValuationAssumptions:
    merged = DEFAULT_ASSUMPTIONS.model_dump()
    if overrides is not None:
        merged.update(overrides.model_dump(exclude_none=True))
    try:
        return ValuationAssumptions(**merged)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/v1/analyze", response_model=AnalyzeResponse)
def analyze_ticker(
    request: AnalyzeRequest,
    client: SECClient = Depends(get_sec_client),
    ticker_map: TickerMap = Depends(get_ticker_map),
    anthropic_client: anthropic.Anthropic | None = Depends(get_anthropic_client),
    sentiment_classifier=Depends(get_sentiment_classifier),
    market_data_client: httpx.Client = Depends(get_market_data_client),
) -> AnalyzeResponse:
    assumptions = _resolve_assumptions(request.assumptions)
    as_of_date = request.as_of_date or date.today()  # noqa: DTZ011 - calendar date default is fine

    # A per-request key takes priority over the server-configured one.
    # Built fresh here, used only for this call, and held by this local
    # variable alone - it goes out of scope (and is garbage-collected)
    # the moment this function returns, with nothing else in the app
    # ever storing or logging it.
    if request.anthropic_api_key is not None:
        anthropic_client = anthropic.Anthropic(
            api_key=request.anthropic_api_key.get_secret_value()
        )

    if (request.analyze_10k or request.earnings_call_text) and anthropic_client is None:
        raise HTTPException(
            status_code=503,
            detail="analyze_10k/earnings_call_text requested but ANTHROPIC_API_KEY is not "
            "configured on this server",
        )
    if request.include_sentiment and not sentiment_is_available():
        raise HTTPException(
            status_code=503,
            detail="include_sentiment requested but the [sentiment] extra "
            "(transformers/torch) is not installed on this server",
        )

    try:
        result = analyze(
            ticker=request.ticker,
            market_price=request.market_price,
            as_of_date=as_of_date,
            assumptions=assumptions,
            client=client,
            ticker_map=ticker_map,
            analyze_10k=request.analyze_10k,
            earnings_call_text=request.earnings_call_text,
            anthropic_client=anthropic_client,
            include_sentiment=request.include_sentiment,
            sentiment_classifier=sentiment_classifier,
            cross_validate=request.cross_validate,
            compute_wacc=request.compute_wacc,
            compute_comps=request.compute_comps,
            market_data_client=market_data_client,
        )
    except SECClientError as exc:
        status_code = _ERROR_STATUS.get(type(exc), _DEFAULT_SEC_ERROR_STATUS)
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except QualitativeAnalysisError as exc:
        status_code = _ERROR_STATUS.get(type(exc), 502)
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    return AnalyzeResponse(
        company=result.company,
        financials=result.financials,
        metrics=result.metrics,
        margin_of_safety=result.margin_of_safety,
        unsupported_reason=result.unsupported_reason,
        fundamental_growth_estimate=result.fundamental_growth_estimate,
        wacc_estimate=result.wacc_estimate,
        comps_estimate=result.comps_estimate,
        owner_earnings_estimate=result.owner_earnings_estimate,
        valuation_consensus=result.valuation_consensus,
        qualitative_analyses=result.qualitative_analyses,
        sentiment_analyses=result.sentiment_analyses,
        assumptions=assumptions,
        sources=result.sources,
        warnings=result.warnings,
    )
