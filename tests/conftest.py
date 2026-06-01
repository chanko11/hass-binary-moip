"""Shared test fixtures for the no-Home-Assistant (api.py) test suite.

These tests exercise ``custom_components.binary_moip.api`` directly. That module
depends only on ``aiohttp`` + stdlib, so it imports without a Home Assistant
install. We drive it with a fake ``aiohttp.ClientSession`` that records calls and
returns canned responses, rather than hitting a real controller.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest
from aiohttp import ClientResponseError

# Import api.py by file path so the test suite does not require the
# `homeassistant` package (a plain `import custom_components.binary_moip.api`
# would execute the package __init__, which pulls in homeassistant).
_API_PATH = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "binary_moip"
    / "api.py"
)
_spec = importlib.util.spec_from_file_location("binary_moip_api", _API_PATH)
api = importlib.util.module_from_spec(_spec)
sys.modules["binary_moip_api"] = api
_spec.loader.exec_module(api)


class FakeResponse:
    """Minimal stand-in for an aiohttp response used as an async context manager."""

    def __init__(
        self,
        *,
        status: int = 200,
        json_data: Any = None,
        content_length: int | None = None,
    ) -> None:
        self.status = status
        self._json = json_data
        # The client treats content_length == 0 (or 204) as an empty body. When
        # not given, infer: a body iff json_data was provided.
        if content_length is not None:
            self.content_length = content_length
        else:
            self.content_length = 0 if json_data is None else 64

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        if self.status >= 400:
            # request_info must expose .real_url — the client stringifies the
            # error when wrapping it.
            request_info = SimpleNamespace(real_url="https://ctrl.local:443/")
            raise ClientResponseError(
                request_info, (), status=self.status, message=f"HTTP {self.status}"
            )

    async def json(self) -> Any:
        return self._json


class FakeSession:
    """Fake aiohttp ClientSession recording calls and returning canned responses.

    ``post_handler`` / ``request_handler`` are callables that return a
    :class:`FakeResponse` (or raise to simulate a transport error). They receive
    the call kwargs so tests can branch on path/payload.
    """

    def __init__(
        self,
        *,
        post_handler: Callable[..., FakeResponse] | None = None,
        request_handler: Callable[..., FakeResponse] | None = None,
        ws: Any = None,
    ) -> None:
        self._post_handler = post_handler
        self._request_handler = request_handler
        self._ws = ws
        self.post_calls: list[dict[str, Any]] = []
        self.request_calls: list[dict[str, Any]] = []
        self.ws_calls: list[dict[str, Any]] = []

    def post(self, url, *, json=None, ssl=None, timeout=None):  # noqa: A002
        self.post_calls.append({"url": url, "json": json, "ssl": ssl})
        return self._post_handler(url=url, json=json)

    def request(self, method, url, *, json=None, headers=None, ssl=None, timeout=None):  # noqa: A002
        self.request_calls.append(
            {"method": method, "url": url, "json": json, "headers": headers}
        )
        return self._request_handler(
            method=method, url=url, json=json, headers=headers
        )

    async def ws_connect(self, url, *, protocols=None, ssl=None, heartbeat=None):
        self.ws_calls.append(
            {"url": url, "protocols": protocols, "ssl": ssl, "heartbeat": heartbeat}
        )
        return self._ws


@pytest.fixture
def make_client():
    """Factory: build a BinaryMoIPClient over a given FakeSession."""

    def _make(session: FakeSession, **overrides: Any) -> Any:
        kwargs: dict[str, Any] = {
            "host": "ctrl.local",
            "port": 443,
            "username": "admin",
            "password": "secret",
        }
        kwargs.update(overrides)
        return api.BinaryMoIPClient(session, kwargs.pop("host"), **kwargs)

    return _make
