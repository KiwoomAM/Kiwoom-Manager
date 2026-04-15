"""AsyncClient — llm-request Service 통신."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from llm_request._file_meta import FileMeta
from llm_request.exceptions import ServiceConnectionError, ServiceError
from llm_request.models import DoneCheckResult, RequestResult


class AsyncClient:
    """llm-request Service와 통신하는 비동기 클라이언트."""

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def close(self) -> None:
        """HTTP 클라이언트 종료."""
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """공통 HTTP 요청 처리."""
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.ConnectError as e:
            raise ServiceConnectionError(f"llm-request 서비스 연결 실패: {e}") from e
        except httpx.TimeoutException as e:
            raise ServiceConnectionError(f"llm-request 서비스 요청 타임아웃: {e}") from e

        if response.status_code >= 400:
            try:
                body = response.json()
                error_msg = body.get("error", response.text)
                error_type = body.get("error_type")
            except Exception:
                error_msg = response.text
                error_type = None
            raise ServiceError(error_msg, status_code=response.status_code, error_type=error_type)

        return response

    async def request(
        self,
        llm_input: dict,
        *,
        file_metas: list[FileMeta] | None = None,
        cache_mode: bool = True,
        save_mode: bool = True,
        ttl_seconds: int | None = None,
        debug: bool = False,
        database: str | None = None,
        context_cache_ttl: int | None = None,
    ) -> RequestResult:
        """POST /v1/request — LLM 요청."""
        body: dict[str, Any] = {
            "llm_input": llm_input,
            "cache_mode": cache_mode,
            "save_mode": save_mode,
        }
        if file_metas:
            body["file_metas"] = [m.model_dump() for m in file_metas]
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        if debug:
            body["debug"] = debug
        if database is not None:
            body["database"] = database
        if context_cache_ttl is not None:
            body["context_cache_ttl"] = context_cache_ttl

        resp = await self._request("POST", "/v1/request", json=body)
        return resp.json()

    async def request_stream(
        self,
        llm_input: dict,
        *,
        file_metas: list[FileMeta] | None = None,
        cache_mode: bool = True,
        save_mode: bool = True,
        ttl_seconds: int | None = None,
        debug: bool = False,
        database: str | None = None,
        context_cache_ttl: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """POST /v1/request/stream — SSE 스트리밍 요청."""
        body: dict[str, Any] = {
            "llm_input": llm_input,
            "cache_mode": cache_mode,
            "save_mode": save_mode,
        }
        if file_metas:
            body["file_metas"] = [m.model_dump() for m in file_metas]
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        if debug:
            body["debug"] = debug
        if database is not None:
            body["database"] = database
        if context_cache_ttl is not None:
            body["context_cache_ttl"] = context_cache_ttl

        try:
            async with self._http.stream("POST", "/v1/request/stream", json=body) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    try:
                        err_body = resp.json()
                        raise ServiceError(
                            err_body.get("error", resp.text),
                            status_code=resp.status_code,
                            error_type=err_body.get("error_type"),
                        )
                    except ServiceError:
                        raise
                    except Exception:
                        raise ServiceError(resp.text, status_code=resp.status_code)

                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        yield json.loads(data)
        except httpx.ConnectError as e:
            raise ServiceConnectionError(f"llm-request 서비스 연결 실패: {e}") from e
        except httpx.TimeoutException as e:
            raise ServiceConnectionError(f"llm-request 서비스 요청 타임아웃: {e}") from e

    async def done_check(
        self,
        llm_input: dict,
        *,
        file_metas: list[FileMeta] | None = None,
        database: str | None = None,
    ) -> DoneCheckResult:
        """POST /v1/done-check — MDB 캐시(LLM 응답) 존재 확인."""
        body: dict[str, Any] = {"llm_input": llm_input}
        if file_metas:
            body["file_metas"] = [m.model_dump() for m in file_metas]
        if database is not None:
            body["database"] = database

        resp = await self._request("POST", "/v1/done-check", json=body)
        return resp.json()

    async def fetch(
        self,
        cache_key: str,
        *,
        database: str | None = None,
    ) -> RequestResult:
        """POST /v1/fetch — 캐시 키로 직접 조회."""
        body: dict[str, Any] = {"cache_key": cache_key}
        if database is not None:
            body["database"] = database

        resp = await self._request("POST", "/v1/fetch", json=body)
        return resp.json()
