from app.data.models import CompanyInfo, FinancialFact, FinancialStatement, ValuationCategory

DEFAULT_COMPANY = CompanyInfo(
    cik="0000320193",
    ticker="TST",
    name="Test Co",
    sic="3571",
    sic_description="Electronic Computers",
    valuation_category=ValuationCategory.STANDARD,
)


def make_fact(metric: str, value: float, period_end: str, fiscal_year: int) -> FinancialFact:
    return FinancialFact(
        metric=metric,
        value=value,
        unit="USD",
        taxonomy="us-gaap",
        xbrl_tag="SomeTag",
        period_start=f"{fiscal_year - 1}-01-01",
        period_end=period_end,
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        form="10-K",
        filed_date=f"{fiscal_year + 1}-02-01",
        accession_number=f"ACCN-{fiscal_year}",
    )


def make_statement(
    fiscal_year: int,
    period_end: str,
    *,
    company: CompanyInfo = DEFAULT_COMPANY,
    **metric_values: float | None,
) -> FinancialStatement:
    fields = {
        metric: make_fact(metric, value, period_end, fiscal_year) if value is not None else None
        for metric, value in metric_values.items()
    }
    return FinancialStatement(
        company=company,
        fiscal_year=fiscal_year,
        period_end=period_end,
        **fields,
    )
