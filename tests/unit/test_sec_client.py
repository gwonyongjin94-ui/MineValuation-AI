import httpx
import pytest

from app.data.exceptions import (
    SECConnectionError,
    SECHTTPError,
    SECMalformedResponseError,
    SECNotFoundError,
    SECRateLimitError,
    SECTimeoutError,
)
from app.data.sec_client import SECClient


def _client(handler) -> SECClient:
    transport = httpx.MockTransport(handler)
    return SECClient(user_agent="test", min_interval=0, client=httpx.Client(transport=transport))


def test_get_company_facts_success():
    def handler(request):
        return httpx.Response(200, json={"cik": 320193, "facts": {}})

    result = _client(handler).get_company_facts("0000320193")

    assert result["cik"] == 320193


def test_404_raises_not_found():
    def handler(request):
        return httpx.Response(404)

    with pytest.raises(SECNotFoundError):
        _client(handler).get_company_facts("0000000000")


def test_429_raises_rate_limit():
    def handler(request):
        return httpx.Response(429)

    with pytest.raises(SECRateLimitError):
        _client(handler).get_submissions("0000320193")


def test_500_raises_http_error():
    def handler(request):
        return httpx.Response(500)

    with pytest.raises(SECHTTPError):
        _client(handler).get_submissions("0000320193")


def test_malformed_json_raises():
    def handler(request):
        return httpx.Response(200, content=b"not json")

    with pytest.raises(SECMalformedResponseError):
        _client(handler).get_submissions("0000320193")


def test_timeout_raises():
    def handler(request):
        raise httpx.TimeoutException("timed out", request=request)

    with pytest.raises(SECTimeoutError):
        _client(handler).get_submissions("0000320193")


def test_connection_error_raises():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(SECConnectionError):
        _client(handler).get_submissions("0000320193")
