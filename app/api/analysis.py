from collections.abc import Iterator
from datetime import date

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.data.exceptions import SECClientError, UnknownTickerError
from app.data.models import CompanyInfo, FinancialStatement
from app.data.sec_client import SECClient, build_default_client
from app.data.ticker_map import TickerMap, build_default_ticker_map
from app.financials.metrics import YearMetrics
from app.qualitative.risk_extraction import QualitativeAnalysisError, QualitativeRiskAnalysis
from app.services.analysis_service import analyze
from app.valuation.assumptions import BaseFCFMethod, ValuationAssumptions
from app.valuation.margin_of_safety import MarginOfSafetyResult

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


class AnalyzeResponse(BaseModel):
    company: CompanyInfo
    financials: list[FinancialStatement]
    metrics: list[YearMetrics]
    margin_of_safety: MarginOfSafetyResult | None
    unsupported_reason: str | None
    qualitative_analyses: list[QualitativeRiskAnalysis]
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
) -> AnalyzeResponse:
    assumptions = _resolve_assumptions(request.assumptions)
    as_of_date = request.as_of_date or date.today()  # noqa: DTZ011 - calendar date default is fine

    if (request.analyze_10k or request.earnings_call_text) and anthropic_client is None:
        raise HTTPException(
            status_code=503,
            detail="analyze_10k/earnings_call_text requested but ANTHROPIC_API_KEY is not "
            "configured on this server",
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
        qualitative_analyses=result.qualitative_analyses,
        assumptions=assumptions,
        sources=result.sources,
        warnings=result.warnings,
    )
