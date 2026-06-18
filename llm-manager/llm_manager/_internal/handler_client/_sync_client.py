"""SyncClient — 동기 HTTP 클라이언트 (독립 구현, async 래핑 아님)."""

import json
import mimetypes
from collections.abc import Iterator

import httpx

from ._common import (
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


class SyncClient:
    """Handler 서비스와 통신하는 동기 클라이언트."""

    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout)

    @property
    def _last_done_payload(self) -> dict[str, object] | None:
        """직전 stream() 호출의 SSE done event payload (read-only 호환 layer).

        실제 저장소는 ContextVar (`_done_payload_var`) — task/thread 별 격리되어
        동일 client 위 동시 stream 호출의 race 가 구조적으로 불가능. None 이면 done 미수신
        또는 다른 context 에서 호출됨. 내부 stream 구현이 `_done_payload_var.set()` 으로만 갱신.
        """
        return _done_payload_var.get()

    def close(self) -> None:
        """HTTP 클라이언트 종료."""
        self._http.close()

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        """공통 HTTP 요청 처리."""
        try:
            response = self._http.request(method, path, **kwargs)  # type: ignore[arg-type]
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

    def _parse_sse(self, response: httpx.Response) -> Iterator[StreamChunk]:
        """SSE 응답을 StreamChunk로 파싱."""
        _done_payload_var.set(None)
        for line in response.iter_lines():
            try:
                chunk = parse_sse_line(line)
            except StopIteration as e:
                value = e.value
                if isinstance(value, dict):
                    _done_payload_var.set(value)
                break
            if chunk is not None:
                yield chunk

    def call(
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

        Args:
            contents: 한 user 발화의 multipart 내용. messages 와 mutually exclusive.
            messages: 전체 대화 (list[Content]). contents/history 와 mutually exclusive.
            system: 시스템 지시사항
            model: 사용할 모델명
            temperature: 생성 온도
            max_tokens: 최대 토큰 수
            seed: 재현성을 위한 시드값
            output: 출력 형식 ("json", "enum" 등)
            schema: Pydantic 모델, Enum 클래스, 또는 JSON Schema dict
            thinking: 사고 모드
            tools: 도구 정의 목록
            tool_config: Function Calling 모드 ("auto", "any", "none", "validated")
            history: 이전 대화 이력 (contents 와만 결합)
            cached_content: Context Cache 이름
            task_name: 호출(task) 단위 라벨 (한 서비스 안에서 어떤 호출인지 구분)
            service_name: 실행 서비스 식별자 (env SERVICE_NAME)
            media_resolution: 미디어 해상도 ("low", "medium", "high")

        Returns:
            LLM 응답
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
        resp = self._request("POST", "/v1/generate", json=body)
        return LLMResponse.model_validate(resp.json())

    def stream(
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
    ) -> Iterator[StreamChunk]:
        """통합 스트리밍 응답 (SSE 파싱).

        호출 형태는 call() 과 동일 — contents 또는 messages mutually exclusive.

        Args:
            contents: 한 user 발화의 multipart 내용. messages 와 mutually exclusive.
            messages: 전체 대화 (list[Content]). contents/history 와 mutually exclusive.
            system: 시스템 지시사항
            model: 사용할 모델명
            temperature: 생성 온도
            max_tokens: 최대 토큰 수
            seed: 재현성을 위한 시드값
            output: 출력 형식 ("json", "enum" 등)
            schema: Pydantic 모델, Enum 클래스, 또는 JSON Schema dict
            thinking: 사고 모드
            tools: 도구 정의 목록
            tool_config: Function Calling 모드 ("auto", "any", "none", "validated")
            history: 이전 대화 이력 (contents 와만 결합)
            cached_content: Context Cache 이름
            task_name: 호출(task) 단위 라벨 (한 서비스 안에서 어떤 호출인지 구분)
            service_name: 실행 서비스 식별자 (env SERVICE_NAME)
            media_resolution: 미디어 해상도 ("low", "medium", "high")

        Yields:
            스트리밍 청크
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
        try:
            with self._http.stream("POST", "/v1/stream", json=body) as response:
                if response.status_code >= 400:
                    raw_body = b""
                    for raw_chunk in response.iter_bytes():
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

                yield from self._parse_sse(response)
        except httpx.ConnectError as e:
            raise HandlerConnectionError(f"Handler 연결 실패: {e}") from e

    def embed(
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
        """텍스트 임베딩 생성.

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
        resp = self._request("POST", "/v1/embed", json=body)
        return EmbedResponse.model_validate(resp.json())

    def upload_file(self, file_path: str, display_name: str | None = None, mime_type: str | None = None) -> FileRef:
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
            resp = self._request("POST", "/v1/files", files=files, data=data)
        return FileRef.model_validate(resp.json())

    def create_cache(
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
        resp = self._request("POST", "/v1/caches", json=body)
        return CacheRef.model_validate(resp.json())

    def delete_cache(self, name: str) -> None:
        """Context Cache 삭제."""
        self._request("DELETE", f"/v1/caches/{name}")

    def list_caches(self) -> list[CacheRef]:
        """캐시 목록 조회."""
        resp = self._request("GET", "/v1/caches")
        return [CacheRef.model_validate(c) for c in resp.json()]

    def get_cache(self, name: str) -> CacheRef:
        """캐시 상세 조회."""
        resp = self._request("GET", f"/v1/caches/{name}")
        return CacheRef.model_validate(resp.json())

    def update_cache(self, name: str, ttl: int) -> CacheRef:
        """캐시 TTL 업데이트."""
        resp = self._request("PATCH", f"/v1/caches/{name}", json={"ttl": ttl})
        return CacheRef.model_validate(resp.json())

    def delete_file(self, name: str) -> None:
        """파일 삭제 (404는 무시 — 멱등성)."""
        try:
            self._request("DELETE", f"/v1/files/{name}")
        except HandlerError as e:
            if e.status_code == 404:
                return
            raise

    def get_file(self, name: str) -> FileRef | None:
        """파일 조회. 존재하지 않으면 None 반환."""
        try:
            resp = self._request("GET", f"/v1/files/{name}")
        except HandlerError as e:
            if e.status_code == 404:
                return None
            raise
        return FileRef.model_validate(resp.json())

    def list_files(self) -> list[FileRef]:
        """파일 목록 조회."""
        resp = self._request("GET", "/v1/files")
        return [FileRef.model_validate(f) for f in resp.json()]
