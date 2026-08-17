from app.data.models import (
    CompanyInfo,
    FinancialFact,
    FinancialStatement,
    ValuationCategory,
)


def _fact(**overrides) -> FinancialFact:
    base = {
        "metric": "revenue",
        "value": 391035000000,
        "unit": "USD",
        "taxonomy": "us-gaap",
        "xbrl_tag": "RevenueFromContractWithCustomerExcludingAssessedTax",
        "period_start": "2023-10-01",
        "period_end": "2024-09-28",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "form": "10-K",
        "filed_date": "2024-11-01",
        "accession_number": "0000320193-24-000123",
    }
    base.update(overrides)
    return FinancialFact(**base)


def test_standard_company_statement_round_trips():
    company = CompanyInfo(
        cik="0000320193",
        ticker="AAPL",
        name="Apple Inc.",
        sic="3571",
        sic_description="Electronic Computers",
        valuation_category=ValuationCategory.STANDARD,
    )
    statement = FinancialStatement(
        company=company,
        fiscal_year=2024,
        period_end="2024-09-28",
        revenue=_fact(),
        operating_income=_fact(
            metric="operating_income", value=123216000000, xbrl_tag="OperatingIncomeLoss"
        ),
        capex=_fact(
            metric="capex",
            value=9447000000,
            xbrl_tag="PaymentsToAcquirePropertyPlantAndEquipment",
        ),
    )

    assert statement.revenue.value == 391035000000
    assert statement.net_income is None
    assert statement.warnings == []


def test_financial_company_has_mostly_missing_metrics():
    company = CompanyInfo(
        cik="0000019617",
        ticker="JPM",
        name="JPMORGAN CHASE & CO",
        sic="6021",
        sic_description="National Commercial Banks",
        valuation_category=ValuationCategory.FINANCIAL,
    )
    statement = FinancialStatement(
        company=company,
        fiscal_year=2024,
        period_end="2024-12-31",
        revenue=_fact(
            metric="revenue",
            value=177556000000,
            xbrl_tag="Revenues",
            fiscal_year=2024,
            filed_date="2025-02-14",
            accession_number="0000019617-25-000270",
        ),
        net_income=_fact(
            metric="net_income",
            value=58471000000,
            xbrl_tag="NetIncomeLoss",
            fiscal_year=2024,
            filed_date="2025-02-14",
            accession_number="0000019617-25-000270",
        ),
        warnings=["operating_income: no standard tag found (financial company)"],
    )

    assert statement.company.valuation_category == ValuationCategory.FINANCIAL
    assert statement.operating_income is None
    assert statement.current_assets is None
    assert statement.capex is None
    assert len(statement.warnings) == 1


def test_restated_fact_kept_separate_from_as_reported():
    company = CompanyInfo(
        cik="0001709164",
        ticker="HBB",
        name="Hamilton Beach Brands Holding Co",
        sic="3634",
        sic_description="Electric Housewares & Fans",
        valuation_category=ValuationCategory.STANDARD,
    )
    as_reported = _fact(
        metric="revenue",
        value=612843000,
        period_start="2019-01-01",
        period_end="2019-12-31",
        fiscal_year=2019,
        filed_date="2020-02-26",
        accession_number="0001709164-20-000007",
    )
    restated = _fact(
        metric="revenue",
        value=611786000,
        period_start="2019-01-01",
        period_end="2019-12-31",
        fiscal_year=2019,
        form="10-K/A",
        filed_date="2020-07-24",
        accession_number="0001709164-20-000032",
    )
    statement = FinancialStatement(
        company=company,
        fiscal_year=2019,
        period_end="2019-12-31",
        revenue=as_reported,
        restated_facts=[restated],
    )

    assert statement.revenue.value == 612843000
    assert statement.restated_facts[0].value == 611786000
    assert statement.restated_facts[0].form == "10-K/A"
