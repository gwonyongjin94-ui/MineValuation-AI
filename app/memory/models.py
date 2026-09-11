"""Schema for the decision log - an append-only record of what this
system concluded, what actually happened, and what it learned.

Three record kinds share one JSONL stream, discriminated by `kind`:

    decision  -> what we concluded, when, at what price
    outcome   -> what the price actually did by some horizon
    reflection-> an LLM's written lesson from that gap

Append-only in the event-sourced sense: resolving an outcome never
rewrites the decision line it refers to, it appends a new line keyed by
the same `decision_id`. Reading is replay-and-join, so the file is
always a complete audit trail of what was known when - the same
property `restated_facts` gives the financial data (see
margin_of_safety.py's as-of-date handling) applied to our own
conclusions.

What is deliberately NOT stored, per this project's standing promise
that request text doesn't outlive the request (see the API's
`anthropic_api_key`/`earnings_call_text` note in app/api/analysis.py):
qualitative risks are reduced to counts by severity - never a risk
label, never a supporting quote, never any part of `earnings_call_text`
or the 10-K body. A decision record is a numbers-and-metadata artifact,
not a copy of the source material it was formed from.
"""

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel

from app.valuation.assumptions import ValuationAssumptions


class RecordKind(str, Enum):
    DECISION = "decision"
    OUTCOME = "outcome"
    REFLECTION = "reflection"


class Stance(str, Enum):
    """What the numbers implied at decision time, not a recommendation.

    Derived from margin_of_safety's sign: a positive MOS means the model
    put intrinsic value above market price (UNDERVALUED), negative means
    below (OVERVALUED). UNSUPPORTED covers the cases this project
    already refuses to value - banks, missing FCFF inputs, no
    shares_outstanding - so they can be logged and counted without
    pretending a call was made.
    """

    UNDERVALUED = "undervalued"
    OVERVALUED = "overvalued"
    UNSUPPORTED = "unsupported"


class DecisionRecord(BaseModel):
    kind: RecordKind = RecordKind.DECISION
    decision_id: str
    logged_at: datetime
    ticker: str
    as_of_date: date
    market_price: float
    valuation_category: str
    stance: Stance

    assumptions: ValuationAssumptions
    used_wacc_as_discount_rate: bool = False

    intrinsic_value_per_share: float | None = None
    intrinsic_value_low: float | None = None
    intrinsic_value_high: float | None = None
    margin_of_safety: float | None = None

    consensus_methods: list[str] = []
    consensus_low: float | None = None
    consensus_high: float | None = None

    # Counts only - see the module docstring on why no labels or quotes.
    qualitative_sources: list[str] = []
    qualitative_risk_counts: dict[str, int] = {}

    unsupported_reason: str | None = None
    warning_count: int = 0


class OutcomeRecord(BaseModel):
    kind: RecordKind = RecordKind.OUTCOME
    decision_id: str
    resolved_at: datetime
    horizon_days: int
    price_at_decision: float
    price_at_horizon: float
    price_date_at_horizon: date
    return_pct: float
    # Did the price move the way the stance implied? None when the
    # decision made no call (Stance.UNSUPPORTED) - an unsupported
    # valuation can't be right or wrong about direction.
    stance_was_directionally_right: bool | None = None


class ReflectionRecord(BaseModel):
    kind: RecordKind = RecordKind.REFLECTION
    decision_id: str
    created_at: datetime
    model: str
    lesson: str
    input_tokens: int = 0
    output_tokens: int = 0
