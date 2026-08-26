# Valuation Method

FCFF-based DCF, per Damodaran, plus a growing set of reference methods
(Owner Earnings DCF, WACC estimate, Comps) intersected into one
consensus range. This describes exactly what
`app/valuation/{assumptions,fcff,dcf,margin_of_safety,growth,wacc,
comps,owner_earnings,consensus}.py` implement - not a general valuation
tutorial.

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
- Some companies (verified live: NKE, MRK, CVX) never tag
  `OperatingIncomeLoss` at all - not a migration gap, but a legitimate
  GAAP presentation choice (no subtotal line is required). When no
  operating-income tag exists at all, `app/financials/normalizer.py`
  derives one as a last-resort fallback:

  ```
  operating_income = pretax_income - InterestIncomeExpenseNonoperatingNet
                                    - OtherNonoperatingIncomeExpense
  ```

  Verified exact against NKE's and MRK's real income statements. Not
  universally valid - cross-checked against HD and MSFT (companies
  that DO have a real `OperatingIncomeLoss` tag) and the formula did
  not reproduce their true figures, so it only ever fires when
  `operating_income` has no tag at all, and it never overrides a real
  tag. It's also not guaranteed complete: CVX lacks a third
  reconciling item (equity-affiliate income, ~$3B) that this formula
  doesn't capture, so its derived value understates true operating
  income. Restricted to `ValuationCategory.STANDARD` companies -
  financial companies (banks) can coincidentally have a matching
  pretax-income tag too, but "pretax income minus non-operating
  interest" is meaningless for a bank, where interest income/expense
  IS the core business. Always returned with an explicit warning so
  it's never mistaken for a figure read directly off the filing. See
  [DATA_SPIKE_NOTES.md](DATA_SPIKE_NOTES.md) V7 for the full
  verification trail.
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

### WACC estimate (reference only, `app/valuation/wacc.py`)

```
cost_of_equity = risk_free_rate + levered_beta * equity_risk_premium
levered_beta   = industry_unlevered_beta * (1 + (1-tax_rate) * D/E)
cost_of_debt   = risk_free_rate + synthetic_rating_spread(interest coverage)
WACC           = E/(D+E) * cost_of_equity + D/(D+E) * cost_of_debt * (1-tax_rate)
```

Same status as fundamental growth rate above: `analyze()` returns this
as `wacc_estimate` alongside whatever `discount_rate` the caller
supplied, opt-in via `compute_wacc` (an extra request to FRED), and
never substituted into `assumptions.discount_rate`.

**The one part of this system that reaches outside SEC EDGAR.**
`risk_free_rate` (10-year Treasury yield) is fetched live from FRED
(`app/data/market_data.py`) because it cannot be derived from any
company's own filings by definition - it's a government bond yield.
`equity_risk_premium` and the industry-beta/synthetic-rating tables are
NOT live-fetched (Damodaran publishes both as downloadable data, not a
queryable API) - they're documented, dated constants in `wacc.py`,
needing periodic manual updates, the same status as
`DEFAULT_ASSUMPTIONS`.

**Bottom-up (industry-average) beta, not a per-company regression.** A
single company's own regression beta is noisy - this is Damodaran's own
stated reason for averaging across an industry's regression betas
instead of using one company's. This project doesn't fetch historical
stock-price series at all (a genuine design boundary - see
LIMITATIONS.md), so a true per-company regression beta was never an
option either way; the industry-average approach happens to also be the
one Damodaran recommends on the merits.

Debt (D) uses SEC book value; equity (E) uses market value
(`market_price * shares_outstanding`) - both computed the same way
growth.py's near-zero-denominator fix above does. Cost of debt comes
from Damodaran's synthetic-rating table keyed to interest coverage
(`operating_income / interest_expense`) - `interest_expense` is a new
normalized field (`app/financials/normalizer.py`), added and verified
against real data alongside this feature: HD/AAPL/JPM used a plain
`InterestExpense` tag through ~FY2023, then HD renamed it to
`InterestExpenseNonoperating` starting its FY2025 10-K (confirmed a
clean rename, not a different concept); Boeing has never had an
`InterestExpense` fact at all, only `InterestAndDebtExpense`; Apple's
FY2024/2025 10-Ks report no discrete interest-expense concept under any
known tag - a real gap, left as `None` with a warning.

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

