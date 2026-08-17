"""V2 spike - observe what real LLM risk-extraction output looks like
before designing the qualitative-risk schema. Not part of the app.
"""

import anthropic

from app.config import get_settings
from app.data.filing_documents import fetch_filing_document, list_recent_filings
from app.data.sec_client import build_default_client
from app.data.ticker_map import build_default_ticker_map

MODEL = "claude-haiku-4-5-20251001"

PROMPT = """You are analyzing the Risk Factors and MD&A content of a company's 10-K filing.

Extract the most material, company-specific qualitative risks - not generic industry
boilerplate that would apply to almost any company in the sector. For each risk, give:
- a short label
- a 1-2 sentence explanation grounded in what the filing actually says (not general
  knowledge about the company)
- whether the filing's own language suggests this is new/emerging vs longstanding
- a severity judgment: low / medium / high

List at most 8 risks, ordered by materiality. Then give a 2-3 sentence overall summary
of the qualitative risk picture.

Filing text follows:
---
{text}
---
"""


def main() -> None:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set in .env")

    ticker_map = build_default_ticker_map()
    sec_client = build_default_client()
    cik = ticker_map.resolve("AAPL")
    submissions = sec_client.get_submissions(cik)
    filings = [f for f in list_recent_filings(submissions) if f.form == "10-K"]
    document = fetch_filing_document(sec_client, cik, filings[0])
    sec_client.close()

    print(f"filing text: {len(document.text)} chars (~{len(document.text) // 4} tokens)\n")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": PROMPT.format(text=document.text)}],
    )

    print(response.content[0].text)
    print()
    print("usage:", response.usage)


if __name__ == "__main__":
    main()
