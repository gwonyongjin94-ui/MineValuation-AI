# Limitations

Honest accounting of what this system does not do, so results are read
as a starting point for analysis, not a verdict. Grouped by layer.

## Data layer

- **SEC EDGAR is the only data source for every company *financial*
  fact.** No cross-validation against another provider - if SEC's own
  XBRL tagging is wrong for a company, this system reproduces that
  error. Three reference-only/support estimates reach outside SEC EDGAR
  for non-financial-statement data: `compute_wacc`'s risk-free rate
  (FRED - a macro/government figure with no company-specific
  counterpart in SEC filings by definition), `compute_comps`'s peer
  current prices (Yahoo Finance - a live trading price, likewise not
  something a filing reports), and an IFRS/20-F filer's currency
  conversion (also Yahoo Finance, an FX spot rate - see the
  Normalization layer section below). The first two never touch
  `margin_of_safety` itself - see VALUATION_METHOD.md's WACC and Comps
  sections - but currency conversion does, by design: it has to run
  before margin_of_safety for a non-USD filer's numbers to be
  USD-comparable at all.
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
  `short_term_debt` (fixed - see below) and `long_term_debt`
  (confirmed single-tag). AAPL reports
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
- **`FinancialStatement.fiscal_year` assumes fiscal years are named
  after the calendar year they end in.** It's derived from
  `period_end.year`, not SEC's raw `fy` field (see DATA_MODEL.md).
  Verified against all five spiked companies (AAPL/GOOGL/MSFT/JPM/HBB),
  but a company using the opposite naming convention (fiscal year named
  after the year it mostly falls in or starts in - seen at some
  retailers) hasn't been checked and would get a `fiscal_year` off by
  one.
- **Same-filing, same-period, multiple-tag entries were checked across
  all five spiked companies** (not just the HBB case that prompted the
  check) - 82 instances found. Most are genuinely different concepts a
  company reports side by side (see the D&A/cash bullet above and
  `short_term_debt`'s fix), not data-quality noise, but this was a
  scan of five companies, not an exhaustive one - a similar issue for
  a metric not yet checked this closely could still exist.
- **`operating_income` is derived, not just tagged, when no
  operating-income concept exists at all.** Verified live: NKE, MRK,
  and CVX never tag `OperatingIncomeLoss` (a legitimate GAAP
  presentation choice, not missing data), so
  `_derive_operating_income()` falls back to
  `pretax_income - InterestIncomeExpenseNonoperatingNet -
  OtherNonoperatingIncomeExpense`. This is an approximation flagged
  with an explicit warning, not a value read directly off the filing -
  it's known-incomplete for CVX specifically (missing a third
  reconciling item, equity-affiliate income, ~$3B), and it's not
  guaranteed correct for any company beyond the ones checked. It only
  fires when `operating_income` has no tag at all (never overrides a
  real tag) and only for `ValuationCategory.STANDARD` companies -
  financial companies are excluded because "pretax income minus
  non-operating interest" is meaningless for a bank. See
  [DATA_SPIKE_NOTES.md](DATA_SPIKE_NOTES.md) V7.
- **`shares_outstanding` falls back to a weighted-average share count,
  with a scale correction, when no point-in-time tag exists.** Checked
  live: NKE/MRK/CVX/KO/JNJ/PG never tag `CommonStockSharesOutstanding`
  at all; WMT tagged it only through FY2011, then stopped entirely.
  `_select_shares_outstanding()` falls back to
  `WeightedAverageNumberOfDilutedSharesOutstanding` (then `...Basic`) -
  a fiscal-year average, not a balance-sheet-date count, flagged via
  warning. Separately, McDonald's real 10-K reports that same tag at
  the same scale as its dollar figures ("In millions," share count
  included, on the face of the actual filing - not a tagging error), so
  a value under 10,000,000 is assumed to be in millions and scaled
  `x1,000,000`; this is a magnitude heuristic, not a structural signal
  (SEC's companyconcept/companyfacts API doesn't expose the XBRL
  `decimals` attribute that would otherwise reveal it), so a real
  company with a legitimately tiny share count would be scaled
  incorrectly. Visa is the one company checked with no fallback
  available either - no shares-outstanding-shaped tag exists under any
  namespace for it, a genuine data gap rather than a naming mismatch.
  See [DATA_SPIKE_NOTES.md](DATA_SPIKE_NOTES.md) V8.
