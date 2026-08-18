"""Raw SEC companyfacts -> FinancialStatement, per docs/DATA_SPIKE_NOTES.md.

Selection rules (all confirmed against real data in the Phase 1.5 spike,
not assumed):
- A fact must be annual: fp == "FY", form in (10-K, 10-K/A).
- Duration concepts (income/cash-flow items) additionally need a ~365-day
  start/end span - fp alone let quarterly restatement data leak through
  in the HBB case (finding #4).
- "As reported" is the matching fact with the earliest filed_date. Later
  filings often re-show the same period as a comparative with an
  identical value (not a restatement); a later fact is only treated as a
  restatement if its value actually differs (finding #3).
"""

from datetime import date

from app.data.models import CompanyInfo, FinancialFact, FinancialStatement, ValuationCategory

ANNUAL_FORMS = ("10-K", "10-K/A")

# taxonomy, candidate tags in priority order. A filing uses one tag
# consistently across its comparative years (finding #2), so we don't
# need to resolve per-year - just try each candidate against the period.
CONCEPT_CANDIDATES: dict[str, tuple[str, list[str]]] = {
    "revenue": (
        "us-gaap",
        [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
            "Revenues",
        ],
    ),
    "operating_income": ("us-gaap", ["OperatingIncomeLoss"]),
    "net_income": ("us-gaap", ["NetIncomeLoss"]),
    "operating_cash_flow": ("us-gaap", ["NetCashProvidedByUsedInOperatingActivities"]),
    "depreciation_amortization": (
        "us-gaap",
        [
            "DepreciationDepletionAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
            "DepreciationAndAmortization",
            "Depreciation",
        ],
    ),
    "capex": (
        "us-gaap",
        [
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsForCapitalImprovements",
            "PaymentsToAcquireProductiveAssets",
        ],
    ),
    "current_assets": ("us-gaap", ["AssetsCurrent"]),
    "current_liabilities": ("us-gaap", ["LiabilitiesCurrent"]),
    "cash": (
        "us-gaap",
        [
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ],
    ),
    "short_term_debt": ("us-gaap", ["ShortTermBorrowings", "DebtCurrent", "LongTermDebtCurrent"]),
    # LongTermDebtNoncurrent only - NOT a fallback list with "LongTermDebt".
    # Checked live: AAPL/GOOGL/MSFT report both tags in the same filing with
    # different values (LongTermDebt appears to include items beyond the
    # noncurrent balance-sheet line), so treating them as interchangeable
    # fallbacks would silently corrupt the figure.
    "long_term_debt": ("us-gaap", ["LongTermDebtNoncurrent"]),
    "stockholders_equity": ("us-gaap", ["StockholdersEquity"]),
    # dei:EntityCommonStockSharesOutstanding reports an "as of" cover-page
    # date near the filing date, not the fiscal year end, so it can't be
    # matched by end == period_end like the rest. us-gaap's own tag does
    # align to period_end - verified against real AAPL/JPM data before
    # picking it.
    "shares_outstanding": ("us-gaap", ["CommonStockSharesOutstanding"]),
}

DURATION_METRICS = {
    "revenue",
    "operating_income",
    "net_income",
    "operating_cash_flow",
    "depreciation_amortization",
    "capex",
}

_FINANCIAL_SIC_RANGE = range(6000, 6800)


def classify_company(sic: str | None) -> ValuationCategory:
    if sic is None:
        return ValuationCategory.UNSUPPORTED
    try:
        code = int(sic)
    except ValueError:
        return ValuationCategory.UNSUPPORTED
    if code in _FINANCIAL_SIC_RANGE:
        return ValuationCategory.FINANCIAL
    return ValuationCategory.STANDARD


def normalize(company_facts: dict, submissions: dict) -> list[FinancialStatement]:
    company = build_company_info(company_facts, submissions)
    facts_ns = company_facts.get("facts", {})
    period_ends = _discover_period_ends(facts_ns)
    return [_build_statement(company, facts_ns, period_end) for period_end in sorted(period_ends)]


def build_company_info(company_facts: dict, submissions: dict) -> CompanyInfo:
    tickers = submissions.get("tickers") or [""]
    sic = submissions.get("sic")
    return CompanyInfo(
        cik=str(company_facts.get("cik", "")).zfill(10),
        ticker=tickers[0],
        name=company_facts.get("entityName") or submissions.get("name", ""),
        sic=sic,
        sic_description=submissions.get("sicDescription"),
        valuation_category=classify_company(sic),
    )


