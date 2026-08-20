# Valuation Method

FCFF-based DCF, per Damodaran. This describes exactly what
`app/valuation/{assumptions,fcff,dcf,margin_of_safety}.py` implement -
not a general DCF tutorial.

## 1. FCFF (`app/valuation/fcff.py`)

```
FCFF = EBIT * (1 - tax_rate) + D&A - CapEx - change in non-cash NWC
```

This is **not** the same number as `metrics.YearMetrics.simple_fcf`
(`operating_cash_flow - capex`), which is a quick liquidity-style
figure computed in `app/financials/metrics.py` and never fed into the
DCF. Conflating the two was flagged early in this project's design
review as the most likely source of a silent, hard-to-catch bug, so
the two live in different modules with different names on purpose.

- **EBIT** is approximated by GAAP `operating_income`. A documented
  simplification: EBIT can differ from operating income when a company
  reports non-operating items inside it, and there is no separate EBIT
  tag normalized in the schema.
- **tax_rate** is a required, direct input to `ValuationAssumptions` -
  not derived as an "effective" or "marginal" rate. Deriving either
  would need `income_tax_expense` and `pretax_income`, neither of
  which is normalized (see LIMITATIONS.md). Pass the rate you actually
  want to assume (e.g. the 21% US statutory rate, or your own read of
  the company's effective rate from its 10-K).
- **Change in non-cash NWC** follows Damodaran's definition, not a
  naive `current_assets - current_liabilities`:

  ```
  non-cash NWC = (current_assets - cash) - (current_liabilities - short_term_debt)
  change in NWC(year t) = NWC(t) - NWC(t-1)
  ```

  Cash and short-term debt are financing items, not operating working
  capital. Skipping this adjustment would badly distort FCFF for
  cash-rich companies (AAPL, GOOGL) where cash is a large share of
  current assets. If `short_term_debt` has no matching tag for a
  period, it's treated as `0` with an explicit warning (not a silent
  default) - unlike a missing `current_assets`/`current_liabilities`/
  `cash`, which makes that year's NWC (and therefore FCFF)
  uncomputable.

- FCFF for the first available fiscal year is always `None` - there is
  no prior year to compute a change in NWC against.

### Base FCFF

`ValuationAssumptions.base_fcf_method` chooses how the forecast's
starting point is picked from the FCFF history:

- `latest_year` - most recent computable FCFF
- `3yr_avg` / `5yr_avg` - average of the most recent N computable
  years (fewer years than N available -> proceeds with what exists,
  plus a warning)

Averaging multiple years, not just the latest one, is the default
(`3yr_avg`) because a single year can be distorted by one-off items -
consistent with the project's value-investing framing, which favors
normalized figures over a single snapshot.

### Fundamental growth rate (reference only, `app/valuation/growth.py`)

```
reinvestment rate = (CapEx - D&A + change in non-cash NWC) / NOPAT
ROIC              = NOPAT / (short_term_debt + long_term_debt
                              + stockholders_equity - cash)
fundamental growth rate = reinvestment rate * ROIC
```

Per Damodaran's fundamental-growth framework. `NOPAT`, `CapEx`, `D&A`,
and `change in non-cash NWC` are the exact same per-year values
`compute_fcff_series()` already computes above - this module reuses
them rather than recomputing, so it's automatically consistent with
however FCFF is calculated.

`estimate_fundamental_growth_rate()` reports this per fiscal year plus
a `suggested_growth_rate` (average of the most recent 3 computable
years, same averaging rationale as base FCFF above). **It is a
reference estimate only** - `analysis_service.analyze()` always returns
it as `fundamental_growth_estimate`, alongside whatever
`fcff_growth_rate` the caller actually supplied in `assumptions`, and
never substitutes one for the other. This is the same side-by-side
principle as qualitative risk vs. quantitative MOS (section 4 below):
the DCF result should tell you what your assumption implies, and this
should tell you what the company's own reinvestment/return history
implies, and reconciling the two is the caller's judgment call, not
something this project collapses into one number automatically.