- **IFRS/20-F support is verified against exactly one company (NVO,
  Novo Nordisk) - not assumed to generalize.** `IFRS_CONCEPT_CANDIDATES`
  has zero multi-candidate fallback chains (unlike the us-gaap side,
  where tag migrations are common and expected); a second IFRS filer
  using a different concept name for the same line item would just come
  back as a missing metric with a warning, not silently guessed at.
  Three US-GAAP-only selection strategies (short_term_debt's
  additive-tags handling, operating_income derivation,
  shares_outstanding's weighted-average fallback) were deliberately
  **not** ported to the IFRS path for the same reason - NVO tags all
  three directly so none of them were needed, and generalizing an
  unverified fallback risks the same kind of near-regression already
  caught once for a us-gaap company (finding #6, JPM). `capex` for IFRS
  filers is PP&E purchases only, same scope as the us-gaap capex
  candidates - NVO also reports a separate, sometimes-large
  `PurchaseOfIntangibleAssets` line (DKK 30bn in FY2025, 7x the prior
  year) that isn't included, since whether it's recurring reinvestment
  or a one-off (licensing, M&A) hasn't been verified. See
  [DATA_SPIKE_NOTES.md](DATA_SPIKE_NOTES.md) V9.
- **Currency conversion uses a live spot rate fetched at request time,
  not the rate on each fact's own filing date.** An IFRS filer's
  multi-year statements (e.g. FY2023-2025) are all converted at
  *today's* DKK->USD rate, not the rate that applied when each fact was
  actually reported - so year-over-year USD figures partly reflect FX
  movement, not just the underlying business. This is the same
  simplification real screener tools make by default, but it's not
  disclosed anywhere except the `warnings` field's conversion notice.
  Also depends on Yahoo Finance's FX-pair pricing (`"DKKUSD=X"`-style
  tickers via the same unofficial chart API `wacc.py`/`comps.py` already
  depend on for peer prices - see the Data layer section above) - no
  independent verification that every currency pair Yahoo could be
  asked for is actually priced there, only that DKK/USD is.

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
- **`discount_rate` must still be supplied - a reference WACC estimate
  exists but is never substituted for it.** `wacc_estimate` (opt-in via
  `compute_wacc`, see VALUATION_METHOD.md) has real limitations of its
  own:
  - **Beta is industry-average (bottom-up), not a per-company
    regression** - this project never fetches historical stock-price
    series, so no company's own volatility/systematic-risk is captured.
    A company with unusual company-specific risk (Boeing's 737 MAX
    crisis, say) gets its industry's average beta, not a beta reflecting
    its own actual trading behavior - Damodaran's stated reason for
    preferring bottom-up beta (regression betas are noisy) is real, but
    it's a real tradeoff, not a free upgrade.
  - **The SIC-to-industry mapping (`INDUSTRY_UNLEVERED_BETA` in
    wacc.py) is a coarse approximation covering ~35 SIC prefixes**, not
    an exhaustive crosswalk to Damodaran's ~90-industry table - a
    company whose SIC doesn't match any bucket gets a market-neutral
    beta of 1.0 (a documented placeholder, not a sourced figure).
  - **`equity_risk_premium` and the beta/synthetic-rating tables are
    static, dated constants** (see wacc.py's module docstring for exact
    source/date), not live-fetched - Damodaran publishes both as
    downloadable data, not a queryable API, so keeping these current
    needs a manual update, not a code change to use differently.
  - **Cost of debt needs `interest_expense`, a field with real,
    confirmed gaps**: Apple's FY2024/2025 10-Ks report no discrete
    interest-expense concept under any tag this project knows about -
    `wacc_estimate.cost_of_debt_aftertax`/`.wacc` come back `null` with
    a warning for exactly this reason, not a bug.
  - **Only `risk_free_rate` is genuinely live/external** (fetched from
    FRED each time `compute_wacc=True`) - everything else above is
    either SEC data or a documented constant.
  - **FRED's unauthenticated endpoint reproducibly fails from at least
    one real cloud IP range (GitHub Actions' hosted runners, confirmed
    2/2 on separate runs)**, while working normally from a residential
    connection and while Yahoo Finance (comps.py's price source) worked
    fine from the same blocked run - this looks like IP-range-based
    blocking specific to FRED, not a generic network issue. Any
    deployment on similar cloud/datacenter infrastructure could hit the
    same thing. `analyze()` falls back to `wacc.FALLBACK_RISK_FREE_RATE`
    (a dated constant, see that module) with an explicit warning rather
    than losing `wacc_estimate` entirely when this happens - but that
    means the risk-free rate silently used can be stale for as long as
    the deployment keeps hitting this block, not just for one request.
- **`fundamental_growth_estimate` (reinvestment rate x ROIC, see
  VALUATION_METHOD.md) is a reference figure only, and does not replace
  the required `fcff_growth_rate` input.** It inherits every limitation
  the FCFF calculation already has (EBIT approximated by operating
  income, non-cash NWC's short_term_debt-defaults-to-0 behavior) plus
  its own: it's purely backward-looking (a company's historical
  reinvestment/return pattern, not a forecast of what it will do), and
  a period before consistent XBRL tagging (see DATA_SPIKE_NOTES.md's
  AAPL FY2007-2009 observation) simply gets `growth_rate: null` for
  that year with a warning, not an estimate.
- **The market-value-equity fix for ROIC's near-zero-book-equity trap
  (see VALUATION_METHOD.md) only applies to the latest fiscal year.**
  Earlier years in `fundamental_growth_estimate.by_year` still use book
  `stockholders_equity`, since no historical price series is available
  to value them contemporaneously - `analyze()` only ever receives one
  market price, for "now." A company whose book equity has been near
  zero for multiple years (HD, BA) will still show a distorted ROIC for
  those older years, which drags down the 3-year-average
  `suggested_growth_rate` even though the latest year alone is now
  accurate. And if `shares_outstanding` is missing on the latest
  statement, this falls back to book equity silently reusing the same
  near-zero-denominator behavior it's meant to fix.
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

- **The target company's market price is a request input, never
  fetched.** `margin_of_safety` is always computed against whatever
  price the caller supplies - there is no live-quote integration for
  the company actually being valued (`comps_estimate`'s peer prices and
  `wacc_estimate`'s risk-free rate are fetched live, but only for those
  two reference-only estimates, never for `margin_of_safety` itself).
- **Look-ahead-bias filtering by `filed_date` is implemented but not
  backtested.** It has not been validated against a known historical
  scenario to confirm point-in-time results match what a real investor
  would have seen.

## Comps layer (reference only)

- **Peer lists are curated, hardcoded, and thin.** `INDUSTRY_PEERS` in
  `comps.py` covers ~30 SIC-prefix buckets with 2-6 large-cap peers
  each, not an exhaustive industry crosswalk - a company whose SIC
  doesn't match any bucket gets `peers: []` and an explicit warning, not
  a fallback peer group. Several buckets have only one or two peers
  (e.g. HD's only listed peer is Lowe's), so a computed median can rest
  on a single data point - flagged with a "low-confidence sample"
  warning, but not withheld.
- **Peers are not filtered by size, growth, or margin profile** - only
  by SIC-prefix bucket membership. A capital-light software peer and a
  hardware-heavy one can land in the same bucket with very different
  "normal" EV/Revenue multiples, and the median doesn't know the
  difference.
- **EBITDA is a proxy** (`operating_income + depreciation_amortization`),
  the same simplification and caveats as the EBIT proxy discussed under
  FCFF above - no adjustment for non-operating items inside
  `operating_income`.
- **Peer current price comes from an unofficial, undocumented API**
  (Yahoo Finance's chart endpoint, via
  `app/data/market_data.py::fetch_current_price()`) with no uptime or
  format-stability guarantee, unlike FRED (wacc_estimate) or SEC EDGAR
  (everything else in this app). A peer whose price can't be fetched is
  skipped with a named warning, not silently dropped, but the whole
  feature depends on this one non-guaranteed endpoint staying up.
- **No control-premium or minority-discount adjustment** - peer trading
  multiples reflect minority-stake, liquid-market prices; applying them
  to imply a value has the same limitation any comps analysis does
  (distinct from a precedent-transactions approach, which isn't
  implemented at all here).

## Owner Earnings layer

- **Maintenance capex is a bounded guess, not a measured figure** -
  Buffett's own 1986 letter says as much, and no XBRL tag separates
  maintenance from growth capex. The `full-capex`/`D&A-as-maintenance`
  bracket (see VALUATION_METHOD.md) is a reasonable range, not a proof
  that the true figure lies inside it - a company could plausibly need
  MORE maintenance capex than either bound implies (e.g. deferred
  maintenance catching up) or less (e.g. capex-light asset-light
  expansion that still counts as "growth").
- **Owner earnings inherits FCFF's non-cash-NWC definition and
  short_term_debt-defaults-to-0 behavior** (same `compute_operating_nwc()`
  call) - see the FCFF section's limitations above for what that does
  and doesn't cover.
- **Uses net income, not NOPAT** - so unlike FCFF, owner earnings is
  sensitive to interest expense, one-time tax items, and other
  below-the-operating-line noise that a firm-level measure would
  exclude. This is intentional (owner earnings is meant to reflect what
  flows to equity holders specifically), not an oversight, but it means
  a year with an unusual tax benefit or a debt refinancing charge shows
  up directly in the range in a way FCFF wouldn't.

## Valuation Consensus layer

- **Only spans whichever methods actually ran** - by default (no
  `compute_comps`), consensus is only ever DCF-FCFF ∩ Owner Earnings
  DCF; Comps only joins in when requested, and precedent-transactions
  analysis isn't implemented at all (see the Comps layer section
  above), so this is not the full three-to-four-method "football field"
  a real deal team would build.
- **No weighting between methods** - the intersection treats every
  available range as equally authoritative. A method resting on a
  single peer (see the Comps layer's thin-sample warning) counts the
  same as DCF's 3x3 sensitivity grid when computing the overlap.
- **"No overlap" is reported, not resolved** - when methods disagree
  entirely, `overlap_low`/`overlap_high` come back `null` with a
  warning naming the gap. This is deliberately not smoothed into "split
  the difference" or any other reconciliation; two independent methods
  landing in different neighborhoods is itself the finding.

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
- **`supporting_quote`/`grounding` are self-reported by the model, not
  independently verified.** The prompt instructs the model to copy a
  verbatim excerpt and mark it `explicit`/`inferred`, but nothing in
  `risk_extraction.py` checks that `supporting_quote` is actually a
  substring of the source text - a fabricated or paraphrased "verbatim"
  quote would not be caught. This narrows the failure mode from
  ungrounded claims to plausible-but-unverified ones; it doesn't
  eliminate it. See ARCHITECTURE.md's "Verification gates" section.
- **`cross_validate` is, in practice, closer to "Haiku result plus a
  flag" than genuine two-model agreement.** Live testing found
  `claude-sonnet-5` reproducibly fails (3/3 runs, two different
  companies) on the ~60-70K-token filings this project actually
  processes, while `claude-haiku-4-5` does not - see
  DATA_SPIKE_NOTES.md's "V2 — 논문 기반 개선" section. Requesting
  `cross_validate` still doubles the LLM cost even when only one
  model's result ends up usable.

## Scope, generally

- No portfolio management, trade execution, or stock screening/
  recommendation.
- No mobile or web UI - this is a JSON API only.
- Single-ticker analysis only; no batch or comparative endpoints.