def _is_annual_entry(entry: dict, *, is_duration: bool) -> bool:
    if entry.get("form") not in ANNUAL_FORMS:
        return False
    if entry.get("fp") != "FY":
        return False
    if not is_duration:
        return True
    start = entry.get("start")
    if not start:
        return False
    span = (date.fromisoformat(entry["end"]) - date.fromisoformat(start)).days
    return 350 <= span <= 380


def _discover_period_ends(facts_ns: dict) -> set[date]:
    period_ends: set[date] = set()
    for metric in ("revenue", "net_income"):
        taxonomy, tags = CONCEPT_CANDIDATES[metric]
        ns = facts_ns.get(taxonomy, {})
        for tag in tags:
            concept = ns.get(tag)
            if not concept:
                continue
            for entries in concept.get("units", {}).values():
                for entry in entries:
                    if _is_annual_entry(entry, is_duration=True):
                        period_ends.add(date.fromisoformat(entry["end"]))
    return period_ends


def _select_fact(
    facts_ns: dict, metric: str, period_end: date
) -> tuple[FinancialFact | None, list[FinancialFact]]:
    taxonomy, tags = CONCEPT_CANDIDATES[metric]
    is_duration = metric in DURATION_METRICS
    ns = facts_ns.get(taxonomy, {})

    matches: list[tuple[str, str, dict]] = []
    for tag in tags:
        concept = ns.get(tag)
        if not concept:
            continue
        for unit, entries in concept.get("units", {}).items():
            for entry in entries:
                if entry.get("end") != period_end.isoformat():
                    continue
                if not _is_annual_entry(entry, is_duration=is_duration):
                    continue
                matches.append((tag, unit, entry))

    if not matches:
        return None, []

    matches.sort(key=lambda m: m[2]["filed"])
    as_reported = _to_fact(metric, taxonomy, *matches[0])

    # This is how "one fact per filing" (finding #2) actually gets enforced:
    # once as_reported's accn is picked, every other match sharing that same
    # accn - regardless of which tag it came from - is dropped here, never
    # considered as a restatement candidate. A real case this guards
    # against: HBB dual-tags one filing's revenue under both
    # RevenueFromContractWithCustomerExcludingAssessedTax and the generic
    # Revenues tag with an identical value - without this dedup, that
    # second tag's entry would need separate handling to avoid being
    # mistaken for a competing value from the same filing.
    restated: list[FinancialFact] = []
    seen_accn = {as_reported.accession_number}
    for tag, unit, entry in matches[1:]:
        if entry["accn"] in seen_accn:
            continue
        seen_accn.add(entry["accn"])
        if entry["val"] == as_reported.value:
            continue
        restated.append(_to_fact(metric, taxonomy, tag, unit, entry))

    return as_reported, restated


def _to_fact(metric: str, taxonomy: str, tag: str, unit: str, entry: dict) -> FinancialFact:
    return FinancialFact(
        metric=metric,
        value=entry["val"],
        unit=unit,
        taxonomy=taxonomy,
        xbrl_tag=tag,
        period_start=entry.get("start"),
        period_end=entry["end"],
        fiscal_year=entry["fy"],
        fiscal_period=entry["fp"],
        form=entry["form"],
        filed_date=entry["filed"],
        accession_number=entry["accn"],
        frame=entry.get("frame"),
    )


def _build_statement(company: CompanyInfo, facts_ns: dict, period_end: date) -> FinancialStatement:
    fields: dict[str, FinancialFact | None] = {}
    restated_facts: list[FinancialFact] = []
    warnings: list[str] = []
    fiscal_year: int | None = None

    for metric in CONCEPT_CANDIDATES:
        fact, restated = _select_fact(facts_ns, metric, period_end)
        fields[metric] = fact
        restated_facts.extend(restated)
        if fact is None:
            warnings.append(f"{metric}: no standard tag found for {period_end.isoformat()}")
        elif fiscal_year is None:
            fiscal_year = fact.fiscal_year

    return FinancialStatement(
        company=company,
        fiscal_year=fiscal_year if fiscal_year is not None else period_end.year,
        period_end=period_end,
        restated_facts=restated_facts,
        warnings=warnings,
        **fields,
    )
