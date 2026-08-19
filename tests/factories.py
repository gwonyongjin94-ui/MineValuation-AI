import json
from types import SimpleNamespace

import httpx

from app.data.models import CompanyInfo, FinancialFact, FinancialStatement, ValuationCategory
from app.data.sec_client import SECClient
from app.data.ticker_map import TickerMap

DEFAULT_COMPANY = CompanyInfo(
    cik="0000320193",
    ticker="TST",
    name="Test Co",
    sic="3571",
    sic_description="Electronic Computers",
    valuation_category=ValuationCategory.STANDARD,
)


def make_fact(
    metric: str,
    value: float,
    period_end: str,
    fiscal_year: int,
    *,
    filed_date: str | None = None,
    form: str = "10-K",
    accession_number: str | None = None,
    xbrl_tag: str = "SomeTag",
) -> FinancialFact:
    return FinancialFact(
        metric=metric,
        value=value,
        unit="USD",
        taxonomy="us-gaap",
        xbrl_tag=xbrl_tag,
        period_start=f"{fiscal_year - 1}-01-01",
        period_end=period_end,
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        form=form,
        filed_date=filed_date or f"{fiscal_year + 1}-02-01",
        accession_number=accession_number or f"ACCN-{fiscal_year}",
    )


def make_statement(
    fiscal_year: int,
    period_end: str,
    *,
    company: CompanyInfo = DEFAULT_COMPANY,
    restated_facts: list | None = None,
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
        restated_facts=restated_facts or [],
        **fields,
    )


def sec_entry(val, end, fy, fp="FY", form="10-K", filed="2020-01-01", accn="X-1", start=None):
    entry = {"val": val, "end": end, "fy": fy, "fp": fp, "form": form, "filed": filed, "accn": accn}
    if start is not None:
        entry["start"] = start
    return entry


def sec_facts(tag_entries: dict, taxonomy: str = "us-gaap", unit: str = "USD") -> dict:
    return {
        "facts": {
            taxonomy: {tag: {"units": {unit: entries}} for tag, entries in tag_entries.items()}
        }
    }


def build_mock_sec_client(
    submissions: dict, company_facts: dict, document_html: str | None = None
) -> SECClient:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "submissions" in url:
            return httpx.Response(200, json=submissions)
        if "companyfacts" in url:
            return httpx.Response(200, json=company_facts)
        return httpx.Response(200, text=document_html or "<html><body>test filing</body></html>")

    transport = httpx.MockTransport(handler)
    return SECClient(user_agent="test", min_interval=0, client=httpx.Client(transport=transport))


def build_ticker_map_with_cache(tmp_path, mapping: dict) -> TickerMap:
    cache_path = tmp_path / "company_tickers.json"
    entries = {
        str(i): {"cik_str": cik, "ticker": ticker, "title": ticker}
        for i, (ticker, cik) in enumerate(mapping.items())
    }
    cache_path.write_text(json.dumps(entries))
    return TickerMap(user_agent="test", cache_path=cache_path)


STANDARD_SUBMISSIONS = {
    "cik": 999999,
    "tickers": ["TSTX"],
    "name": "Test Standard Co",
    "sic": "3571",
    "sicDescription": "Electronic Computers",
    "filings": {
        "recent": {
            "form": ["10-K"],
            "accessionNumber": ["0000999999-25-000001"],
            "filingDate": ["2025-02-01"],
            "primaryDocument": ["tstx-10k.htm"],
        }
    },
}

BANK_SUBMISSIONS = {
    "cik": 888888,
    "tickers": ["TBNK"],
    "name": "Test Bank Co",
    "sic": "6021",
    "sicDescription": "National Commercial Banks",
}


