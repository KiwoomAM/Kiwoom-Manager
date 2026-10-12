"""테스트 파일들이 함께 쓰는 fixture."""

import functools
from collections.abc import Iterator

import httpx
import pytest

from tests._fakes import _FakeHandler, _reset_clients


@pytest.fixture
def handler(monkeypatch) -> Iterator[_FakeHandler]:
    """SDK 가 만드는 HTTP 클라이언트에 가짜 transport 만 끼운다."""
    fake = _FakeHandler()
    transport = httpx.MockTransport(fake)
    monkeypatch.setattr(
        httpx, "Client", functools.partial(httpx.Client, transport=transport)
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", functools.partial(httpx.AsyncClient, transport=transport)
    )
    _reset_clients()
    yield fake
    _reset_clients()
