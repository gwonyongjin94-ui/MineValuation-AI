import time
from typing import Self

import httpx

from app.config import get_settings
from app.data.http import fetch_json, fetch_text

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# SEC's fair-access policy caps automated access at 10 req/s; 0.2s keeps headroom
# below that cap instead of hugging the limit exactly.
DEFAULT_MIN_INTERVAL = 0.2


class SECClient:
    def __init__(
        self,
        user_agent: str,
        timeout: float = 10.0,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        client: httpx.Client | None = None,
    ):
        self._client = client or httpx.Client(headers={"User-Agent": user_agent}, timeout=timeout)
        self._min_interval = min_interval
        self._last_request_at: float | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get_submissions(self, cik: str) -> dict:
        return self._get(SUBMISSIONS_URL.format(cik=cik))

    def get_company_facts(self, cik: str) -> dict:
        return self._get(COMPANY_FACTS_URL.format(cik=cik))

    def get_document(self, url: str) -> str:
        """Fetch an arbitrary SEC document (e.g. a 10-K/10-Q filing) as raw text.

        Shares this client's throttle with get_submissions/get_company_facts -
        www.sec.gov and data.sec.gov both fall under SEC's one fair-access
        rate limit, not separate per-subdomain budgets.
        """
        self._throttle()
        return fetch_text(self._client, url)

    def _get(self, url: str) -> dict:
        self._throttle()
        return fetch_json(self._client, url)

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            wait = self._min_interval - (time.monotonic() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at = time.monotonic()


def build_default_client() -> SECClient:
    settings = get_settings()
    return SECClient(user_agent=settings.sec_user_agent)
