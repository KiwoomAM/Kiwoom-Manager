"""전송 계층 Protocol — 컬렉션 래퍼의 DIP 경계.

구체 구현(`SyncClient`, `AsyncClient`)은 이 Protocol을 구조적으로 만족한다.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SyncTransport(Protocol):
    """동기 HTTP 전송."""

    def get(self, path: str, **kwargs: Any) -> Any: ...
    def post(self, path: str, **kwargs: Any) -> Any: ...
    def delete(self, path: str, **kwargs: Any) -> Any: ...


@runtime_checkable
class AsyncTransport(Protocol):
    """비동기 HTTP 전송."""

    async def get(self, path: str, **kwargs: Any) -> Any: ...
    async def post(self, path: str, **kwargs: Any) -> Any: ...
    async def delete(self, path: str, **kwargs: Any) -> Any: ...
