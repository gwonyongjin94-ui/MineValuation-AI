# Data Model

This describes the schema in `app/data/models.py` and the selection
rules implemented in `app/financials/normalizer.py`. Every rule here
was decided from observing real SEC data first - see
[DATA_SPIKE_NOTES.md](DATA_SPIKE_NOTES.md) for the raw findings this
document is built on. Don't extend the tag lists below from memory;
check a live `companyfacts` response first, the way every addition so
far has been checked (see git history for `long_term_debt`,
`shares_outstanding`, `stockholders_equity`).

## SEC endpoints used

- `https://data.sec.gov/submissions/CIK{cik}.json` - company metadata,
  including `sic` / `sicDescription` (used for `valuation_category`)
- `https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json` - all
  non-custom-taxonomy XBRL facts for the company in one payload
- `https://www.sec.gov/files/company_tickers.json` - ticker -> CIK,
  fetched once and cached locally (`app/data/ticker_map.py`)

`companyfacts` aggregates only standard taxonomies (`us-gaap`, `dei`,
`ifrs-full`, `srt`) - a company-specific custom-extension tag for a
concept means that concept is simply absent from this API, not present
under an unfamiliar name. Custom-taxonomy parsing from raw filings is
explicitly out of V1 scope.

## `FinancialFact`

One selected XBRL fact, always carrying enough provenance to trace it
back to the exact filing it came from:

```
metric, value, unit, taxonomy, xbrl_tag,
period_start (None for instant/balance-sheet concepts),
period_end, fiscal_year, fiscal_period, form,
filed_date, accession_number, frame, source
```

`accession_number` and `filed_date` are not derived - they're already
present on every fact in the raw `companyfacts` payload (`accn` /
`filed`) and are simply carried through.

## `FinancialStatement`

Normalized annual financials for one company/fiscal year. Every metric
field is `FinancialFact | None` - a missing tag is `None` plus an entry
in `warnings`, never a silently-defaulted `0`.

| Field | Concept type | Candidate XBRL tags (priority order) |
|---|---|---|
| `revenue` | duration | `RevenueFromContractWithCustomerExcludingAssessedTax`, `RevenueFromContractWithCustomerIncludingAssessedTax`, `SalesRevenueNet`, `Revenues` |
| `operating_income` | duration | `OperatingIncomeLoss` |
| `net_income` | duration | `NetIncomeLoss` |
| `operating_cash_flow` | duration | `NetCashProvidedByUsedInOperatingActivities` |
| `depreciation_amortization` | duration | `DepreciationDepletionAndAmortization`, `DepreciationAmortizationAndAccretionNet`, `DepreciationAndAmortization`, `Depreciation` |
| `capex` | duration | `PaymentsToAcquirePropertyPlantAndEquipment`, `PaymentsForCapitalImprovements`, `PaymentsToAcquireProductiveAssets` |
| `current_assets` | instant | `AssetsCurrent` |
| `current_liabilities` | instant | `LiabilitiesCurrent` |
| `cash` | instant | `CashAndCashEquivalentsAtCarryingValue`, `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` |
| `short_term_debt` | instant | `ShortTermBorrowings`, `DebtCurrent`, `LongTermDebtCurrent` |
| `long_term_debt` | instant | `LongTermDebtNoncurrent` **only** |
| `stockholders_equity` | instant | `StockholdersEquity` |
| `shares_outstanding` | instant | `CommonStockSharesOutstanding` (us-gaap, not dei) |

Two entries above are deliberately single-tag with no fallback list:

- **`long_term_debt`**: `LongTermDebtNoncurrent` and `LongTermDebt` were
  both checked live against AAPL/GOOGL/MSFT and report *different
  values in the same filing* - `LongTermDebt` isn't just an alternate
  name for the same number. Treating them as interchangeable fallbacks
  would silently corrupt the figure, so only `LongTermDebtNoncurrent`
  is used.
- **`shares_outstanding`**: `dei:EntityCommonStockSharesOutstanding`
  exists on every 10-K but reports an "as of" cover-page date near the
  *filing* date, not the fiscal year end - it can't be matched by
  `end == period_end` like every other instant concept here.
  `us-gaap:CommonStockSharesOutstanding` was checked live instead and
  its `end` does align to the fiscal year end.

`restated_facts: list[FinancialFact]` holds any fact for the *same*
period found under a *later* accession number with a *different*
value - see the selection rules below. It is never used to silently
overwrite the as-reported fact.

## `CompanyInfo.valuation_category`

`STANDARD` / `FINANCIAL` / `UNSUPPORTED`, decided from `sic` (SIC code
6000-6799 -> `FINANCIAL`) in `normalizer.classify_company()`, before
any metric extraction runs. See ARCHITECTURE.md for why this ordering
matters.

## Fact selection rules (`app/financials/normalizer.py`)

A candidate fact must satisfy, in order:

1. `form in ("10-K", "10-K/A")` and `fp == "FY"`.
2. For **duration** concepts only: `start` present and
   `350 <= (end - start).days <= 380`. `fp == "FY"` alone is not
   enough - a 10-K/A can carry restated *quarterly* figures (`fp` in
   `Q1`-`Q4`) whose `end` date can coincide with a fiscal year end,
   which would otherwise leak into an "annual" selection.
3. `end == period_end` (the fiscal year being built).

Among all facts that pass those filters (across every candidate tag,
since a company can migrate tags between filings), the **as-reported**
fact is the one with the earliest `filed_date`. Any other matching fact
is added to `restated_facts` only if its `value` actually differs -
otherwise it's just a later filing re-showing the same period as a
comparative, not a restatement.

Fiscal years are discovered (not assumed) by unioning the annual
`period_end` dates found under `revenue` and `net_income`'s candidate
tags - the two concepts virtually every 10-K filer reports under a
standard tag.

## V2: filing document text (`app/data/filing_documents.py`)

Separate from `companyfacts` - this is the raw 10-K/10-Q document
itself (inline-XBRL HTML), fetched from
`https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{primaryDocument}`
(the `primaryDocument` filename comes from `submissions.filings.recent`).

**No section-level extraction** (e.g. "just Risk Factors"). Checked live
against two real 10-Ks first: AAPL's filer template has a hyperlinked
table of contents mapping each Item to an anchor id, so slicing between
the Item 1A and Item 1B anchors cleanly isolates Risk Factors - but
MSFT's filer template has no such links at all, and "Item 1A" appears
21 times as plain repeated text with nothing to anchor a slice to.
Filer-generated HTML structure varies too much between vendors to build
a reliable parser without a much bigger investment (see
DATA_SPIKE_NOTES.md's V2 section). `clean_filing_html()` strips
script/style tags and the hidden inline-XBRL metadata block
(`display:none`) and returns the whole document as plain text instead -
the qualitative layer's job is deciding what part of that text matters,
not this layer's.

Cleaned text size for real filings: AAPL's latest 10-K is ~208k chars
(~52k tokens); MSFT's is ~336k chars (~84k tokens) - both fit
comfortably in an LLM context window without chunking.

## Known data-model limitations

See [LIMITATIONS.md](LIMITATIONS.md) for the full list; the ones most
relevant to this schema specifically:

- No income-tax-expense or pretax-income fields, so effective/marginal
  tax rate can't be derived from the data (see VALUATION_METHOD.md).
- `EBIT` is approximated by GAAP `operating_income` - they can differ
  when a company reports non-operating items inside operating income.
- Tag fallback lists reflect what AAPL/GOOGL/MSFT/JPM/HBB report today;
  a company using an unlisted tag will simply show up as a missing
  metric with a warning, not an error.
