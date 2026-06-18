# -*- coding: utf-8 -*-
"""redis_manager/aio/backend.py

redis-handler HTTP 서버를 경유하는 async KeyValueBackend 구현.
sync HandlerBackend의 async 미러 — 공용 _http 헬퍼로 예외 매핑/경로 인코딩 공유.

NOTE: `from __future__ import annotations`는 클래스 내부 `set()` 메서드와
`set[str]` 타입 힌트의 이름 충돌을 회피하기 위해 필요.
"""

from __future__ import annotations

import os
from typing import Any, AsyncIterator, Optional

import httpx

from .. import _http
from .._circuit import CircuitBreaker
from ..config import RedisConfig
from ..exceptions import RedisHandlerError, RedisUnavailableError


class AsyncHandlerBackend:
    """KeyValueBackend의 redis-handler HTTP async 구현."""

    name = "redis"

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        # client 미주입 시 첫 요청 시점에 lazy 생성 — import 후
        # RedisConfig.configure(handler_url=...) 호출을 존중하기 위함.
        self._http: Optional[httpx.AsyncClient] = client
        self._breaker = CircuitBreaker()

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._http is None:
            headers = {}
            service_name = os.getenv("SERVICE_NAME")
            if service_name:
                headers["X-Caller-Service"] = service_name
            self._http = httpx.AsyncClient(
                base_url=RedisConfig.HANDLER_URL,
                timeout=RedisConfig.HANDLER_TIMEOUT,
                headers=headers,
            )
        return self._http

    async def aclose(self) -> None:
        """클라이언트 종료. 다음 요청 시 현재 RedisConfig로 재생성된다."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ── HTTP 공통 ─────────────────────────────────────
    async def _request(self, method: str, path: str, *, params: dict | None = None,
                       json: dict | None = None, breaker: bool = True) -> Any:
        if breaker and not self._breaker.allow():
            raise RedisUnavailableError(
                "redis-handler 서킷 open — 최근 연속 미도달로 HTTP 시도 생략"
            )
        try:
            resp = await self._ensure_client().request(method, path, params=params, json=json)
        except httpx.TransportError as e:
            if breaker:
                self._breaker.record_failure()
            raise RedisUnavailableError(f"redis-handler 연결 실패: {e}") from e
        except httpx.HTTPError as e:
            raise RedisHandlerError(f"redis-handler 요청 실패: {e}") from e

        # 응답 수신 = handler 도달 — 503/4xx여도 브레이커 관점에선 성공
        if breaker:
            self._breaker.record_success()
        _http.raise_for_handler_status(resp)
        try:
            return resp.json()
        except ValueError as e:
            raise RedisHandlerError(f"redis-handler 응답 파싱 실패 (비JSON 본문): {e}") from e

    # ── String ────────────────────────────────────────
    async def get(self, key: str) -> Optional[str]:
        return (await self._request("GET", f"/v1/keys/{_http.key_path(key)}"))["value"]

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: Optional[int] = None,
        nx: bool = False,
        xx: bool = False,
        keepttl: bool = False,
    ) -> bool:
        body: dict[str, Any] = {"value": value, "nx": nx, "xx": xx, "keepttl": keepttl}
        if ex is not None and ex > 0:
            body["ex"] = ex
        data = await self._request("POST", f"/v1/keys/{_http.key_path(key)}/set", json=body)
        return bool(data["ok"])

    async def delete(self, key: str) -> int:
        return (await self._request("DELETE", f"/v1/keys/{_http.key_path(key)}"))["deleted"]

    async def compare_and_delete(self, key: str, expected: str) -> bool:
        data = await self._request(
            "POST", f"/v1/keys/{_http.key_path(key)}/compare-and-delete",
            json={"expected": expected},
        )
        return bool(data["ok"])

    async def compare_and_set(self, key: str, expected: str, value: str, *, ex: int) -> bool:
        data = await self._request(
            "POST", f"/v1/keys/{_http.key_path(key)}/compare-and-set",
            json={"expected": expected, "value": value, "ex": ex},
        )
        return bool(data["ok"])

    # ── Key ───────────────────────────────────────────
    async def exists(self, key: str) -> bool:
        data = await self._request("GET", f"/v1/keys/{_http.key_path(key)}/exists")
        return bool(data["exists"])

    async def ttl(self, key: str) -> int:
        return (await self._request("GET", f"/v1/keys/{_http.key_path(key)}/ttl"))["ttl"]

    async def expire(self, key: str, seconds: int) -> bool:
        data = await self._request(
            "POST", f"/v1/keys/{_http.key_path(key)}/expire", json={"seconds": seconds}
        )
        return bool(data["ok"])

    async def scan_iter(self, match: str, count: int = 100) -> AsyncIterator[str]:
        """Cursor 페이지네이션 기반 SCAN. cursor=0 반환 시 종료."""
        cursor = 0
        while True:
            data = await self._request(
                "GET", "/v1/keys",
                params={"match": match, "cursor": cursor, "count": count},
            )
            for key in data["keys"]:
                yield key
            cursor = data["cursor"]
            if cursor == 0:
                return

    # ── Set ───────────────────────────────────────────
    async def sadd(self, key: str, *members: str) -> int:
        data = await self._request(
            "POST", f"/v1/sets/{_http.key_path(key)}/add", json={"members": list(members)}
        )
        return data["count"]

    async def smembers(self, key: str) -> set[str]:
        data = await self._request("GET", f"/v1/sets/{_http.key_path(key)}")
        return set(data["members"])

    async def srem(self, key: str, *members: str) -> int:
        data = await self._request(
            "POST", f"/v1/sets/{_http.key_path(key)}/remove", json={"members": list(members)}
        )
        return data["count"]

    # ── Health ────────────────────────────────────────
    async def health(self) -> dict:
        """handler /v1/admin/health 원본 응답 (live PING 결과). 브레이커 미적용 — 진단은 항상 live."""
        return await self._request("GET", "/v1/admin/health", breaker=False)

    async def ping(self) -> bool:
        """live 연결 확인. handler 미도달/Redis 다운 모두 False."""
        try:
            return (await self.health()).get("status") == "connected"
        except RedisHandlerError:
            return False


__all__ = ["AsyncHandlerBackend"]
