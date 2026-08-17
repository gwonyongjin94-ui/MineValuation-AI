import pytest

from app.config import get_settings
from app.data.sec_client import SECClient

pytestmark = pytest.mark.integration


def test_get_company_facts_real_aapl():
    settings = get_settings()
    client = SECClient(user_agent=settings.sec_user_agent)
    try:
        facts = client.get_company_facts("0000320193")
    finally:
        client.close()

    assert facts["cik"] == 320193
    assert "us-gaap" in facts["facts"]