def standard_company_facts() -> dict:
    """Two fiscal years with every metric needed for a full FCFF/DCF run."""
    years = [
        (2023, "2023-12-31", "2024-02-01", "A-2023", 5000, 700, 1000, 900, 100, 150, 500, 200, 50,
         20, 300, 2000),
        (2024, "2024-12-31", "2025-02-01", "A-2024", 5500, 850, 1200, 1000, 120, 180, 600, 250, 80,
         30, 320, 2200),
    ]
    tags = {
        "Revenues": [],
        "NetIncomeLoss": [],
        "OperatingIncomeLoss": [],
        "NetCashProvidedByUsedInOperatingActivities": [],
        "Depreciation": [],
        "PaymentsToAcquirePropertyPlantAndEquipment": [],
        "AssetsCurrent": [],
        "LiabilitiesCurrent": [],
        "CashAndCashEquivalentsAtCarryingValue": [],
        "DebtCurrent": [],
        "LongTermDebtNoncurrent": [],
        "CommonStockSharesOutstanding": [],
        "StockholdersEquity": [],
    }
    for (fy, end, filed, accn, revenue, net_income, op_income, ocf, dep, capex, cur_assets,
         cur_liab, cash, debt_cur, debt_lt, equity) in years:
        start = f"{fy}-01-01"
        tags["Revenues"].append(sec_entry(revenue, end, fy, filed=filed, accn=accn, start=start))
        tags["NetIncomeLoss"].append(
            sec_entry(net_income, end, fy, filed=filed, accn=accn, start=start)
        )
        tags["OperatingIncomeLoss"].append(
            sec_entry(op_income, end, fy, filed=filed, accn=accn, start=start)
        )
        tags["NetCashProvidedByUsedInOperatingActivities"].append(
            sec_entry(ocf, end, fy, filed=filed, accn=accn, start=start)
        )
        tags["Depreciation"].append(sec_entry(dep, end, fy, filed=filed, accn=accn, start=start))
        tags["PaymentsToAcquirePropertyPlantAndEquipment"].append(
            sec_entry(capex, end, fy, filed=filed, accn=accn, start=start)
        )
        tags["AssetsCurrent"].append(sec_entry(cur_assets, end, fy, filed=filed, accn=accn))
        tags["LiabilitiesCurrent"].append(sec_entry(cur_liab, end, fy, filed=filed, accn=accn))
        tags["CashAndCashEquivalentsAtCarryingValue"].append(
            sec_entry(cash, end, fy, filed=filed, accn=accn)
        )
        tags["DebtCurrent"].append(sec_entry(debt_cur, end, fy, filed=filed, accn=accn))
        tags["LongTermDebtNoncurrent"].append(sec_entry(debt_lt, end, fy, filed=filed, accn=accn))
        tags["CommonStockSharesOutstanding"].append(sec_entry(1000, end, fy, filed=filed, accn=accn))
        tags["StockholdersEquity"].append(sec_entry(equity, end, fy, filed=filed, accn=accn))

    return {"cik": 999999, "entityName": "Test Standard Co", **sec_facts(tags)}


def bank_company_facts() -> dict:
    tags = {
        "Revenues": [
            sec_entry(9000, "2024-12-31", 2024, filed="2025-02-14", accn="B-2024",
                      start="2024-01-01"),
        ],
        "NetIncomeLoss": [
            sec_entry(2000, "2024-12-31", 2024, filed="2025-02-14", accn="B-2024",
                      start="2024-01-01"),
        ],
    }
    return {"cik": 888888, "entityName": "Test Bank Co", **sec_facts(tags)}


_RISK_DEFAULTS = {"supporting_quote": "the text says so", "grounding": "explicit"}


def fake_anthropic_client(
    risks: list | None = None,
    summary: str = "",
    no_tool_use: bool = False,
    truncated: bool = False,
):
    """A stand-in for anthropic.Anthropic matching only what extract_risks() calls.

    Each risk dict only needs the fields a given test cares about -
    supporting_quote/grounding are filled in with defaults when omitted,
    since most tests exercise severity/status logic, not those two fields.
    truncated=True simulates stop_reason=="max_tokens" (a real failure mode
    hit live with claude-sonnet-5 on a real 10-K before MAX_TOKENS was
    raised - see risk_extraction.py).
    """
    filled_risks = [{**_RISK_DEFAULTS, **risk} for risk in (risks or [])]
    content = []
    if not no_tool_use:
        content.append(
            SimpleNamespace(type="tool_use", input={"risks": filled_risks, "summary": summary})
        )
    response = SimpleNamespace(
        content=content,
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        stop_reason="max_tokens" if truncated else "tool_use",
    )

    class FakeMessages:
        def create(self, **kwargs):
            return response

    return SimpleNamespace(messages=FakeMessages())


def fake_anthropic_client_by_model(
    risks_by_model: dict, summary: str = "", truncated_models: frozenset = frozenset()
):
    """Like fake_anthropic_client, but returns different risks per requested
    model - for testing run_cross_model_extraction(), which calls the same
    client with different `model=` values. truncated_models simulates one
    model in a cross-model run hitting max_tokens while the other succeeds.
    """

    def response_for(model: str):
        if model in truncated_models:
            return SimpleNamespace(
                content=[SimpleNamespace(type="tool_use", input={})],
                usage=SimpleNamespace(input_tokens=100, output_tokens=50),
                stop_reason="max_tokens",
            )
        filled_risks = [{**_RISK_DEFAULTS, **risk} for risk in risks_by_model.get(model, [])]
        content = [
            SimpleNamespace(type="tool_use", input={"risks": filled_risks, "summary": summary})
        ]
        return SimpleNamespace(
            content=content,
            usage=SimpleNamespace(input_tokens=100, output_tokens=50),
            stop_reason="tool_use",
        )

    class FakeMessages:
        def create(self, **kwargs):
            return response_for(kwargs["model"])

    return SimpleNamespace(messages=FakeMessages())


def fake_sentiment_classifier(labels_and_scores: list):
    """A stand-in for the transformers pipeline object score_sentiment() calls."""

    def classify(sentences, **kwargs):
        return [{"label": label, "score": score} for label, score in labels_and_scores]

    return classify
