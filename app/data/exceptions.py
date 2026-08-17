class SECClientError(Exception):
    """Base class for all SEC data-layer errors."""


class UnknownTickerError(SECClientError):
    """Ticker not found in the local ticker-to-CIK cache."""


class SECNotFoundError(SECClientError):
    """SEC returned 404 for a known CIK."""


class SECRateLimitError(SECClientError):
    """SEC returned 429 (rate limit exceeded)."""


class SECTimeoutError(SECClientError):
    """Request to SEC timed out."""


class SECConnectionError(SECClientError):
    """Network-level failure reaching SEC."""


class SECHTTPError(SECClientError):
    """Unexpected HTTP error status from SEC."""


class SECMalformedResponseError(SECClientError):
    """SEC response body was not valid JSON."""
