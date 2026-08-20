"""WACC estimate from real market data, per Damodaran's bottom-up-beta
and synthetic-rating framework - a REFERENCE figure only, same status
as fundamental_growth_estimate (see growth.py): `analyze()` returns it
alongside whatever `discount_rate` the caller supplied in `assumptions`,
and never substitutes one for the other.

    cost_of_equity = risk_free_rate + levered_beta * equity_risk_premium
    levered_beta   = industry_unlevered_beta * (1 + (1-tax_rate) * D/E)
    cost_of_debt   = risk_free_rate + synthetic_rating_spread(interest coverage)
    WACC           = E/(D+E) * cost_of_equity + D/(D+E) * cost_of_debt * (1-tax_rate)

This is the one part of the system that reaches outside SEC EDGAR:
risk_free_rate (10Y Treasury) is fetched live from FRED
(app/data/market_data.py) because it cannot be derived from any single
company's own filings - it's a government bond yield, not a fact about
the company. equity_risk_premium and the industry-beta/synthetic-rating
tables below are NOT live-fetched (Damodaran publishes both as
downloadable data, not a queryable API) - they're documented constants,
sourced and dated, the same status as DEFAULT_ASSUMPTIONS elsewhere in
this project. They need periodic manual updates, not a code change to
use differently.

FALLBACK_RISK_FREE_RATE exists because the live fetch has a real,
confirmed failure mode: FRED's unauthenticated fredgraph.csv endpoint
reproducibly failed from GitHub Actions' hosted-runner IP range (2/2
runs) while working fine from a normal residential connection in the
same session, and Yahoo Finance (comps.py's peer-price source) worked
from the exact same CI run - so this isn't a generic network problem,
it looks like IP-range-based blocking specific to FRED. Any deployment
on similar cloud/datacenter infrastructure could hit the same thing, not
just CI - so analysis_service.py falls back to this dated constant
(with an explicit warning) rather than dropping wacc_estimate entirely
when the live fetch fails.

Sources (both accessed 2026-08-20):
- ERP: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/
  (implied ERP, January 2026 update) = 4.23%
- Synthetic rating table: https://pages.stern.nyu.edu/~adamodar/
  New_Home_Page/datafile/ratings.htm (large non-financial-service firms)
- Industry unlevered betas: https://pages.stern.nyu.edu/~adamodar/
  New_Home_Page/datafile/Betas.html (January 2026 update)

Bottom-up (industry-average) beta, not a per-company regression: a
single company's own regression beta is noisy (large standard errors) -
Damodaran's own stated reason for averaging across an industry's
regression betas instead. This project doesn't fetch historical price
series at all, so a regression beta was never on the table either way -
see docs/VALUATION_METHOD.md for the fuller discussion of this
tradeoff.
"""

from pydantic import BaseModel

from app.data.models import FinancialStatement

# Damodaran, implied ERP for the S&P 500, January 2026 data update.
# https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/
EQUITY_RISK_PREMIUM = 0.0423

# Used only when the live FRED fetch fails (see module docstring) - the
# 10-year Treasury yield observed directly, multiple times, during this
# feature's own development (2026-08-20), not a long-term reference
# figure. Gets noticeably stale faster than ERP/beta above; a caller
# relying on this fallback is told so via an explicit warning.
FALLBACK_RISK_FREE_RATE = 0.047

# Used only when a company's SIC code doesn't match any bucket below.
# 1.0 is the CAPM-neutral "market average" beta, not a sourced Damodaran
# figure - a documented, honest placeholder rather than false precision.
FALLBACK_UNLEVERED_BETA = 1.0
FALLBACK_INDUSTRY_LABEL = "Diversified (no SIC match - market-average beta used)"

