from app.data.models import ValuationCategory
from app.financials.normalizer import classify_company, normalize

SUBMISSIONS_STANDARD = {
    "tickers": ["TST"],
    "name": "Test Co",
    "sic": "3571",
    "sicDescription": "Electronic Computers",
}
SUBMISSIONS_BANK = {
    "tickers": ["BNK"],
    "name": "Test Bank",
    "sic": "6021",
    "sicDescription": "National Commercial Banks",
}


def _entry(val, end, fy, fp="FY", form="10-K", filed="2020-01-01", accn="X-1", start=None):
    entry = {"val": val, "end": end, "fy": fy, "fp": fp, "form": form, "filed": filed, "accn": accn}
    if start is not None:
        entry["start"] = start
    return entry


def _facts(tag_entries: dict, taxonomy: str = "us-gaap", unit: str = "USD") -> dict:
    return {
        "facts": {
            taxonomy: {tag: {"units": {unit: entries}} for tag, entries in tag_entries.items()}
        }
    }


def test_tag_migration_across_filings_both_resolve():
    company_facts = {
        "cik": 1,
        "entityName": "Test Co",
        **_facts(
            {
                "SalesRevenueNet": [
                    _entry(100, "2017-09-30", fy=2017, filed="2017-11-01", accn="A-2017",
                           start="2016-10-01"),
                ],
                "RevenueFromContractWithCustomerExcludingAssessedTax": [
                    # comparative re-report of the same FY2017 period, identical value
                    _entry(100, "2017-09-30", fy=2018, filed="2018-11-01", accn="A-2018",
                           start="2016-10-01"),
                    _entry(120, "2018-09-29", fy=2018, filed="2018-11-01", accn="A-2018",
                           start="2017-10-01"),
                ],
                "NetIncomeLoss": [
                    _entry(30, "2017-09-30", fy=2017, filed="2017-11-01", accn="A-2017",
                           start="2016-10-01"),
                    _entry(40, "2018-09-29", fy=2018, filed="2018-11-01", accn="A-2018",
                           start="2017-10-01"),
                ],
            }
        ),
    }

    statements = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert [s.period_end.isoformat() for s in statements] == ["2017-09-30", "2018-09-29"]
    fy2017, fy2018 = statements
    assert fy2017.revenue.value == 100
    assert fy2017.revenue.xbrl_tag == "SalesRevenueNet"
    assert fy2018.revenue.value == 120
    assert fy2018.revenue.xbrl_tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert fy2018.restated_facts == []


