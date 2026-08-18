# Limitations

Honest accounting of what this system does not do, so results are read
as a starting point for analysis, not a verdict. Grouped by layer.

## Data layer

- **SEC EDGAR is the only data source.** No cross-validation against
  another provider. If SEC's own XBRL tagging is wrong for a company,
  this system reproduces that error.
- **Custom-taxonomy XBRL is not parsed.** `companyfacts` only
  aggregates standard taxonomies (`us-gaap`, `dei`, `ifrs-full`,
  `srt`). If a company reports an important figure only under a
  company-specific extension tag, it will show up here as a missing
  metric, not as a differently-named one.
- **Tag fallback lists reflect a five-company spike** (AAPL, GOOGL,
  MSFT, JPM, HBB - see DATA_SPIKE_NOTES.md), not an exhaustive survey.
  A company using an XBRL tag outside those lists will show missing
  metrics with warnings rather than crashing, but the specific number
  may simply be unavailable until that tag is added (after checking it
  live first - see DATA_MODEL.md).
- **Tag migration is ongoing, not a one-time 2018 (ASC 606) event.**
  GOOGL switched its revenue tag again in its FY2025 10-K, filed in
  2026. Fallback lists will need periodic maintenance.
- **`long_term_debt` uses a single tag** (`LongTermDebtNoncurrent`)
  with no fallback, because the obvious alternative (`LongTermDebt`)
  was confirmed to report a *different number* in the same filing for
  AAPL/GOOGL/MSFT. Some companies may simply lack a matching tag as a
  result.
- **Whether `depreciation_amortization` and `cash`'s fallback
  candidates measure the same thing is not fully verified**, unlike
  `short_term_debt` (fixed - see below) and `long_term_debt`/
  `shares_outstanding` (confirmed single-tag). AAPL reports
  `DepreciationDepletionAndAmortization` vs `Depreciation`, and
  `CashAndCashEquivalentsAtCarryingValue` vs `...RestrictedCash...`,
  simultaneously with different values in the same filing. The current
  priority order's choice is plausible (broader cash-flow-statement
  figure for D&A, narrower unrestricted-cash figure for cash) but
  hasn't been checked against what `AssetsCurrent` itself includes -
  see DATA_MODEL.md.

## Normalization layer

- **As-of-date resolution picks the latest fact known by that date**
  (as-reported or a later restatement, whichever was filed later but
  still `<= as_of_date`) - implemented in
  `margin_of_safety._resolve_fact_as_of()`, not in the normalizer
  itself. The normalizer's `FinancialStatement.revenue` etc. always
  hold the as-reported value; `restated_facts` holds the rest. Reading
  a `FinancialStatement` directly (bypassing `compute_margin_of_safety`)
  gets the as-reported figure only.
- **Not every value change in a later filing is a formal SEC
  restatement.** `restated_facts` is populated whenever a later filing
  reports a different value for the same period, whatever the cause -
  reclassification, a discontinued-operations recast, a presentation
  change, or an actual restatement. The field name implies more
  certainty about cause than the data actually supports.
- **Fiscal-year discovery depends on `revenue` and `net_income`.** A
  company that tags neither under a recognized concept (unlikely but
  possible) would show zero discoverable fiscal years.