# (SIC prefix, industry label, unlevered beta). Checked in order, most
# specific prefix first; first match wins. A coarse approximation of
# Damodaran's ~90-industry table via SIC major/sub-group prefixes, not
# an exhaustive SIC crosswalk - covers the industries actually seen in
# this project's spiked/tested companies (see DATA_SPIKE_NOTES.md) and
# common large-cap sectors, not every SIC code that exists.
INDUSTRY_UNLEVERED_BETA: list[tuple[str, str, float]] = [
    ("2834", "Drugs (Pharmaceutical)", 0.89),
    ("2836", "Drugs (Biotechnology)", 1.03),
    ("283", "Drugs (Pharmaceutical)", 0.89),
    ("2911", "Oil/Gas (Integrated)", 0.27),
    ("1311", "Oil/Gas (Production and Exploration)", 0.56),
    ("3674", "Semiconductor", 1.49),
    ("3559", "Semiconductor Equip", 1.35),
    ("357", "Software (System & Application)", 1.23),
    ("7372", "Software (System & Application)", 1.23),
    ("7370", "Software (Internet)", 1.55),
    ("7371", "Software (Internet)", 1.55),
    ("7379", "Software (Internet)", 1.55),
    ("3721", "Aerospace/Defense", 0.85),
    ("3760", "Aerospace/Defense", 0.85),
    ("3711", "Retail (Automotive)", 0.70),
    ("2086", "Household Products", 0.72),
    ("2840", "Household Products", 0.72),
    ("2000", "Retail (Grocery and Food)", 0.80),
    ("581", "Restaurant/Dining", 0.77),
    ("5211", "Retail (Building Supply)", 1.31),
    ("541", "Retail (Grocery and Food)", 0.80),
    ("531", "Retail (General)", 0.76),
    ("533", "Retail (General)", 0.76),
    ("3841", "Healthcare Products", 0.83),
    ("4813", "Telecom. Services", 0.37),
    ("48", "Telecom. Services", 0.37),
    ("22", "Apparel", 0.76),
    ("23", "Apparel", 0.76),
    ("3559", "Machinery", 0.87),
    ("35", "Machinery", 0.87),
    ("781", "Entertainment", 0.74),
    ("483", "Broadcasting", 0.29),
    ("602", "Banks (Regional)", 0.29),
    ("603", "Bank (Money Center)", 0.34),
    ("631", "Insurance (Life)", 0.43),
    ("632", "Insurance (General)", 0.56),
    ("633", "Insurance (Prop/Cas.)", 0.44),
    ("6199", "Financial Svcs. (Non-bank & Insurance)", 0.32),
    ("671", "Financial Svcs. (Non-bank & Insurance)", 0.32),
]

# (min interest coverage, rating, pretax spread over risk-free rate).
# Damodaran's large-cap (>$5B) synthetic-rating table, checked from the
# top (highest coverage) down - first range the ratio falls into wins.
# Uses -inf as the effective floor for the lowest bucket.
SYNTHETIC_RATING_TABLE: list[tuple[float, str, float]] = [
    (8.50, "Aaa/AAA", 0.0040),
    (6.50, "Aa2/AA", 0.0055),
    (5.50, "A1/A+", 0.0070),
    (4.25, "A2/A", 0.0078),
    (3.00, "A3/A-", 0.0089),
    (2.50, "Baa2/BBB", 0.0111),
    (2.25, "Ba1/BB+", 0.0138),
    (2.00, "Ba2/BB", 0.0184),
    (1.75, "B1/B+", 0.0275),
    (1.50, "B2/B", 0.0321),
    (1.25, "B3/B-", 0.0509),
    (0.80, "Caa/CCC", 0.0885),
    (0.65, "Ca2/CC", 0.1261),
    (0.20, "C2/C", 0.1600),
    (float("-inf"), "D2/D", 0.1900),
]


class WACCEstimate(BaseModel):
    industry: str
    unlevered_beta: float
    levered_beta: float | None = None
    cost_of_equity: float | None = None
    synthetic_rating: str | None = None
    cost_of_debt_pretax: float | None = None
    cost_of_debt_aftertax: float | None = None
    wacc: float | None = None
    risk_free_rate: float
    equity_risk_premium: float
    warnings: list[str] = []


