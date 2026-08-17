"""Phase 1.5 data spike — observation only, not part of the app.

Fetches real SEC companyfacts for a handful of edge-case companies and
prints, per metric, which XBRL tags exist and what their recent fact
entries look like (unit / period / fy / fp / form / filed / accn).
Findings get written up by hand into docs/DATA_SPIKE_NOTES.md; this
script itself is not imported by the app.
"""

from app.data.sec_client import build_default_client
from app.data.ticker_map import build_default_ticker_map

CONCEPTS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "depreciation_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
        "Depreciation",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForCapitalImprovements",
        "PaymentsToAcquireProductiveAssets",
    ],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "short_term_debt": ["ShortTermBorrowings", "DebtCurrent", "LongTermDebtCurrent"],
}


def annual_entries(fact_entries: list[dict]) -> list[dict]:
    return [e for e in fact_entries if e.get("fp") == "FY" and e.get("form") in ("10-K", "10-K/A")]


def summarize(facts: dict, taxonomy: str = "us-gaap") -> None:
    ns = facts.get("facts", {}).get(taxonomy, {})
    for metric, tag_candidates in CONCEPTS.items():
        present = [t for t in tag_candidates if t in ns]
        print(f"  {metric}: tags present = {present or 'NONE'}")
        for tag in present:
            units = ns[tag].get("units", {})
            for unit, entries in units.items():
                annual = annual_entries(entries)
                last = annual[-3:]
                for e in last:
                    print(
                        f"    [{tag}/{unit}] val={e.get('val')} start={e.get('start')} "
                        f"end={e.get('end')} fy={e.get('fy')} fp={e.get('fp')} "
                        f"form={e.get('form')} filed={e.get('filed')} accn={e.get('accn')}"
                    )


def revenue_tag_history(facts: dict) -> None:
    ns = facts.get("facts", {}).get("us-gaap", {})
    print("  revenue tag history (all annual periods, oldest->newest):")
    for tag in CONCEPTS["revenue"]:
        if tag not in ns:
            continue
        for entries in ns[tag].get("units", {}).values():
            annual = sorted(annual_entries(entries), key=lambda e: e.get("end", ""))
            years = [(e.get("fy"), e.get("form"), e.get("end")) for e in annual]
            print(f"    {tag}: {years}")


def restatement_comparison(facts: dict) -> None:
    ns = facts.get("facts", {}).get("us-gaap", {})
    print("  10-K vs 10-K/A comparison for revenue-like tags:")
    for tag in CONCEPTS["revenue"]:
        if tag not in ns:
            continue
        for entries in ns[tag].get("units", {}).values():
            for e in entries:
                if e.get("form") in ("10-K", "10-K/A"):
                    print(
                        f"    [{tag}] form={e.get('form')} fy={e.get('fy')} "
                        f"end={e.get('end')} val={e.get('val')} filed={e.get('filed')} "
                        f"accn={e.get('accn')}"
                    )


def main() -> None:
    tm = build_default_ticker_map()
    client = build_default_client()

    for ticker in ["GOOGL", "MSFT", "AAPL", "JPM", "HBB"]:
        cik = tm.resolve(ticker)
        sub = client.get_submissions(cik)
        facts = client.get_company_facts(cik)
        print(f"\n=== {ticker} (CIK {cik}) — {sub.get('sicDescription')} ===")
        summarize(facts)
        if ticker == "AAPL":
            revenue_tag_history(facts)
        if ticker == "HBB":
            restatement_comparison(facts)

    client.close()


if __name__ == "__main__":
    main()