- **Same-filing, same-period, multiple-tag entries were checked across
  all five spiked companies** (not just the HBB case that prompted the
  check) - 82 instances found. Most are genuinely different concepts a
  company reports side by side (see the D&A/cash bullet above and
  `short_term_debt`'s fix), not data-quality noise, but this was a
  scan of five companies, not an exhaustive one - a similar issue for
  a metric not yet checked this closely could still exist.

## Financial-metrics layer

- **No net debt / leverage ratio.** Computing it correctly needs
  `long_term_debt` (added in Phase 5, used by the DCF) but the metrics
  layer (Phase 4) was built before that field existed and hasn't been
  revisited to add it as a standalone ratio outside the DCF's own
  `total_debt`.
- **Safety flags (negative FCF, negative margin, current ratio < 1.0)
  are mechanical, not judgment.** AAPL's real data trips the
  current-ratio flag every year because it deliberately keeps low
  current assets, not because it's in distress - a flag says "look at
  this," not "this is bad."

## Valuation layer

- **EBIT is approximated by GAAP operating income.** No adjustment for
  non-operating items that might be included in it.
- **Tax rate is a direct user input**, not derived from the company's
  actual filings. Auto-deriving an effective or marginal rate would
  need `income_tax_expense` and `pretax_income`, which aren't
  normalized in the schema.
- **No WACC calculator.** `discount_rate` must be supplied; V1 does
  not compute cost of equity (e.g. via CAPM/beta) or cost of debt from
  market data.
- **Financial companies (banks, insurers - SIC 6000-6799) get no
  valuation at all**, not an adapted one. Damodaran's literature
  describes alternative approaches for these (e.g. treating regulatory
  capital as reinvestment, FCFE-based models) - none are implemented.
  The same applies to any company SIC-classified as `UNSUPPORTED`.
- **REITs, financial-development-stage companies, and other
  DCF-unfriendly business types outside the 6000-6799 SIC range are
  not specially handled** - they'll run through the standard DCF path
  even though FCFF/DCF may not be the right lens for them either.
- **The 75% terminal-value-dominance warning threshold is a
  heuristic**, not a statistically derived cutoff.
- **The ±1 percentage point sensitivity grid is a fixed, small
  neighborhood** around the base assumptions, not a full exploration
  of assumption uncertainty.

## Margin-of-safety layer

- **Market price is a request input, not fetched from any market-data
  provider.** There is no live-quote integration in V1.
- **Look-ahead-bias filtering by `filed_date` is implemented but not
  backtested.** It has not been validated against a known historical
  scenario to confirm point-in-time results match what a real investor
  would have seen.

## Qualitative layer (V2)

- **No section-level extraction from filings.** Checked live against
  AAPL and MSFT's 10-Ks first: filer-generated HTML structure varies
  too much between vendors to reliably isolate "just Risk Factors" or
  "just MD&A" (see DATA_MODEL.md and DATA_SPIKE_NOTES.md's V2 section).
  The whole cleaned document is handed to the LLM instead - it may
  spend attention on sections (legal boilerplate, exhibits) that aren't
  actually relevant.
- **Earnings calls are not fetched automatically.** There's no free SEC
  source for call transcripts; the caller pastes the text directly via
  `earnings_call_text`. Nothing validates that the pasted text is
  accurate, complete, or actually from the company/quarter claimed.
- **No numeric "Adjusted Margin of Safety."** Qualitative risk and
  quantitative MOS are reported side by side, never combined into one
  score - see VALUATION_METHOD.md for why. A high-severity-risk warning
  is a flag to look closer, not a discount applied to the number.
- **LLM risk extraction quality is not independently benchmarked.**
  Spiked once against a real AAPL 10-K with plausible, well-grounded
  output (see `scripts/spike_qualitative_risk.py`), but there's no
  systematic evaluation against a labeled set of filings, and no check
  that the model isn't missing a material risk it should have flagged.
- **FinBERT sentiment is sentence-level and context-free.** Each
  sentence is classified independently - a sentence like "the Company
  no longer faces material litigation risk" can score negative on
  "litigation risk" wording despite being good news, since FinBERT
  doesn't see surrounding sentences for context.
- **Both qualitative features cost real resources per call**: the LLM
  call costs money (a few cents per 10-K on Haiku - see
  DATA_SPIKE_NOTES.md), and FinBERT sentiment adds tens of seconds of
  CPU inference time per request. Neither runs unless explicitly
  requested (`analyze_10k`/`earnings_call_text`/`include_sentiment`).

## Scope, generally

- No portfolio management, trade execution, or stock screening/
  recommendation.
- No mobile or web UI - this is a JSON API only.
- Single-ticker analysis only; no batch or comparative endpoints.