def test_restatement_detected_and_as_reported_uses_earliest_filed():
    company_facts = {
        "cik": 2,
        "entityName": "HBB-like",
        **_facts(
            {
                "RevenueFromContractWithCustomerExcludingAssessedTax": [
                    _entry(612843000, "2019-12-31", fy=2019, filed="2020-02-26", accn="ORIG",
                           form="10-K", start="2019-01-01"),
                    _entry(611786000, "2019-12-31", fy=2019, filed="2020-07-24", accn="AMEND",
                           form="10-K/A", start="2019-01-01"),
                ],
                "NetIncomeLoss": [
                    _entry(50000000, "2019-12-31", fy=2019, filed="2020-02-26", accn="ORIG",
                           start="2019-01-01"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.revenue.value == 612843000
    assert statement.revenue.form == "10-K"
    assert len(statement.restated_facts) == 1
    assert statement.restated_facts[0].value == 611786000
    assert statement.restated_facts[0].form == "10-K/A"


def test_quarterly_entries_excluded_by_span_and_fp_filter():
    company_facts = {
        "cik": 3,
        "entityName": "Quarterly Co",
        **_facts(
            {
                "Revenues": [
                    # Q4 quarter happens to end on the same date as the fiscal year
                    _entry(200000000, "2019-12-31", fy=2019, fp="Q4", filed="2020-02-26",
                           accn="ORIG", start="2019-10-01"),
                    _entry(600000000, "2019-12-31", fy=2019, fp="FY", filed="2020-02-26",
                           accn="ORIG", start="2019-01-01"),
                ],
                "NetIncomeLoss": [
                    _entry(50000000, "2019-12-31", fy=2019, filed="2020-02-26", accn="ORIG",
                           start="2019-01-01"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.revenue.value == 600000000


def test_financial_company_classified_and_missing_metrics_are_none_with_warnings():
    company_facts = {
        "cik": 19617,
        "entityName": "Test Bank",
        **_facts(
            {
                "Revenues": [
                    _entry(177556000000, "2024-12-31", fy=2024, filed="2025-02-14", accn="J-1",
                           start="2024-01-01"),
                ],
                "NetIncomeLoss": [
                    _entry(58471000000, "2024-12-31", fy=2024, filed="2025-02-14", accn="J-1",
                           start="2024-01-01"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_BANK)

    assert statement.company.valuation_category == ValuationCategory.FINANCIAL
    assert statement.operating_income is None
    assert statement.current_assets is None
    assert statement.capex is None
    assert "operating_income: no standard tag found for 2024-12-31" in statement.warnings


def test_instant_metric_matches_without_start():
    company_facts = {
        "cik": 4,
        "entityName": "Instant Co",
        **_facts(
            {
                "Revenues": [
                    _entry(100, "2024-12-31", fy=2024, filed="2025-01-01", accn="I-1",
                           start="2024-01-01"),
                ],
                "NetIncomeLoss": [
                    _entry(10, "2024-12-31", fy=2024, filed="2025-01-01", accn="I-1",
                           start="2024-01-01"),
                ],
                "AssetsCurrent": [
                    _entry(500, "2024-12-31", fy=2024, filed="2025-01-01", accn="I-1"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.current_assets.value == 500


def test_classify_company_by_sic():
    assert classify_company("6021") == ValuationCategory.FINANCIAL
    assert classify_company("3571") == ValuationCategory.STANDARD
    assert classify_company(None) == ValuationCategory.UNSUPPORTED
    assert classify_company("not-a-number") == ValuationCategory.UNSUPPORTED


def test_same_accn_dual_tag_does_not_produce_a_bogus_restatement():
    # Reproduces a real case found in HBB's companyfacts: one 10-K (accn
    # 0001709164-19-000004) tags FY2018 revenue under BOTH
    # RevenueFromContractWithCustomerExcludingAssessedTax and the generic
    # Revenues tag, with the identical value. Selecting matches purely by
    # candidate-tag order without accn-awareness could double-count this as
    # a same-period conflict; the accn dedup in _select_fact must collapse
    # it to one fact and not flag the second tag as a restatement.
    company_facts = {
        "cik": 2,
        "entityName": "HBB-like",
        **_facts(
            {
                "RevenueFromContractWithCustomerExcludingAssessedTax": [
                    _entry(743179000, "2018-12-31", fy=2018, filed="2019-03-06", accn="A-2018",
                           start="2018-01-01"),
                ],
                "Revenues": [
                    _entry(743179000, "2018-12-31", fy=2018, filed="2019-03-06", accn="A-2018",
                           start="2018-01-01"),
                ],
                "NetIncomeLoss": [
                    _entry(30000000, "2018-12-31", fy=2018, filed="2019-03-06", accn="A-2018",
                           start="2018-01-01"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.revenue.value == 743179000
    assert statement.revenue.xbrl_tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert statement.restated_facts == []


def test_short_term_debt_sums_complementary_components():
    # Real MSFT FY2015 numbers: ShortTermBorrowings (commercial paper) and
    # LongTermDebtCurrent (current portion of long-term debt) are both
    # nonzero in the same filing - complementary balance-sheet lines, not
    # alternate tags for the same figure. Picking one via fallback priority
    # (the old behavior) would have understated short-term debt by $2,499M.
    company_facts = {
        "cik": 3,
        "entityName": "MSFT-like",
        **_facts(
            {
                "ShortTermBorrowings": [
                    _entry(4985000000, "2015-06-30", fy=2015, filed="2015-07-31", accn="A-2015"),
                ],
                "LongTermDebtCurrent": [
                    _entry(2499000000, "2015-06-30", fy=2015, filed="2015-07-31", accn="A-2015"),
                ],
                "Revenues": [
                    _entry(93580000000, "2015-06-30", fy=2015, filed="2015-07-31", accn="A-2015",
                           start="2014-07-01"),
                ],
                "NetIncomeLoss": [
                    _entry(12193000000, "2015-06-30", fy=2015, filed="2015-07-31", accn="A-2015",
                           start="2014-07-01"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.short_term_debt.value == 7484000000
    assert statement.short_term_debt.xbrl_tag == "ShortTermBorrowings+LongTermDebtCurrent"


def test_short_term_debt_uses_single_component_when_only_one_present():
    company_facts = {
        "cik": 4,
        "entityName": "AAPL-like",
        **_facts(
            {
                "LongTermDebtCurrent": [
                    _entry(10912000000, "2024-09-28", fy=2024, filed="2024-11-01", accn="A-2024"),
                ],
                "Revenues": [
                    _entry(391035000000, "2024-09-28", fy=2024, filed="2024-11-01", accn="A-2024",
                           start="2023-10-01"),
                ],
                "NetIncomeLoss": [
                    _entry(93736000000, "2024-09-28", fy=2024, filed="2024-11-01", accn="A-2024",
                           start="2023-10-01"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.short_term_debt.value == 10912000000
    assert statement.short_term_debt.xbrl_tag == "LongTermDebtCurrent"


def test_fiscal_year_derived_from_period_end_not_from_borrowed_filing_level_fy():
    # Reproduces AAPL's real oldest 10-K (accn 0001193125-09-214859, filed
    # 2009-10-27): SEC's raw `fy` is a per-filing DocumentFiscalYearFocus,
    # not per-period - every fact in that one filing is stamped fy=2009,
    # including the FY2007 and FY2008 comparative periods it also reports.
    # Trusting fact.fiscal_year verbatim collapsed all three period_ends
    # onto "fiscal_year: 2009" in FinancialStatement. Confirmed live that
    # this filing-level stamping isn't unique to AAPL's earliest filing -
    # every 10-K across AAPL/GOOGL/MSFT/JPM/HBB does it - it just usually
    # self-corrects because the earliest-filed occurrence of a period is
    # normally that period's own primary filing. FY2007/FY2008 never had
    # one (XBRL wasn't mandatory yet), so they never self-correct.
    company_facts = {
        "cik": 320193,
        "entityName": "AAPL-like",
        **_facts(
            {
                "SalesRevenueNet": [
                    _entry(24006000000, "2007-09-29", fy=2009, filed="2009-10-27", accn="OLDEST",
                           start="2006-09-24"),
                    _entry(32479000000, "2008-09-27", fy=2009, filed="2009-10-27", accn="OLDEST",
                           start="2007-09-30"),
                    _entry(36537000000, "2009-09-26", fy=2009, filed="2009-10-27", accn="OLDEST",
                           start="2008-09-28"),
                ],
                "NetIncomeLoss": [
                    _entry(3496000000, "2007-09-29", fy=2009, filed="2009-10-27", accn="OLDEST",
                           start="2006-09-24"),
                    _entry(4834000000, "2008-09-27", fy=2009, filed="2009-10-27", accn="OLDEST",
                           start="2007-09-30"),
                    _entry(5704000000, "2009-09-26", fy=2009, filed="2009-10-27", accn="OLDEST",
                           start="2008-09-28"),
                ],
            }
        ),
    }

    statements = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert [s.fiscal_year for s in statements] == [2007, 2008, 2009]
    assert [s.period_end.isoformat() for s in statements] == [
        "2007-09-29",
        "2008-09-27",
        "2009-09-26",
    ]


def test_short_term_debt_falls_back_to_debt_current_when_no_component_exists():
    company_facts = {
        "cik": 5,
        "entityName": "GOOGL-like",
        **_facts(
            {
                "DebtCurrent": [
                    _entry(2000000000, "2024-12-31", fy=2024, filed="2025-02-01", accn="A-2024"),
                ],
                "Revenues": [
                    _entry(350018000000, "2024-12-31", fy=2024, filed="2025-02-01", accn="A-2024",
                           start="2024-01-01"),
                ],
                "NetIncomeLoss": [
                    _entry(100118000000, "2024-12-31", fy=2024, filed="2025-02-01", accn="A-2024",
                           start="2024-01-01"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.short_term_debt.value == 2000000000
    assert statement.short_term_debt.xbrl_tag == "DebtCurrent"