`shares_outstanding` itself has the same kind of tag-availability gap
`operating_income` does (see above). Checked live: NKE/MRK/CVX/KO/JNJ/PG
never tag `CommonStockSharesOutstanding` at all, and WMT tagged it only
through its FY2011 10-K, then stopped. `app/financials/normalizer.py`
falls back to `WeightedAverageNumberOfDilutedSharesOutstanding` (then
`...Basic`) when the point-in-time tag is absent - a weighted-average
over the fiscal year rather than a balance-sheet-date count, flagged
with a warning either way. A second, independent issue surfaced on
McDonald's specifically: its weighted-average share tag is reported at
the same scale as its dollar figures ("In millions" on the face of its
real 10-K income statement, share count included - not a filer error),
so a value under 10,000,000 is assumed to be in millions and scaled
`x1,000,000` (verified exact: the corrected count divides MCD's real
net income into $11.9528/share, matching its actual reported diluted
EPS of $11.95). Visa is the one company checked where no fallback
exists either - it has no shares-outstanding-shaped tag under any
namespace, a genuine data gap, not a naming mismatch. See
[DATA_SPIKE_NOTES.md](DATA_SPIKE_NOTES.md) V8 for the full verification
trail.

**`discount_rate` is a required user input, still never computed
inside the DCF itself.** A reference WACC estimate exists
(`wacc_estimate`, opt-in via `compute_wacc` - see section 1's WACC
subsection above) but is deliberately never substituted for
`assumptions.discount_rate`, the same side-by-side principle as
`fundamental_growth_estimate`. See LIMITATIONS.md for what the estimate
itself does not cover.

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

**The target company's own market price is always a request input, never
fetched.** Keeping "what the market says about this company" separate
from "what SEC filings say" is what let this project isolate SEC-layer
bugs from valuation-layer bugs during development, and there's no
reason to lose that separation for the number margin_of_safety is
actually computed against - even though other companies' prices (comps
peers, below) and a Treasury yield (wacc_estimate) are now fetched for
reference-only estimates elsewhere in the response.

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

## 5. Comps (reference only, `app/valuation/comps.py`)

```
EV       = market_price * shares_outstanding + debt - cash    (per company)
EBITDA   = operating_income + depreciation_amortization        (a proxy)

peer EV/EBITDA, EV/Revenue, P/E -> median across a curated peer group
implied value/share = (peer median multiple * target's own metric
                        - target's debt + target's cash) / target's shares
```

