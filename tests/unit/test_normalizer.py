import pytest

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


def test_interest_expense_tag_migration_reproduces_hd_case():
    # HD renamed InterestExpense -> InterestExpenseNonoperating starting
    # with its FY2025 10-K - confirmed live that the prior comparative
    # period is re-tagged with an identical value under the new name in
    # that filing (a clean rename, same as revenue's tag history), not a
    # different concept coexisting with a different value.
    company_facts = {
        "cik": 1,
        "entityName": "HD-like",
        **_facts(
            {
                "Revenues": [
                    _entry(150000000000, "2024-01-28", fy=2024, filed="2024-03-01",
                           accn="A-2024", start="2023-01-30"),
                    _entry(160000000000, "2025-02-02", fy=2025, filed="2025-03-01",
                           accn="A-2025", start="2024-01-29"),
                ],
                "NetIncomeLoss": [
                    _entry(15000000000, "2024-01-28", fy=2024, filed="2024-03-01",
                           accn="A-2024", start="2023-01-30"),
                    _entry(16000000000, "2025-02-02", fy=2025, filed="2025-03-01",
                           accn="A-2025", start="2024-01-29"),
                ],
                "InterestExpense": [
                    _entry(1943000000, "2024-01-28", fy=2024, filed="2024-03-01",
                           accn="A-2024", start="2023-01-30"),
                ],
                "InterestExpenseNonoperating": [
                    # comparative re-report of the same FY2024 period, identical value
                    _entry(1943000000, "2024-01-28", fy=2025, filed="2025-03-01",
                           accn="A-2025", start="2023-01-30"),
                    _entry(2321000000, "2025-02-02", fy=2025, filed="2025-03-01",
                           accn="A-2025", start="2024-01-29"),
                ],
            }
        ),
    }

    statements = normalize(company_facts, SUBMISSIONS_STANDARD)

    fy2024, fy2025 = sorted(statements, key=lambda s: s.period_end)
    assert fy2024.interest_expense.value == 1943000000
    assert fy2024.interest_expense.xbrl_tag == "InterestExpense"
    assert fy2025.interest_expense.value == 2321000000
    assert fy2025.interest_expense.xbrl_tag == "InterestExpenseNonoperating"
    assert fy2025.restated_facts == []


