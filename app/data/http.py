import httpx

from app.data.exceptions import (
    SECConnectionError,
    SECHTTPError,
    SECMalformedResponseError,
    SECNotFoundError,
    SECRateLimitError,
    SECTimeoutError,
)


def fetch_json(client: httpx.Client, url: str) -> dict:
    try:
        response = client.get(url)
    except httpx.TimeoutException as exc:
        raise SECTimeoutError(url) from exc
    except httpx.RequestError as exc:
        raise SECConnectionError(url) from exc

    if response.status_code == 404:
        raise SECNotFoundError(url)
    if response.status_code == 429:
        raise SECRateLimitError(url)
    if response.status_code >= 400:
        raise SECHTTPError(f"{response.status_code} for {url}")

    try:
        return response.json()
    except ValueError as exc:
        raise SECMalformedResponseError(url) from exc
