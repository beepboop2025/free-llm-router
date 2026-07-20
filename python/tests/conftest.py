"""Shared fixtures: a fake monotonic clock and an in-memory HTTP stub.

Everything here is pure / in-process — no network, no API keys, no sleeping.
The router injects `monotonic` for its rate limiter and circuit breaker, and
exposes an overridable `_http()` returning an httpx-like client, so we can drive
the whole failover loop deterministically.
"""

from __future__ import annotations

from typing import Callable, List

import pytest


class FakeClock:
    """A controllable monotonic clock. `advance()` moves time forward."""

    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


class FakeResponse:
    """Minimal stand-in for httpx.Response."""

    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            request = httpx.Request("POST", "http://test/chat/completions")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=request, response=response
            )


class FakeHTTPClient:
    """Replaces httpx.AsyncClient. Each post() pops the next scripted handler.

    A handler is either a FakeResponse (returned) or an Exception (raised),
    letting tests script per-provider success / failure sequences.
    """

    def __init__(self, handlers: List) -> None:
        self._handlers = list(handlers)
        self.calls: List[dict] = []
        self.is_closed = False

    async def post(self, url: str, *, json: dict, headers: dict):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if not self._handlers:
            raise AssertionError("FakeHTTPClient: more requests than scripted handlers")
        handler = self._handlers.pop(0)
        if isinstance(handler, Exception):
            raise handler
        return handler

    async def aclose(self) -> None:
        self.is_closed = True


def ok_payload(*, content: str = "hello", model: str = "m", total: int = 7) -> dict:
    return {
        "model": model,
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": total},
    }


@pytest.fixture
def make_http() -> Callable[[List], FakeHTTPClient]:
    def _make(handlers: List) -> FakeHTTPClient:
        return FakeHTTPClient(handlers)

    return _make