def test_interest_expense_falls_back_to_interest_and_debt_expense_when_no_component_exists():
    # Reproduces Boeing: no InterestExpense fact in any year - the real
    # total interest expense (~$2.7-2.8B, matching its known debt load) is
    # only under InterestAndDebtExpense.
    company_facts = {
        "cik": 2,
        "entityName": "BA-like",
        **_facts(
            {
                "Revenues": [
                    _entry(66000000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
                "NetIncomeLoss": [
                    _entry(2600000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
                "InterestAndDebtExpense": [
                    _entry(2771000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.interest_expense.value == 2771000000
    assert statement.interest_expense.xbrl_tag == "InterestAndDebtExpense"


def test_interest_expense_missing_leaves_none_with_warning():
    # Reproduces Apple's FY2024/2025 10-Ks: no discrete interest-expense
    # concept reported under any known tag - a real gap, not guessed at.
    company_facts = {
        "cik": 3,
        "entityName": "AAPL-like",
        **_facts(
            {
                "Revenues": [
                    _entry(391000000000, "2025-09-27", fy=2025, filed="2025-10-31",
                           accn="A-2025", start="2024-09-29"),
                ],
                "NetIncomeLoss": [
                    _entry(112000000000, "2025-09-27", fy=2025, filed="2025-10-31",
                           accn="A-2025", start="2024-09-29"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.interest_expense is None
    assert "interest_expense: no standard tag found for 2025-09-27" in statement.warnings


def test_operating_income_derived_from_pretax_when_never_tagged_reproduces_nke_case():
    # Nike's real FY2026 10-K, hand-verified: Revenues 46,398 - Cost of
    # sales 26,487 - Demand creation 4,754 - Operating overhead 11,360 =
    # 3,797 real operating income; OperatingIncomeLoss is never tagged at
    # all (checked live - zero 10-K entries in Nike's full filing
    # history), a legitimate GAAP choice (no subtotal line), not missing
    # data.
    company_facts = {
        "cik": 1,
        "entityName": "NKE-like",
        **_facts(
            {
                "Revenues": [
                    _entry(46398000000, "2026-05-31", fy=2026, filed="2026-07-01",
                           accn="A-2026", start="2025-06-01"),
                ],
                "NetIncomeLoss": [
                    _entry(3108000000, "2026-05-31", fy=2026, filed="2026-07-01",
                           accn="A-2026", start="2025-06-01"),
                ],
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": [
                    _entry(3900000000, "2026-05-31", fy=2026, filed="2026-07-01",
                           accn="A-2026", start="2025-06-01"),
                ],
                "InterestIncomeExpenseNonoperatingNet": [
                    _entry(50000000, "2026-05-31", fy=2026, filed="2026-07-01",
                           accn="A-2026", start="2025-06-01"),
                ],
                "OtherNonoperatingIncomeExpense": [
                    _entry(53000000, "2026-05-31", fy=2026, filed="2026-07-01",
                           accn="A-2026", start="2025-06-01"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.operating_income.value == 3797000000
    assert statement.operating_income.xbrl_tag.startswith("derived(")
    assert any(
        "operating_income derived from pretax income" in w for w in statement.warnings
    )


def test_operating_income_derivation_defaults_missing_components_to_zero():
    # Reproduces Chevron: pretax income exists but the interest/other
    # tags this formula also uses don't - defaults them to 0 rather than
    # giving up, since a partial derivation is still more useful than
    # none, as long as it's flagged (checked live: Chevron's real
    # structure has a third material reconciling item - equity affiliate
    # income - this formula doesn't capture, so the derived figure is a
    # known-incomplete approximation for exactly this kind of company).
    company_facts = {
        "cik": 2,
        "entityName": "CVX-like",
        **_facts(
            {
                "Revenues": [
                    _entry(184432000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
                "NetIncomeLoss": [
                    _entry(17660000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments": [
                    _entry(19743000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.operating_income.value == 19743000000


def test_operating_income_derivation_skipped_for_financial_companies():
    # A bank can have a matching pretax-income tag too (checked live on
    # JPM) - the derivation must not fire for financial companies, since
    # "pretax income minus non-operating interest" is meaningless when
    # interest IS the core business, not a reconciling item.
    company_facts = {
        "cik": 3,
        "entityName": "Bank-like",
        **_facts(
            {
                "Revenues": [
                    _entry(50000000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
                "NetIncomeLoss": [
                    _entry(10000000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": [
                    _entry(12000000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_BANK)

    assert statement.operating_income is None
    assert "operating_income: no standard tag found for 2025-12-31" in statement.warnings


def test_operating_income_derivation_not_attempted_when_directly_tagged():
    company_facts = {
        "cik": 4,
        "entityName": "HD-like",
        **_facts(
            {
                "Revenues": [
                    _entry(100000000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
                "NetIncomeLoss": [
                    _entry(15000000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
                "OperatingIncomeLoss": [
                    _entry(20000000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": [
                    _entry(99999999999, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
            }
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.operating_income.value == 20000000000
    assert statement.operating_income.xbrl_tag == "OperatingIncomeLoss"


def _facts_with_shares(usd_tags: dict, shares_tags: dict) -> dict:
    usd = _facts(usd_tags, unit="USD")["facts"]["us-gaap"]
    shares = _facts(shares_tags, unit="shares")["facts"]["us-gaap"]
    return {"facts": {"us-gaap": {**usd, **shares}}}


def test_shares_outstanding_falls_back_to_weighted_average_diluted_reproduces_nke_case():
    # Nike never tags CommonStockSharesOutstanding at all (checked live) -
    # only its weighted-average diluted share count, at a normal
    # DJIA-scale raw magnitude (~1.48B), which needs no scale correction.
    company_facts = {
        "cik": 5,
        "entityName": "NKE-like",
        **_facts_with_shares(
            {
                "Revenues": [
                    _entry(46398000000, "2026-05-31", fy=2026, filed="2026-07-01",
                           accn="A-2026", start="2025-06-01"),
                ],
                "NetIncomeLoss": [
                    _entry(3108000000, "2026-05-31", fy=2026, filed="2026-07-01",
                           accn="A-2026", start="2025-06-01"),
                ],
            },
            {
                "WeightedAverageNumberOfDilutedSharesOutstanding": [
                    _entry(1481000000, "2026-05-31", fy=2026, filed="2026-07-01",
                           accn="A-2026", start="2025-06-01"),
                ],
            },
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.shares_outstanding.value == 1481000000
    assert statement.shares_outstanding.xbrl_tag == "WeightedAverageNumberOfDilutedSharesOutstanding"
    assert any("derived from" in w for w in statement.warnings)
    assert not any("assumed reported in millions" in w for w in statement.warnings)


def test_shares_outstanding_falls_back_to_basic_when_diluted_absent():
    company_facts = {
        "cik": 6,
        "entityName": "MRK-like",
        **_facts_with_shares(
            {
                "Revenues": [
                    _entry(64000000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
                "NetIncomeLoss": [
                    _entry(21000000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
            },
            {
                "WeightedAverageNumberOfSharesOutstandingBasic": [
                    _entry(2502000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
            },
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.shares_outstanding.value == 2502000000
    assert statement.shares_outstanding.xbrl_tag == "WeightedAverageNumberOfSharesOutstandingBasic"


def test_shares_outstanding_scale_corrected_when_reported_in_millions_reproduces_mcd_case():
    # McDonald's real FY2025 10-K income statement is headed "In millions"
    # and literally prints "Weighted-average shares outstanding-diluted
    # 716.4" (net_income $8,563M / 716,400,000 shares = $11.95/share,
    # exactly its real reported diluted EPS) - not a filer tagging error,
    # a real presentation choice this normalizer must detect and undo.
    company_facts = {
        "cik": 7,
        "entityName": "MCD-like",
        **_facts_with_shares(
            {
                "Revenues": [
                    _entry(25923000000, "2025-12-31", fy=2025, filed="2026-02-24",
                           accn="A-2025", start="2025-01-01"),
                ],
                "NetIncomeLoss": [
                    _entry(8563000000, "2025-12-31", fy=2025, filed="2026-02-24",
                           accn="A-2025", start="2025-01-01"),
                ],
            },
            {
                "WeightedAverageNumberOfDilutedSharesOutstanding": [
                    _entry(716.4, "2025-12-31", fy=2025, filed="2026-02-24",
                           accn="A-2025", start="2025-01-01"),
                ],
            },
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.shares_outstanding.value == 716400000.0
    assert statement.net_income.value / statement.shares_outstanding.value == pytest.approx(
        11.95, abs=0.01
    )
    assert any("assumed reported in millions" in w for w in statement.warnings)


def test_shares_outstanding_prefers_point_in_time_tag_over_weighted_average():
    company_facts = {
        "cik": 8,
        "entityName": "HD-like-shares",
        **_facts_with_shares(
            {
                "Revenues": [
                    _entry(100000000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
                "NetIncomeLoss": [
                    _entry(15000000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
                "CommonStockSharesOutstanding": [
                    _entry(1000000000, "2025-12-31", fy=2025, filed="2026-02-01", accn="A-2025"),
                ],
            },
            {
                "WeightedAverageNumberOfDilutedSharesOutstanding": [
                    _entry(999000000, "2025-12-31", fy=2025, filed="2026-02-01",
                           accn="A-2025", start="2025-01-01"),
                ],
            },
        ),
    }

    [statement] = normalize(company_facts, SUBMISSIONS_STANDARD)

    assert statement.shares_outstanding.value == 1000000000
    assert statement.shares_outstanding.xbrl_tag == "CommonStockSharesOutstanding"
    assert not any("derived from" in w for w in statement.warnings)
