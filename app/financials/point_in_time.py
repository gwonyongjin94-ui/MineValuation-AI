"""What was actually knowable about a company on a given date.

Two separate things, easy to conflate, both required:

- `filed_date <= as_of_date` - never use a number that wasn't public
  yet (the look-ahead-bias principle from docs/DATA_SPIKE_NOTES.md
  finding #3).
- Among everything that WAS public by then (the as-reported fact and
  any restatements filed before it), use the latest. A 10-K/A
  restatement is public the moment it's filed, so an "as of" valuation
  after that date should reflect it, not the superseded original.

This lived inside margin_of_safety.py until a backtest needed it. That
placement quietly meant only margin_of_safety was point-in-time
correct: Owner Earnings, H-Model, Residual Income, Comps, WACC and the
growth estimate all received the unfiltered statement list. Harmless
whenever as_of_date is today (everything filed is public), and
catastrophic for a historical run - proven on real data with
as_of_date=2020-01-01, where margin_of_safety used a base FCFF of
$48.2B (FY2017-19, correct) while the H-Model used $101.5B (FY2023-25,
six years of future data).

Filtering is idempotent: running it twice excludes nothing the second
time, so analyze() can filter once up front and compute_margin_of_safety()
can keep filtering defensively for callers that reach it directly.
"""

from datetime import date

from app.data.models import FinancialFact, FinancialStatement

# Every FinancialStatement field holding a FinancialFact. Listed rather
# than derived from the model so adding a metric to FinancialStatement
# without resolving it here is a visible omission instead of a silent
# look-ahead hole.
FACT_FIELDS = (
    "revenue",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "depreciation_amortization",
    "capex",
    "current_assets",
    "current_liabilities",
    "cash",
    "short_term_debt",
    "long_term_debt",
    "stockholders_equity",
    "shares_outstanding",
    "interest_expense",
)


def resolve_fact_as_of(
    as_reported: FinancialFact | None,
    restatements: list[FinancialFact],
    as_of_date: date,
) -> FinancialFact | None:
    candidates = list(restatements)
    if as_reported is not None:
        candidates.append(as_reported)
    known = [fact for fact in candidates if fact.filed_date <= as_of_date]
    if not known:
        return None
    return max(known, key=lambda fact: fact.filed_date)


def resolve_statement_as_of(
    statement: FinancialStatement, as_of_date: date
) -> FinancialStatement | None:
    """The statement as it was knowable on `as_of_date`, or None when no
    field of it was public yet.
    """
    restated_by_metric: dict[str, list[FinancialFact]] = {}
    for fact in statement.restated_facts:
        restated_by_metric.setdefault(fact.metric, []).append(fact)

    resolved = {
        field_name: resolve_fact_as_of(
            getattr(statement, field_name), restated_by_metric.get(field_name, []), as_of_date
        )
        for field_name in FACT_FIELDS
    }

    if all(fact is None for fact in resolved.values()):
        return None

    return statement.model_copy(update=resolved)


def filter_look_ahead(
    statements: list[FinancialStatement], as_of_date: date
) -> tuple[list[FinancialStatement], int]:
    """(statements knowable on as_of_date, how many were dropped)."""
    eligible = []
    excluded = 0
    for statement in statements:
        resolved = resolve_statement_as_of(statement, as_of_date)
        if resolved is None:
            excluded += 1
            continue
        eligible.append(resolved)
    return eligible, excluded
