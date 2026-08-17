import json
from pathlib import Path

import httpx

from app.config import get_settings
from app.data.exceptions import UnknownTickerError
from app.data.http import fetch_json

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[2] / ".cache" / "company_tickers.json"


class TickerMap:
    def __init__(
        self,
        user_agent: str,
        cache_path: Path,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ):
        self._user_agent = user_agent
        self._cache_path = cache_path
        self._timeout = timeout
        self._client = client
        self._mapping: dict[str, str] | None = None

    def resolve(self, ticker: str) -> str:
        mapping = self._load()
        cik = mapping.get(ticker.upper())
        if cik is None:
            raise UnknownTickerError(ticker)
        return cik

    def _load(self) -> dict[str, str]:
        if self._mapping is not None:
            return self._mapping

        if self._cache_path.exists():
            raw = json.loads(self._cache_path.read_text())
        else:
            raw = self._fetch_and_cache()

        self._mapping = {
            entry["ticker"].upper(): str(entry["cik_str"]).zfill(10) for entry in raw.values()
        }
        return self._mapping

    def _fetch_and_cache(self) -> dict:
        client = self._client or httpx.Client(
            headers={"User-Agent": self._user_agent}, timeout=self._timeout
        )
        try:
            raw = fetch_json(client, TICKERS_URL)
        finally:
            if self._client is None:
                client.close()

        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_path.write_text(json.dumps(raw))
        return raw


def build_default_ticker_map() -> TickerMap:
    settings = get_settings()
    return TickerMap(user_agent=settings.sec_user_agent, cache_path=DEFAULT_CACHE_PATH)
