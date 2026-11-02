"""The two halves of what happens after a decision: resolving what the
price actually did, then asking an LLM what to learn from the gap.

resolve_outcome() is pure arithmetic over a price series - no network,
no LLM, fully testable. write_reflection() is the only part that costs
money, and nothing in the request path ever calls it: it runs from
scripts/resolve_decisions.py, so a normal /api/v1/analyze request can
never silently incur reflection cost. That mirrors how every other LLM
feature here is an explicit opt-in flag rather than an ambient cost.

A lesson is written to be reusable on a *different* company later, so
the prompt asks for calibration of this analyst's own judgment, not a
verdict on the ticker. "AAPL went up 12%" teaches nothing reusable;
"extreme negative margins of safety on mature large caps have been
tracking assumption error more than overvaluation" does.
"""

from datetime import date

from app.data.market_data import PricePoint, close_on_or_after
from app.memory.decision_log import now_utc
from app.memory.models import DecisionRecord, OutcomeRecord, ReflectionRecord, Stance

DEFAULT_HORIZON_DAYS = 90
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1024

# Ranges Yahoo's chart endpoint serves at daily granularity. "max" is
# deliberately absent: it switches to monthly closes without saying so,
# which understates every drawdown computed from it.
_DAILY_RANGES = ((1, "1y"), (2, "2y"), (5, "5y"), (10, "10y"))

_PROMPT = """You are reviewing one past equity analysis produced by an automated
valuation system, to write a calibration lesson for its future analyses.

What the system concluded on {as_of_date}:
- Ticker: {ticker} ({valuation_category})
- Market price at the time: ${market_price:,.2f}
- Stance implied by the numbers: {stance}
- Intrinsic value: {intrinsic_value} (range {intrinsic_low} - {intrinsic_high})
- Margin of safety: {margin_of_safety}
- Assumptions used: growth {growth:.1%}, discount {discount:.1%}, terminal {terminal:.1%}, tax {tax:.1%}
- Valuation methods that agreed enough to produce a consensus: {consensus_methods}
- Qualitative risk counts by severity: {risk_counts}

What actually happened:
- {horizon_days} days later ({price_date}), the price was ${price_at_horizon:,.2f}
- Return over the period: {return_pct:+.1%}
- Was the stance directionally right? {directionally_right}

Write ONE lesson, at most two sentences, that would make this system's FUTURE
analyses better calibrated - on any company, not just this one. Focus on the
relationship between its inputs (assumptions, method agreement, risk counts) and
the outcome. Do not give investment advice, do not predict this stock, and do not
state a lesson the single data point above cannot support - if one analysis is too
little to conclude anything, say that plainly and name what would need to be
tracked across more analyses to find out.

Reply with the lesson text only, no preamble."""


def _max_drawdown(window: list[PricePoint]) -> tuple[float, PricePoint] | None:
    """Worst peak-to-trough decline within `window`, and where it bottomed.

    Measured against the running high rather than against the entry
    price, which is the definition that matters for holding something:
    the pain is relative to what the position was worth at its best, not
    to what it cost. A window that only ever rises has a drawdown of
    0.0, which is a real answer - distinct from None, meaning not
    measurable.
    """
    if not window:
        return None
    peak = window[0].close
    worst_pct = 0.0
    worst_point = window[0]
    for point in window:
        peak = max(peak, point.close)
        if peak <= 0:
            continue
        decline = (point.close - peak) / peak
        if decline < worst_pct:
            worst_pct = decline
            worst_point = point
    return worst_pct, worst_point


