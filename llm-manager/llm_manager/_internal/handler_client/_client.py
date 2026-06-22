"""AsyncClient — 비동기 HTTP 클라이언트."""

import json
import mimetypes
from collections.abc import AsyncIterator

import httpx

from ._common import (
    KEEPALIVE_EXPIRY,
    ContentsType,
    _done_payload_var,
    _resolve_mime_type,
    build_body,
    build_cache_body,
    build_embed_body,
    parse_sse_line,
)
from .exceptions import HandlerConnectionError, HandlerError, from_error_type
from .models import CacheRef, Content, EmbedResponse, FileRef, LLMResponse, StreamChunk


class AsyncClient:
    """Handler 서비스와 통신하는 비동기 클라이언트."""

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        # keepalive_expiry 를 서버(uvicorn timeout-keep-alive 기본 5s) 보다 짧게 두어
        # idle 소켓을 서버 마감 전에 폐기 → stale 소켓 재사용(RemoteProtocolError) race 차단.
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            limits=httpx.Limits(keepalive_expiry=KEEPALIVE_EXPIRY),
        )

    @property
    def _last_done_payload(self) -> dict[str, object] | None:
        """직전 stream() 호출의 SSE done event payload (read-only 호환 layer).

        실제 저장소는 ContextVar (`_done_payload_var`) — task/thread 별 격리되어
        동일 client 위 동시 stream 호출의 race 가 구조적으로 불가능. None 이면 done 미수신
        또는 다른 context 에서 호출됨. 내부 stream 구현이 `_done_payload_var.set()` 으로만 갱신.
        """
        return _done_payload_var.get()

    async def close(self) -> None:
        """HTTP 클라이언트 종료."""
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        """공통 HTTP 요청 처리."""
        try:
            response = await self._http.request(method, path, **kwargs)  # type: ignore[arg-type]
        except httpx.RemoteProtocolError as e:
            # stale keep-alive 소켓: 서버가 응답 없이 끊음 = 요청 미처리 → 새 소켓으로 1회 재시도.
            # 단, multipart 파일 업로드는 파일 핸들이 소진되어 재전송 불가 → 재시도 제외.
            if "files" in kwargs:
                raise HandlerConnectionError(f"Handler 연결 실패: {e}") from e
            try:
                response = await self._http.request(method, path, **kwargs)  # type: ignore[arg-type]
            except (httpx.RemoteProtocolError, httpx.ConnectError) as e2:
                raise HandlerConnectionError(f"Handler 연결 실패 (재시도 후): {e2}") from e2
        except httpx.ConnectError as e:
            raise HandlerConnectionError(f"Handler 연결 실패: {e}") from e

        if response.status_code >= 400:
            detail = None
            try:
                body = response.json()
                error_msg = body.get("error", response.text)
                error_type = body.get("error_type")
                detail = body.get("detail")
            except Exception:
                error_msg = response.text
                error_type = None
            raise from_error_type(error_msg, response.status_code, error_type, detail)

        return response

    async def call(
        self,
        contents: ContentsType | None = None,
        *,
        messages: list[Content] | None = None,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        seed: int | None = None,
        output: str | None = None,
        schema: type | dict | None = None,
        thinking: str | None = None,
        tools: list[dict] | None = None,
        tool_config: str | None = None,
        history: list[dict] | None = None,
        cached_content: str | None = None,
        task_name: str | None = None,
        service_name: str | None = None,
        media_resolution: str | None = None,
    ) -> LLMResponse:
        """통합 단일 응답 생성.

        호출 형태 (mutually exclusive):
            1. call(contents=...)               — 이번 새 user 발화 (history 와 결합 가능)
            2. call(contents=..., history=...)  — user 발화 + 이전 dict 형식 history
            3. call(messages=[Content, ...])    — 전체 대화 명시 (advanced, type-safe)
        """
        body = build_body(
            contents,
            messages=messages,
            system=system,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            seed=seed,
            output=output,
            schema=schema,
            thinking=thinking,
            tools=tools,
            tool_config=tool_config,
            history=history,
            cached_content=cached_content,
            task_name=task_name,
            service_name=service_name,
            media_resolution=media_resolution,
        )
        resp = await self._request("POST", "/v1/generate", json=body)
        return LLMResponse.model_validate(resp.json())

    async def stream(
        self,
        contents: ContentsType | None = None,
        *,
        messages: list[Content] | None = None,
        system: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        seed: int | None = None,
        output: str | None = None,
        schema: type | dict | None = None,
        thinking: str | None = None,
        tools: list[dict] | None = None,
        tool_config: str | None = None,
        history: list[dict] | None = None,
        cached_content: str | None = None,
        task_name: str | None = None,
        service_name: str | None = None,
        media_resolution: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """통합 스트리밍 응답 (SSE 파싱).

        호출 형태는 call() 과 동일 — contents 또는 messages mutually exclusive.
        """
        body = build_body(
            contents,
            messages=messages,
            system=system,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            seed=seed,
            output=output,
            schema=schema,
            thinking=thinking,
            tools=tools,
            tool_config=tool_config,
            history=history,
            cached_content=cached_content,
            task_name=task_name,
            service_name=service_name,
            media_resolution=media_resolution,
        )
        _done_payload_var.set(None)
        try:
            async with self._http.stream("POST", "/v1/stream", json=body) as response:
                if response.status_code >= 400:
                    raw_body = b""
                    async for raw_chunk in response.aiter_bytes():
                        raw_body += raw_chunk
                    error_type = None
                    detail = None
                    try:
                        error_data = json.loads(raw_body)
                        error_msg = error_data.get("error", raw_body.decode())
                        error_type = error_data.get("error_type")
                        detail = error_data.get("detail")
                    except Exception:
                        error_msg = raw_body.decode()
                    raise from_error_type(error_msg, response.status_code, error_type, detail)

                async for line in response.aiter_lines():
                    try:
                        chunk = parse_sse_line(line)
                    except StopIteration as e:
                        value = e.value
                        if isinstance(value, dict):
                            _done_payload_var.set(value)
                        break
                    if chunk is not None:
                        yield chunk
        except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
            # 스트림은 중간까지 받다 끊기면 부분 출력이 생겨 재시도가 위험 → wrap 만.
            raise HandlerConnectionError(f"Handler 연결 실패: {e}") from e

    async def embed(
        self,
        contents: str | list[str],
        *,
        model: str | None = None,
        task_type: str | None = None,
        title: str | None = None,
        output_dimensionality: int | None = None,
        task_name: str | None = None,
        service_name: str | None = None,
    ) -> EmbedResponse:
        """텍스트 임베딩 생성 (비동기).

        Args:
            contents: 임베딩할 텍스트 (단일 문자열 또는 배치 리스트)
            model: 임베딩 모델명 (기본: gemini-embedding-001)
            task_type: 임베딩 작업 유형 (예: "RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT")
            title: 문서 제목 (task_type="RETRIEVAL_DOCUMENT"일 때만)
            output_dimensionality: 출력 벡터 차원 수 (128-3072)
            task_name: 호출(task) 단위 라벨 (한 서비스 안에서 어떤 호출인지 구분)
            service_name: 실행 서비스 식별자 (env SERVICE_NAME)

        Returns:
            임베딩 응답
        """
        body = build_embed_body(
            contents,
            model=model,
            task_type=task_type,
            title=title,
            output_dimensionality=output_dimensionality,
            task_name=task_name,
            service_name=service_name,
        )
        resp = await self._request("POST", "/v1/embed", json=body)
        return EmbedResponse.model_validate(resp.json())

    async def upload_file(
        self, file_path: str, display_name: str | None = None, mime_type: str | None = None
    ) -> FileRef:
        """파일 업로드.

        Args:
            file_path: 업로드할 로컬 파일 경로
            display_name: Gemini에서 표시할 이름 (미지정 시 파일명 사용)
            mime_type: MIME 타입 (미지정 시 파일 확장자에서 자동 감지)

        Raises:
            SDKError: MIME 타입 감지 실패 또는 명시값과 감지값 불일치
        """
        filename = file_path.split("/")[-1]
        detected, _ = mimetypes.guess_type(file_path)
        resolved = _resolve_mime_type(mime_type, detected, file_path)
        with open(file_path, "rb") as f:
            files = {"file": (filename, f, resolved)}
            data = {"display_name": display_name} if display_name else {}
            resp = await self._request("POST", "/v1/files", files=files, data=data)
        return FileRef.model_validate(resp.json())

    async def create_cache(
        self,
        contents: ContentsType,
        *,
        model: str,
        system: str | None = None,
        display_name: str | None = None,
        ttl: int = 3600,
        tools: list[dict] | None = None,
        tool_config: str | None = None,
    ) -> CacheRef:
        """Context Cache 생성.

        Args:
            contents: 캐싱할 콘텐츠 (텍스트, 또는 텍스트/파일/Part 혼합 리스트)
            model: 사용할 모델명
            system: 시스템 지시사항
            display_name: 캐시 표시 이름 (최대 128자)
            ttl: TTL 초 단위 (기본 1시간)
            tools: 캐시 사용 시 자동 적용할 도구 정의
            tool_config: function calling 모드 ("auto", "any", "none")
        """
        body = build_cache_body(
            contents,
            model=model,
            system=system,
            display_name=display_name,
            ttl=ttl,
            tools=tools,
            tool_config=tool_config,
        )
        resp = await self._request("POST", "/v1/caches", json=body)
        return CacheRef.model_validate(resp.json())

    async def delete_cache(self, name: str) -> None:
        """Context Cache 삭제."""
        await self._request("DELETE", f"/v1/caches/{name}")

    async def list_caches(self) -> list[CacheRef]:
        """캐시 목록 조회."""
        resp = await self._request("GET", "/v1/caches")
        return [CacheRef.model_validate(c) for c in resp.json()]

    async def get_cache(self, name: str) -> CacheRef:
        """캐시 상세 조회."""
        resp = await self._request("GET", f"/v1/caches/{name}")
        return CacheRef.model_validate(resp.json())

    async def update_cache(self, name: str, ttl: int) -> CacheRef:
        """캐시 TTL 업데이트."""
        resp = await self._request("PATCH", f"/v1/caches/{name}", json={"ttl": ttl})
        return CacheRef.model_validate(resp.json())

    async def delete_file(self, name: str) -> None:
        """파일 삭제 (404는 무시 — 멱등성)."""
        try:
            await self._request("DELETE", f"/v1/files/{name}")
        except HandlerError as e:
            if e.status_code == 404:
                return
            raise

    async def get_file(self, name: str) -> FileRef | None:
        """파일 조회. 존재하지 않으면 None 반환."""
        try:
            resp = await self._request("GET", f"/v1/files/{name}")
        except HandlerError as e:
            if e.status_code == 404:
                return None
            raise
        return FileRef.model_validate(resp.json())

    async def list_files(self) -> list[FileRef]:
        """파일 목록 조회."""
        resp = await self._request("GET", "/v1/files")
        return [FileRef.model_validate(f) for f in resp.json()]
