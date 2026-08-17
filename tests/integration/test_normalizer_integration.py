import pytest

from app.config import get_settings
from app.data.models import ValuationCategory
from app.data.sec_client import SECClient
from app.data.ticker_map import build_default_ticker_map
from app.financials.normalizer import normalize

pytestmark = pytest.mark.integration


def _normalize_ticker(ticker: str):
    settings = get_settings()
    tm = build_default_ticker_map()
    client = SECClient(user_agent=settings.sec_user_agent)
    try:
        cik = tm.resolve(ticker)
        submissions = client.get_submissions(cik)
        company_facts = client.get_company_facts(cik)
    finally:
        client.close()
    return normalize(company_facts, submissions)


def test_normalize_aapl_real_data():
    statements = _normalize_ticker("AAPL")

    assert statements
    latest = statements[-1]
    assert latest.company.valuation_category == ValuationCategory.STANDARD
    assert latest.revenue is not None
    assert latest.revenue.value > 0
    assert latest.net_income is not None


def test_normalize_jpm_real_data_is_financial_and_missing_fcff_inputs():
    statements = _normalize_ticker("JPM")

    assert statements
    latest = statements[-1]
    assert latest.company.valuation_category == ValuationCategory.FINANCIAL
    assert latest.operating_income is None
    assert latest.current_assets is None
    assert latest.capex is None