The other leg of the "several methods, one football-field range" toolkit
real institutions use alongside DCF (see the JPM/BlackRock/Buffett
discussion this feature came out of) - relative valuation (what similar
companies currently trade at) instead of absolute valuation (what this
company's own cash flows are worth, discounted). Same reference-only
status as `fundamental_growth_estimate`/`wacc_estimate`: `analyze()`
returns this as `comps_estimate` alongside `margin_of_safety`, opt-in
via `compute_comps`, never merged into it.

**Peer lists are curated, documented constants, not a live SEC
discovery query.** SEC's own browse-edgar SIC-search endpoint was tried
and rejected: its XML output has a long-standing serialization bug
where the company name comes back literally as `"ARRAY(0x...)"` instead
of a string, and matches carry no size/relevance ranking - the first N
results for a SIC code are alphabetical noise (a shell company as often
as a real peer), and ranking candidates by size would mean fetching
every one's market cap before filtering, which doesn't fit a single
request's latency budget. Same tradeoff already made for `wacc.py`'s
industry beta table (see that module's docstring) - `comps.py` reuses
the identical SIC-prefix-matching convention, on purpose, as a parallel
structure.

**A peer group can be thin - the median is still computed, never
withheld, but flagged.** Some curated buckets have as few as one peer
(e.g. HD's only listed peer is LOW); `MIN_PEERS_FOR_MEDIAN`-style
silent suppression was tried first and rejected after it produced
`null` implied values that looked like a bug rather than "there's only
one comparable company" - same "compute it, but warn" principle as
`select_base_fcff()` averaging over fewer years than requested.
`peers` skipped because a ticker doesn't resolve, has no shares
outstanding, or Yahoo Finance has no price for it are named individually
in `warnings`, not silently dropped.

Peer prices come from `app/data/market_data.py`'s `fetch_current_price()`
- the same Yahoo Finance chart API endpoint used to build the
30-DJIA-constituent reference spreadsheet before this feature existed,
now formalized into the app. Unofficial/undocumented, no API key - see
that function's docstring for the reliability caveat this carries that
FRED and SEC EDGAR do not.

## 6. Owner Earnings DCF (`app/valuation/owner_earnings.py`)

```
Owner Earnings = net_income + D&A - maintenance_capex - change_in_NWC
```

Per Buffett's 1986 Berkshire shareholder letter - a second DCF variant
alongside section 2's FCFF-based one, reusing `dcf.py`'s `run_dcf()`/
`run_sensitivity()` unchanged (they don't care what the "base cash
flow" represents), just discounting owner earnings instead of FCFF.
Two real differences from FCFF, not just a rename: owner earnings
starts from **net income** (after interest and tax - an equity-level
measure) rather than NOPAT (a firm-level, pre-interest measure), and it
splits capex into a maintenance component rather than subtracting the
total.

Buffett's own letter admits maintenance capex "cannot be precisely
calculated" from GAAP data and "must be a guess" - and no XBRL tag
distinguishes maintenance from growth capex. Rather than inventing a
single unverifiable split, this project bounds it:

- **conservative**: maintenance capex = full capex (assumes zero growth
  capex)
- **optimistic**: maintenance capex = D&A only (a common analyst
  heuristic - capex above D&A is growth)

`owner_earnings_low`/`_high` take `min`/`max` of the two variants, not
a hardcoded assignment, so the range stays correctly ordered even in
the atypical case (capex < D&A) where the labels would otherwise flip.
Verified live: for AAPL (capex ≈ D&A, a mature business needing little
growth capex) the two variants land within ~0.2% of each other across
recent years; for AMZN (capex >> D&A, heavy AI/data-center buildout)
the 2025 spread between them is ~$66B - the range width itself is a
real signal about how much of a company's capex is discretionary.

`run_owner_earnings_dcf_valuation()` runs the full DCF+sensitivity
twice (once from the conservative base, once from the optimistic base)
and takes the min value-per-share from the low run and the max from
the high run - combining the maintenance-capex uncertainty with the
usual discount-rate/terminal-growth sensitivity into one range, same
as `unsupported_reason` handling for financial companies (raises
`UnsupportedValuationError`, caught by `analyze()`).

## 7. Valuation Consensus (`app/valuation/consensus.py`)

Every method that produces a value-per-share range - DCF (FCFF),
Owner Earnings DCF, and Comps (when `compute_comps=True`) - contributes
one `ValueRange(method, low, high)`. `compute_consensus()` intersects
all of them:

```
overlap_low  = max(low_i for each available range)
overlap_high = min(high_i for each available range)
```

If `overlap_low > overlap_high`, there is no consensus - the methods
genuinely disagree, and this is reported explicitly as a warning
(`overlap_low`/`overlap_high` come back `null`) rather than silently
producing a nonsensical inverted range. This is the same "football
field" comparison real institutions use when running DCF, comps, and
precedent transactions side by side (see the JPM/BlackRock/Buffett
methodology discussion this whole ranges-and-intersection design came
out of) - precedent transactions analysis itself is not implemented in
this project (no M&A deal-multiple data source), so the consensus here
only ever spans up to three methods, not the full institutional set.

Verified live: HD's three methods overlap tightly (DCF-FCFF
$261-$464, Owner Earnings DCF $232-$414, Comps $236-$264 -> consensus
$261-$264, a real 3-way agreement zone). NVDA's do not overlap at all
(both DCF variants land around $23-$52, Comps lands at $139-$511 -
`overlap_low`/`_high` are `null` with an explicit "no overlap" warning)
- disagreement this stark between an absolute-valuation approach and a
peer-multiple approach is itself informative, not a bug to paper over.

`scripts/analyze.py`'s `format_range_chart()` renders these ranges (and
the overlap, when one exists) as a terminal bar chart:

```
                      Valuation Range

DCF (FCFF)                  ├──────────────────────────────────────────┤
                            $261                                       $464

DCF (Owner Earnings)  ├─────────────────────────────────────┤
                      $232                                  $414

Comps                  ├─────┤
                       $236  $264

Overlap                     ├─┤
                            $261~$264
```

## 8. IFRS / non-USD filers (`app/financials/normalizer.py`, `app/data/market_data.py`)

Everything above assumes a US domestic filer reporting under US-GAAP in
a 10-K/10-K/A, in USD. A foreign private issuer (e.g. Novo Nordisk/NVO,
a Danish company) instead files Form **20-F**, reporting under **IFRS**,
in its home currency. Checked live: NVO has zero `us-gaap` facts, only
`ifrs-full` (253 tags) - so supporting it needs three separate things,
all handled automatically with no extra request parameter:

1. **Taxonomy detection.** `_detect_taxonomy()` checks whether a
   company's raw companyfacts JSON has any `us-gaap` facts (existing
   GAAP path, unchanged) or only `ifrs-full` facts (new IFRS path,
   `IFRS_CONCEPT_CANDIDATES`, forms `20-F`/`20-F/A` instead of
   `10-K`/`10-K/A`). Every IFRS tag was verified against NVO's real
   FY2025 20-F figures - see
   [DATA_SPIKE_NOTES.md](DATA_SPIKE_NOTES.md) V9. Three US-GAAP-only
   selection strategies (short_term_debt's additive-tags handling,
   operating_income derivation, shares_outstanding's weighted-average
   fallback) deliberately have no IFRS equivalent yet - they're each
   verified against only one company (NVO doesn't need any of them, it
   tags all three directly), and generalizing an unverified fallback to
   a second company risks repeating the JPM near-regression from
   finding #6.
2. **Currency conversion.** `market_price` is always assumed USD (how a
   US-listed ticker is quoted). `app/data/market_data.py`'s
   `fetch_fx_rate()` gets a live spot rate from Yahoo Finance - reusing
   `fetch_current_price()` unchanged, since Yahoo prices FX pairs as
   ordinary tickers (`"DKKUSD=X"`). `app/financials/normalizer.py`'s
   `convert_statements_to_usd()` then converts every monetary
   `FinancialFact` (not `shares_outstanding` - a count, not a currency
   amount) to USD.
3. **Where the conversion happens.** `normalize()` itself stays
   offline/pure - no network calls, same as always. `analyze()` calls it
   once, immediately after `normalize()` and before anything else reads
   `statements` (growth estimate, WACC, comps, FCFF/DCF, Owner
   Earnings). Every one of those modules is unmodified - they just see
   already-USD figures, with no currency-awareness of their own. If
   conversion is needed but `market_data_client` is `None`, `analyze()`
   raises `MarketDataError` rather than silently computing a
   USD-labeled number that's actually still denominated in DKK - see
   [LIMITATIONS.md](LIMITATIONS.md).

## Defaults at the API boundary

`app/api/analysis.py` defines `DEFAULT_ASSUMPTIONS` (5% FCFF growth,
9% discount rate, 2.5% terminal growth, 21% tax rate, 5-year forecast,
3-year average base FCFF) used when a request doesn't specify
assumptions. This is a documented, overridable constant at the HTTP
boundary, not a number inside the valuation math - and the response
always echoes back whichever assumptions actually produced it, per the
project's "never just return a number" requirement.
