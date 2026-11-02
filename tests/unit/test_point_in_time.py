"""As-of-date resolution: what was knowable on a date.

HBB's FY2019 revenue is the worked example throughout - $612,843,000 as
originally reported (10-K, filed 2020-02-26), restated to $611,786,000
in a 10-K/A filed 2020-07-24. Real numbers from
docs/DATA_SPIKE_NOTES.md, reused here as the shape of a genuine
restatement.
"""

from datetime import date

from app.financials.point_in_time import (
    FACT_FIELDS,
    filter_look_ahead,
    resolve_fact_as_of,
    resolve_statement_as_of,
)
from tests.factories import make_fact, make_statement

AS_REPORTED = ("revenue", 612843000, "2019-12-31", 2019)
RESTATED_VALUE = 611786000


def as_reported_fact():
    return make_fact(*AS_REPORTED, filed_date="2020-02-26", accession_number="ORIG")


def restated_fact():
    return make_fact(
        "revenue", RESTATED_VALUE, "2019-12-31", 2019, filed_date="2020-07-24",
        form="10-K/A", accession_number="AMEND",
    )


def test_resolve_fact_as_of_uses_as_reported_before_restatement_filed():
    resolved = resolve_fact_as_of(as_reported_fact(), [restated_fact()], date(2020, 6, 1))

    assert resolved.value == 612843000


def test_resolve_fact_as_of_uses_restated_value_once_public():
    resolved = resolve_fact_as_of(as_reported_fact(), [restated_fact()], date(2020, 8, 1))

    assert resolved.value == RESTATED_VALUE


def test_resolve_fact_as_of_switches_on_the_restatement_filing_date_itself():
    # The boundary is <=, so the restatement counts on the day it is filed.
    assert resolve_fact_as_of(
        as_reported_fact(), [restated_fact()], date(2020, 7, 23)
    ).value == 612843000
    assert resolve_fact_as_of(
        as_reported_fact(), [restated_fact()], date(2020, 7, 24)
    ).value == RESTATED_VALUE


def test_resolve_fact_as_of_none_before_anything_filed():
    assert resolve_fact_as_of(as_reported_fact(), [], date(2019, 12, 31)) is None


def test_resolve_fact_as_of_picks_the_latest_of_several_restatements():
    second = make_fact(
        "revenue", 600000000, "2019-12-31", 2019, filed_date="2021-01-15",
        form="10-K/A", accession_number="AMEND2",
    )

    resolved = resolve_fact_as_of(
        as_reported_fact(), [restated_fact(), second], date(2021, 6, 1)
    )

    assert resolved.value == 600000000


def test_resolve_fact_as_of_handles_a_restatement_with_no_original():
    # Possible when the as-reported fact used a tag this project doesn't
    # map but the amendment used one it does.
    assert resolve_fact_as_of(None, [restated_fact()], date(2020, 8, 1)).value == RESTATED_VALUE
    assert resolve_fact_as_of(None, [], date(2020, 8, 1)) is None


def test_resolve_statement_as_of_excludes_statement_with_nothing_public_yet():
    statement = make_statement(2019, "2019-12-31", revenue=612843000)

    assert resolve_statement_as_of(statement, date(2000, 1, 1)) is None


def test_resolve_statement_as_of_drops_only_the_fields_not_yet_public():
    # A statement is not all-or-nothing: metrics within one fiscal year
    # can become public at different times.
    late = make_fact("net_income", 50, "2019-12-31", 2019, filed_date="2021-01-01")
    statement = make_statement(2019, "2019-12-31", revenue=612843000)
    statement = statement.model_copy(update={"net_income": late})

    resolved = resolve_statement_as_of(statement, date(2020, 6, 1))

    assert resolved.revenue.value == 612843000
    assert resolved.net_income is None


def test_filter_look_ahead_counts_what_it_dropped():
    old = make_statement(2019, "2019-12-31", revenue=100)
    new = make_statement(2024, "2024-12-31", revenue=200)

    eligible, excluded = filter_look_ahead([old, new], date(2021, 1, 1))

    assert [s.fiscal_year for s in eligible] == [2019]
    assert excluded == 1


def test_filter_look_ahead_is_idempotent():
    # analyze() filters once up front and compute_margin_of_safety()
    # filters again defensively - the second pass must be a no-op, or
    # the excluded count reported to the caller would be wrong.
    statements = [
        make_statement(2019, "2019-12-31", revenue=100),
        make_statement(2024, "2024-12-31", revenue=200),
    ]

    once, excluded_once = filter_look_ahead(statements, date(2021, 1, 1))
    twice, excluded_twice = filter_look_ahead(once, date(2021, 1, 1))

    assert [s.fiscal_year for s in twice] == [s.fiscal_year for s in once]
    assert excluded_once == 1
    assert excluded_twice == 0


def test_filter_look_ahead_on_an_empty_list():
    assert filter_look_ahead([], date(2021, 1, 1)) == ([], 0)


def test_every_fact_field_exists_on_the_statement_model():
    # FACT_FIELDS is hand-listed so that adding a metric to
    # FinancialStatement without resolving it here is a visible
    # omission. This catches the other direction: a field renamed or
    # removed from the model while FACT_FIELDS still names it, which
    # would silently resolve to nothing.
    statement = make_statement(2019, "2019-12-31", revenue=100)

    for field_name in FACT_FIELDS:
        assert field_name in type(statement).model_fields