def resolve_outcome(
    decision: DecisionRecord,
    history: list[PricePoint],
    horizon_days: int = DEFAULT_HORIZON_DAYS,
) -> OutcomeRecord | None:
    """Scores one decision against the price `horizon_days` after its
    as_of_date. Returns None when either end of the comparison isn't in
    the series - an unresolvable decision stays unresolved rather than
    being scored against whatever price happens to be nearest.
    """
    start = close_on_or_after(history, decision.as_of_date)
    target_date = date.fromordinal(decision.as_of_date.toordinal() + horizon_days)
    end = close_on_or_after(history, target_date)
    if start is None or end is None or start.close == 0:
        return None
    # Both endpoints snapping to the same close means the horizon didn't
    # elapse inside the series we were handed - typically a history that
    # starts after the decision date, so close_on_or_after() returned its
    # first point twice. Caught for real: a 600-cell backtest resolved
    # 360 cells to exactly 0.00% return because the resolver fetched a
    # default 2-year history for decisions up to 5 years old. Refusing is
    # the only honest answer; reporting a 0% return invents a fact, and
    # the decision stays unresolved so a later run with enough history
    # can score it properly.
    if end.date <= start.date:
        return None

    return_pct = (end.close - start.close) / start.close
    window = [p for p in history if start.date <= p.date <= end.date]
    drawdown = _max_drawdown(window)
    if decision.stance == Stance.UNDERVALUED:
        directionally_right = return_pct > 0
    elif decision.stance == Stance.OVERVALUED:
        directionally_right = return_pct < 0
    else:
        # No call was made (banks, missing inputs) - it can't be scored
        # right or wrong, and pretending otherwise would pollute the
        # calibration statistics the lessons are drawn from.
        directionally_right = None

    return OutcomeRecord(
        decision_id=decision.decision_id,
        resolved_at=now_utc(),
        horizon_days=horizon_days,
        price_at_decision=start.close,
        price_at_horizon=end.close,
        price_date_at_horizon=end.date,
        return_pct=return_pct,
        stance_was_directionally_right=directionally_right,
        max_drawdown_pct=drawdown[0] if drawdown else None,
        trough_close=drawdown[1].close if drawdown else None,
        trough_date=drawdown[1].date if drawdown else None,
        closes_observed=len(window),
    )


def history_range_for(oldest_as_of: date, today: date, horizon_days: int) -> str:
    """The shortest daily-granularity range that still covers a decision
    made on `oldest_as_of` plus its horizon.

    The resolver used to take the default 2-year range regardless of how
    old the pending decisions were, which silently scored everything
    older than that against a single price point.
    """
    span_days = (today - oldest_as_of).days + horizon_days
    years_needed = span_days / 365
    for years, label in _DAILY_RANGES:
        if years >= years_needed:
            return label
    return _DAILY_RANGES[-1][1]


def _format_optional(value: float | None, prefix: str = "$") -> str:
    return "not computable" if value is None else f"{prefix}{value:,.2f}"


def write_reflection(
    client,
    decision: DecisionRecord,
    outcome: OutcomeRecord,
    model: str = DEFAULT_MODEL,
) -> ReflectionRecord:
    mos = decision.margin_of_safety
    directionally_right = {
        True: "yes",
        False: "no",
        None: "not applicable - the system declined to value this company",
    }[outcome.stance_was_directionally_right]

    prompt = _PROMPT.format(
        as_of_date=decision.as_of_date.isoformat(),
        ticker=decision.ticker,
        valuation_category=decision.valuation_category,
        market_price=decision.market_price,
        stance=decision.stance.value,
        intrinsic_value=_format_optional(decision.intrinsic_value_per_share),
        intrinsic_low=_format_optional(decision.intrinsic_value_low),
        intrinsic_high=_format_optional(decision.intrinsic_value_high),
        margin_of_safety="not computable" if mos is None else f"{mos:+.1%}",
        growth=decision.assumptions.fcff_growth_rate,
        discount=decision.assumptions.discount_rate,
        terminal=decision.assumptions.terminal_growth_rate,
        tax=decision.assumptions.tax_rate,
        consensus_methods=", ".join(decision.consensus_methods) or "none",
        risk_counts=decision.qualitative_risk_counts or "none recorded",
        horizon_days=outcome.horizon_days,
        price_date=outcome.price_date_at_horizon.isoformat(),
        price_at_horizon=outcome.price_at_horizon,
        return_pct=outcome.return_pct,
        directionally_right=directionally_right,
    )

    response = client.messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    lesson = "".join(block.text for block in response.content if block.type == "text").strip()

    return ReflectionRecord(
        decision_id=decision.decision_id,
        created_at=now_utc(),
        model=model,
        lesson=lesson,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