A negative or missing value here is informative, not a bug: a company
that shrank its reinvestment (buybacks exceeding net capex, e.g. a
mature company like AAPL in some recent fiscal years) or lacks
`stockholders_equity`/balance-sheet tags (e.g. a bank - see
DATA_SPIKE_NOTES.md finding #6) will legitimately show
`suggested_growth_rate: null` or a negative number, with per-year
`warnings` explaining why - never a silently wrong positive default.

**Sign trap: two negatives multiply to a misleading positive.** If
`reinvestment_rate` and `roic` are *both* negative (a company with a
negative NOPAT that is also shrinking its reinvestment), their product
is a positive `growth_rate` that reads as healthy growth but describes
the opposite - a business with a negative return on capital. Found
live on CRCL (Circle Internet Group): `growth_rate=+27.7%` from
`reinvestment_rate=-656%` and `roic=-4.2%`. `_growth_year()` adds an
explicit per-year warning in this case rather than reporting the
number unqualified.

**Near-zero-denominator trap: book equity crushed by buybacks/leverage
inflates ROIC to an implausible magnitude.** A mature company that has
spent years buying back stock (Home Depot) or is carrying heavy
leverage against accumulated losses (Boeing) can end up with book
`stockholders_equity` near zero - nothing wrong with the business, just
its capital structure. ROIC's denominator (invested capital) then
shrinks toward zero too, and `NOPAT / invested_capital` explodes: found
live at `roic=144%` for HD and `roic=113%` for BA, both clearly
unrealistic. `estimate_fundamental_growth_rate()` takes an optional
`market_price` parameter; for the *latest* fiscal year only, invested
capital is computed with market value of equity
(`market_price * shares_outstanding` - the same market price
`analyze()` already receives for margin_of_safety, not a new data
source) instead of book equity, since a heavy-buyback company's market
cap doesn't collapse just because its book equity did. Verified live:
HD's ROIC dropped from 144% to 4.8%, BA's from 113% to 1.5%. Earlier
fiscal years still use book equity (no historical price series is
available to value them contemporaneously), so `suggested_growth_rate`
(a 3-year average) only partly corrects for this - see LIMITATIONS.md.

## 2. DCF (`app/valuation/dcf.py`)

```
projected FCFF(t) = base_fcff * (1 + fcff_growth_rate)^t,  t = 1..forecast_years
discounted FCFF(t) = projected FCFF(t) / (1 + discount_rate)^t

terminal value = FCFF(forecast_years) * (1 + terminal_growth_rate)
                 / (discount_rate - terminal_growth_rate)
discounted terminal value = terminal value / (1 + discount_rate)^forecast_years

enterprise value = sum(discounted FCFF) + discounted terminal value

equity value = enterprise value + cash - (short_term_debt + long_term_debt)
value per share = equity value / shares_outstanding
```

`ValuationAssumptions` requires `fcff_growth_rate`, `discount_rate`,
`terminal_growth_rate`, and `tax_rate` explicitly - none of these
numbers exist anywhere inside the calculation code itself.
`terminal_growth_rate >= discount_rate` is rejected at construction
time (the terminal value formula is undefined/negative otherwise).

If `cash` or `shares_outstanding` is missing on the statement being
valued, the equity-value bridge or per-share step is skipped with an
explicit warning rather than silently treating the missing value as
zero.

**No discount rate (WACC) calculator.** `discount_rate` is a required
user input, not computed from a cost-of-equity/cost-of-debt model -
that would need market data (beta, cost of debt) V1 deliberately
doesn't fetch. See LIMITATIONS.md.

### Terminal value dominance warning

If the discounted terminal value exceeds 75% of enterprise value, the
result carries a warning: a short forecast horizon relative to the
terminal assumption means most of the valuation rests on an assumption
about the distant future, not near-term projected cash flow - a
classic DCF critique from the value-investing tradition this project
is built around, not just an implementation detail.

### Sensitivity - never a single number

`run_dcf_valuation()` always returns a 3x3 grid
(`DCFResult.sensitivity`) of `value_per_share` across
`discount_rate ± {0, 1pp}` and `terminal_growth_rate ± {0, 1pp}`
around the base assumptions. Combinations where the varied terminal
growth rate would be >= the varied discount rate are marked invalid
(`value_per_share: null`) rather than silently skipped or crashing.

A single point estimate implies more precision than a DCF actually
has; margin-of-safety reasoning needs a range to reason about, not a
falsely precise number.

## 3. Margin of Safety (`app/valuation/margin_of_safety.py`)

```
MOS = (intrinsic_value - market_price) / intrinsic_value
```

`compute_margin_of_safety(statements, assumptions, market_price,
as_of_date)`:

1. **Resolves each metric to what was actually known as of that date -
   the as-reported fact, or a later restatement, whichever has the
   latest `filed_date` that is still `<= as_of_date`.** This is two
   things, and an earlier version only did the first:
   - never use data that wasn't public yet (`filed_date <= as_of_date`
     - the look-ahead-bias principle, see DATA_SPIKE_NOTES.md finding
     #3, where a real 10-K/A changed a reported figure after the fact)
   - but DO use a restatement once it *was* public - freezing at the
     original pre-restatement number past that point is its own kind
     of look-ahead-adjacent error (using known-superseded data instead
     of what the market actually knew). Verified against real HBB data:
     `as_of_date=2020-06-01` (before its 10-K/A) resolves FY2019 revenue
     to the original $612,843,000; `as_of_date=2020-08-01` (after)
     resolves it to the restated $611,786,000.
   A fiscal year with nothing resolvable as of `as_of_date` (nothing
   filed yet) is excluded the same way a wholly-future statement always
   was.
2. Runs the DCF on the remaining (eligible, as-of-date-resolved) statements.
3. Computes MOS for the base case, plus `margin_of_safety_low/high`
   read directly off the DCF's own sensitivity grid (min/max
   `value_per_share`) - not a separately invented range. MOS is
   monotonic in intrinsic value for intrinsic value > 0, so this
   pairing is always consistent.