def _value(fact) -> float | None:
    return fact.value if fact is not None else None


def unlevered_beta_for_sic(sic: str | None) -> tuple[str, float]:
    if sic:
        for prefix, label, beta in INDUSTRY_UNLEVERED_BETA:
            if sic.startswith(prefix):
                return label, beta
    return FALLBACK_INDUSTRY_LABEL, FALLBACK_UNLEVERED_BETA


def _synthetic_rating(interest_coverage: float) -> tuple[str, float]:
    for min_coverage, rating, spread in SYNTHETIC_RATING_TABLE:
        if interest_coverage >= min_coverage:
            return rating, spread
    return SYNTHETIC_RATING_TABLE[-1][1], SYNTHETIC_RATING_TABLE[-1][2]


def estimate_wacc(
    statement: FinancialStatement,
    market_price: float,
    risk_free_rate: float,
    tax_rate: float,
) -> WACCEstimate:
    warnings: list[str] = []
    industry, unlevered_beta = unlevered_beta_for_sic(statement.company.sic)

    shares_outstanding = _value(statement.shares_outstanding)
    market_equity = market_price * shares_outstanding if shares_outstanding else None
    if market_equity is None:
        warnings.append("shares_outstanding not found - cannot compute market value of equity")

    short_term_debt = _value(statement.short_term_debt) or 0.0
    long_term_debt = _value(statement.long_term_debt) or 0.0
    debt = short_term_debt + long_term_debt

    levered_beta = unlevered_beta
    if market_equity is not None and market_equity > 0:
        levered_beta = unlevered_beta * (1 + (1 - tax_rate) * (debt / market_equity))
    else:
        warnings.append("could not relever beta to this company's own D/E - using unlevered beta")

    cost_of_equity = risk_free_rate + levered_beta * EQUITY_RISK_PREMIUM

    operating_income = _value(statement.operating_income)
    interest_expense = _value(statement.interest_expense)
    synthetic_rating = None
    cost_of_debt_pretax = None
    cost_of_debt_aftertax = None
    if operating_income is None or interest_expense is None:
        warnings.append(
            "operating_income/interest_expense not found - cannot estimate cost of debt"
        )
    elif interest_expense == 0:
        synthetic_rating, spread = SYNTHETIC_RATING_TABLE[0][1], SYNTHETIC_RATING_TABLE[0][2]
        cost_of_debt_pretax = risk_free_rate + spread
        cost_of_debt_aftertax = cost_of_debt_pretax * (1 - tax_rate)
    else:
        interest_coverage = operating_income / interest_expense
        synthetic_rating, spread = _synthetic_rating(interest_coverage)
        cost_of_debt_pretax = risk_free_rate + spread
        cost_of_debt_aftertax = cost_of_debt_pretax * (1 - tax_rate)

    wacc = None
    if market_equity is not None and market_equity > 0:
        total_capital = market_equity + debt
        weight_equity = market_equity / total_capital
        weight_debt = debt / total_capital
        if debt == 0:
            wacc = cost_of_equity
        elif cost_of_debt_aftertax is not None:
            wacc = weight_equity * cost_of_equity + weight_debt * cost_of_debt_aftertax
        else:
            warnings.append("has debt but no cost-of-debt estimate - cannot compute full WACC")
    else:
        warnings.append("cannot compute capital-structure weights - no WACC estimate")

    return WACCEstimate(
        industry=industry,
        unlevered_beta=unlevered_beta,
        levered_beta=levered_beta,
        cost_of_equity=cost_of_equity,
        synthetic_rating=synthetic_rating,
        cost_of_debt_pretax=cost_of_debt_pretax,
        cost_of_debt_aftertax=cost_of_debt_aftertax,
        wacc=wacc,
        risk_free_rate=risk_free_rate,
        equity_risk_premium=EQUITY_RISK_PREMIUM,
        warnings=warnings,
    )
