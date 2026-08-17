# Limitations

Honest accounting of what V1 does not do, so results are read as a
starting point for analysis, not a verdict. Grouped by layer.

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

## Normalization layer

- **"As reported" always wins over restated values** for the metric
  actually used in valuation - `restated_facts` are surfaced but never
  substituted automatically. If a company's restated figures are more
  accurate for your purposes, you'd need to read `restated_facts`
  directly.
- **Fiscal-year discovery depends on `revenue` and `net_income`.** A
  company that tags neither under a recognized concept (unlikely but
  possible) would show zero discoverable fiscal years.

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

## Scope, generally

- No qualitative analysis of any kind (10-K/10-Q text, earnings calls,
  news) - this is explicitly a V2+ concern per the original project
  plan, and no text-processing code exists yet.
- No portfolio management, trade execution, or stock screening/
  recommendation.
- No mobile or web UI - this is a JSON API only.
- Single-ticker analysis only; no batch or comparative endpoints.