A non-positive intrinsic value produces `margin_of_safety: null`
rather than a nonsensical or divide-by-zero result.

**Market price is a request input, not fetched.** V1 deliberately does
not integrate a market-data provider - keeping "what the market says"
separate from "what SEC filings say" is what let this project isolate
SEC-layer bugs from valuation-layer bugs during development, and there
is no reason to lose that separation now.

## 4. Qualitative risk and sentiment (V2) - never merged into MOS numerically

`app/qualitative/risk_extraction.py` and `app/qualitative/sentiment.py`
produce structured output from text (an auto-fetched 10-K, and/or a
user-pasted earnings call transcript), but **there is no formula that
converts a qualitative risk into dollars of intrinsic value or
percentage points of margin of safety**, and this project deliberately
does not invent one. Every other assumption in this system has some
grounding - a stated growth/discount-rate input, a formula from
Damodaran, a tag verified against real data. "N high-severity risks =
X% off the margin of safety" would have none of that; it would be a
made-up number wearing a formula's clothes, which is exactly what the
rest of this document argues against (no auto-derived tax rate, no
computed WACC, always a range instead of a point estimate).

Instead, `analysis_service.analyze()` reports `margin_of_safety`
(quantitative) and `qualitative_analyses`/`sentiment_analyses`
side by side in the same response, and only adds a `warnings` entry
when 2+ high-severity qualitative risks are found
(`HIGH_SEVERITY_WARNING_THRESHOLD`) - a flag to look closer, not an
adjustment to the number. Reading both together is the user's job, the
same way a human analyst would treat a DCF output and a risk-factors
read as two separate inputs to a judgment call, not two terms in one
equation.

FinBERT sentiment (`score_sentiment()`) is a faster, free, complementary
signal to the LLM risk extraction, not a replacement for it - see
DATA_MODEL.md and DATA_SPIKE_NOTES.md's V2 section for how the text is
prepared and why FinBERT is self-hosted rather than a hosted API.

Each extracted risk also carries `supporting_quote`/`grounding`, and
`run_cross_model_extraction()` can run two Claude tiers and flag
disagreement between them - see ARCHITECTURE.md's "Verification gates"
section for how these map onto the human-in-the-loop checks described
in the AI-Assisted Value Investing paper (Caridi et al., Electronics
2026), and LIMITATIONS.md for what those checks do not cover.

## Defaults at the API boundary

`app/api/analysis.py` defines `DEFAULT_ASSUMPTIONS` (5% FCFF growth,
9% discount rate, 2.5% terminal growth, 21% tax rate, 5-year forecast,
3-year average base FCFF) used when a request doesn't specify
assumptions. This is a documented, overridable constant at the HTTP
boundary, not a number inside the valuation math - and the response
always echoes back whichever assumptions actually produced it, per the
project's "never just return a number" requirement.
