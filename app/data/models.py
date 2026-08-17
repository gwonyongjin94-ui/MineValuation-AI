from datetime import date
from enum import Enum

from pydantic import BaseModel


class ValuationCategory(str, Enum):
    STANDARD = "standard"
    FINANCIAL = "financial"
    UNSUPPORTED = "unsupported"


class FinancialFact(BaseModel):
    """One selected XBRL fact, with enough provenance to trace it back to a filing.

    period_start is None for instant (balance-sheet) concepts and set for
    duration (income/cash-flow-statement) concepts.
    """

    metric: str
    value: float
    unit: str
    taxonomy: str
    xbrl_tag: str
    period_start: date | None
    period_end: date
    fiscal_year: int
    fiscal_period: str
    form: str
    filed_date: date
    accession_number: str
    frame: str | None = None
    source: str = "SEC"


class CompanyInfo(BaseModel):
    cik: str
    ticker: str
    name: str
    sic: str | None
    sic_description: str | None
    valuation_category: ValuationCategory


class FinancialStatement(BaseModel):
    """Normalized annual financials for one company/fiscal year.

    Each metric holds the as-reported fact (earliest filed_date for that
    period) chosen by the normalizer. A missing metric means no standard
    taxonomy tag was found for that period - it is left None and explained
    in `warnings`, never silently defaulted to 0.
    """

    company: CompanyInfo
    fiscal_year: int
    period_end: date

    revenue: FinancialFact | None = None
    operating_income: FinancialFact | None = None
    net_income: FinancialFact | None = None
    operating_cash_flow: FinancialFact | None = None
    depreciation_amortization: FinancialFact | None = None
    capex: FinancialFact | None = None
    current_assets: FinancialFact | None = None
    current_liabilities: FinancialFact | None = None
    cash: FinancialFact | None = None
    short_term_debt: FinancialFact | None = None
    long_term_debt: FinancialFact | None = None
    shares_outstanding: FinancialFact | None = None
    stockholders_equity: FinancialFact | None = None

    # Facts for the same period found under a later accession number with a
    # different value (e.g. a 10-K/A). Never auto-substituted for the
    # as-reported fact above - surfaced separately so callers can choose.
    restated_facts: list[FinancialFact] = []

    warnings: list[str] = []
