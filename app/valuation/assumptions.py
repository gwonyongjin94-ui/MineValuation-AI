"""All numbers a DCF run depends on, explicit and never hardcoded elsewhere.

tax_rate is a direct input, not auto-derived. Deriving an effective or
marginal rate from filings would need income_tax_expense and
pretax_income, neither of which is normalized yet (Phase 1.5 never
spiked those tags) - guessing at it here would repeat the mistake this
project has avoided so far. Pass the rate you want (e.g. the statutory
21% or your own read of the company's effective rate) explicitly.
"""

from enum import Enum

from pydantic import BaseModel, model_validator


class BaseFCFMethod(str, Enum):
    LATEST_YEAR = "latest_year"
    THREE_YEAR_AVG = "3yr_avg"
    FIVE_YEAR_AVG = "5yr_avg"


class ValuationAssumptions(BaseModel):
    fcff_growth_rate: float
    discount_rate: float
    terminal_growth_rate: float
    tax_rate: float
    forecast_years: int = 5
    base_fcf_method: BaseFCFMethod = BaseFCFMethod.THREE_YEAR_AVG

    @model_validator(mode="after")
    def _terminal_growth_below_discount_rate(self) -> "ValuationAssumptions":
        if self.terminal_growth_rate >= self.discount_rate:
            raise ValueError("terminal_growth_rate must be less than discount_rate")
        return self
