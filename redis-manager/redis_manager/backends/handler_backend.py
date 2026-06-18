# -*- coding: utf-8 -*-
"""redis_manager/backends/handler_backend.py

redis-handler HTTP 서버를 경유하는 KeyValueBackend 구현 (v3).

예외 매핑:
    httpx.TransportError → RedisUnavailableError (handler 미도달)
    HTTP 503             → RedisRetryExhausted  (handler가 Redis 재시도 소진 보고)
    그 외 4xx/5xx        → RedisHandlerError

셋 모두 RedisHandlerError 계열이라 RedisManager의 일반 명령 폴백 분기
(except RedisHandlerError)가 v2와 동일하게 동작한다.

NOTE: `from __future__ import annotations`는 클래스 내부 `set()` 메서드와
`set[str]` 타입 힌트의 이름 충돌을 회피하기 위해 필요.
"""

from __future__ import annotations

import os
from typing import Any, Iterator, Optional

import httpx

from .. import _http
from .._circuit import CircuitBreaker
from ..config import RedisConfig
from ..exceptions import (
    RedisHandlerError,
    RedisUnavailableError,
)


class HandlerBackend:
    """KeyValueBackend Protocol의 redis-handler HTTP 구현."""

    name = "redis"

    def __init__(self, client: Optional[httpx.Client] = None):
        # client 미주입 시 첫 요청 시점에 lazy 생성 — import 후
        # RedisConfig.configure(handler_url=...) 호출을 존중하기 위함.
        self._http: Optional[httpx.Client] = client
        self._breaker = CircuitBreaker()

    def _ensure_client(self) -> httpx.Client:
        if self._http is None:
            headers = {}
            service_name = os.getenv("SERVICE_NAME")
            if service_name:
                headers["X-Caller-Service"] = service_name
            self._http = httpx.Client(
                base_url=RedisConfig.HANDLER_URL,
                timeout=RedisConfig.HANDLER_TIMEOUT,
                headers=headers,
            )
        return self._http

    def close(self) -> None:
        """클라이언트 종료. 다음 요청 시 현재 RedisConfig로 재생성된다."""
        if self._http is not None:
            self._http.close()
            self._http = None

    # ── HTTP 공통 ─────────────────────────────────────
    def _request(self, method: str, path: str, *, params: dict | None = None,
                 json: dict | None = None, breaker: bool = True) -> Any:
        if breaker and not self._breaker.allow():
            raise RedisUnavailableError(
                "redis-handler 서킷 open — 최근 연속 미도달로 HTTP 시도 생략"
            )
        try:
            resp = self._ensure_client().request(method, path, params=params, json=json)
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
    def get(self, key: str) -> Optional[str]:
        return self._request("GET", f"/v1/keys/{_http.key_path(key)}")["value"]

    def set(
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
        data = self._request("POST", f"/v1/keys/{_http.key_path(key)}/set", json=body)
        return bool(data["ok"])

    def delete(self, key: str) -> int:
        return self._request("DELETE", f"/v1/keys/{_http.key_path(key)}")["deleted"]

    def compare_and_delete(self, key: str, expected: str) -> bool:
        data = self._request(
            "POST", f"/v1/keys/{_http.key_path(key)}/compare-and-delete",
            json={"expected": expected},
        )
        return bool(data["ok"])

    def compare_and_set(self, key: str, expected: str, value: str, *, ex: int) -> bool:
        data = self._request(
            "POST", f"/v1/keys/{_http.key_path(key)}/compare-and-set",
            json={"expected": expected, "value": value, "ex": ex},
        )
        return bool(data["ok"])

    # ── Key ───────────────────────────────────────────
    def exists(self, key: str) -> bool:
        return bool(self._request("GET", f"/v1/keys/{_http.key_path(key)}/exists")["exists"])

    def ttl(self, key: str) -> int:
        return self._request("GET", f"/v1/keys/{_http.key_path(key)}/ttl")["ttl"]

    def expire(self, key: str, seconds: int) -> bool:
        data = self._request(
            "POST", f"/v1/keys/{_http.key_path(key)}/expire", json={"seconds": seconds}
        )
        return bool(data["ok"])

    def scan_iter(self, match: str, count: int = 100) -> Iterator[str]:
        """Cursor 페이지네이션 기반 SCAN. cursor=0 반환 시 종료."""
        cursor = 0
        while True:
            data = self._request(
                "GET", "/v1/keys",
                params={"match": match, "cursor": cursor, "count": count},
            )
            yield from data["keys"]
            cursor = data["cursor"]
            if cursor == 0:
                return

    # ── Set ───────────────────────────────────────────
    def sadd(self, key: str, *members: str) -> int:
        data = self._request(
            "POST", f"/v1/sets/{_http.key_path(key)}/add", json={"members": list(members)}
        )
        return data["count"]

    def smembers(self, key: str) -> set[str]:
        return set(self._request("GET", f"/v1/sets/{_http.key_path(key)}")["members"])

    def srem(self, key: str, *members: str) -> int:
        data = self._request(
            "POST", f"/v1/sets/{_http.key_path(key)}/remove", json={"members": list(members)}
        )
        return data["count"]

    # ── Health ────────────────────────────────────────
    def health(self) -> dict:
        """handler /v1/admin/health 원본 응답 (live PING 결과). 브레이커 미적용 — 진단은 항상 live."""
        return self._request("GET", "/v1/admin/health", breaker=False)

    def ping(self) -> bool:
        """live 연결 확인. handler 미도달/Redis 다운 모두 False."""
        try:
            return self.health().get("status") == "connected"
        except RedisHandlerError:
            return False


__all__ = ["HandlerBackend"]
