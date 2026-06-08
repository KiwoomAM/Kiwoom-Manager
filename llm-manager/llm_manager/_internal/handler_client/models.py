"""SDK Request/Response 모델.

Handler와 동일한 스키마를 독립적으로 정의한다.
"""

import base64 as _base64
import json as _json
import mimetypes as _mimetypes
from pathlib import Path as _Path
from typing import Any, Literal

import filetype as _filetype
from pydantic import BaseModel


def ts_to_wire(b: bytes | None) -> str | None:
    """Gemini Part.thought_signature (bytes) → wire (base64 str)."""
    return _base64.b64encode(b).decode() if b else None


def ts_from_wire(s: str | None) -> bytes | None:
    """wire (base64 str) → Gemini Part.thought_signature (bytes)."""
    return _base64.b64decode(s) if s else None


class TokenUsage(BaseModel):
    """토큰 사용량 — Gemini usage_metadata 와 1:1 매핑.

    각 필드 정의 (실험으로 확인된 실제 동작):
    - input_tokens (= prompt_token_count): 이번 호출의 입력 토큰 (cached 미포함)
    - output_tokens (= candidates_token_count): user-facing 답변 토큰
    - thinking_tokens (= thoughts_token_count): 내부 reasoning 토큰 (Gemini 2.5+)
    - tool_use_tokens (= tool_use_prompt_token_count): tool 정의가 차지한 prompt 토큰
    - cached_tokens (= cached_content_token_count): 사용된 캐시 객체의 총 크기

    cached_tokens 주의:
        Gemini docs 는 "이번 호출에 활용된 캐시 토큰" 이라고 설명하지만, 실험상 동일 캐시를
        쓰는 여러 호출에서 매번 같은 값(=cache 객체의 total_token_count)이 반환됨.
        즉 "이번 호출의 캐시 활용량" 이 아니라 "사용한 캐시 객체의 크기" 노출.

    검산: total_tokens = input + output + thinking + tool_use (cached 제외)
    실제 모델이 본 입력 ≈ input_tokens + (cached_tokens or 0)
    """

    input_tokens: int
    output_tokens: int
    total_tokens: int
    thinking_tokens: int | None = None
    cached_tokens: int | None = None
    tool_use_tokens: int | None = None


class FunctionCall(BaseModel):
    """Gemini 가 요청한 client-side 함수 호출."""

    name: str
    args: dict[str, Any] | None = None
    id: str | None = None
    thought_signature: str | None = None


class ToolCallInfo(BaseModel):
    """Gemini server-side tool 호출 흔적 (google_search 등).

    tool_type 은 google-genai SDK 의 `ToolCall.tool_type` 이 `Optional[ToolType]` 이라 None 가능.
    정상 응답에서는 항상 채워짐 — None 이 보이면 Gemini schema 위반 part 가 handler 에서 skip 된 흔적.
    """

    tool_type: str | None = None
    args: dict[str, Any] | None = None
    id: str | None = None
    thought_signature: str | None = None


class ToolResponseInfo(BaseModel):
    """Gemini server-side tool 응답. tool_type None 가능 — ToolCallInfo 와 동일 이유."""

    tool_type: str | None = None
    response: dict[str, Any] | None = None
    id: str | None = None
    thought_signature: str | None = None


class CodeBlock(BaseModel):
    """Gemini code_execution 이 생성한 코드. language None 가능 — Gemini schema 위반 시."""

    language: str | None = None
    code: str
    id: str | None = None
    thought_signature: str | None = None


class CodeResult(BaseModel):
    """Gemini code_execution 실행 결과. outcome None 가능 — Gemini schema 위반 시."""

    outcome: str | None = None
    output: str | None = None
    id: str | None = None
    thought_signature: str | None = None


class GroundingChunk(BaseModel):
    """google_search 인용 출처. uri 는 vertexai redirect (수시간~1일 ephemeral)."""

    title: str | None = None
    uri: str | None = None


class GroundingSegment(BaseModel):
    """citation 이 가리키는 답변 text 내 위치."""

    start_index: int | None = None
    end_index: int | None = None
    text: str | None = None


class GroundingSupport(BaseModel):
    """citation 매핑."""

    segment: GroundingSegment | None = None
    chunk_indices: list[int] = []


class Grounding(BaseModel):
    """google_search 결과 컨테이너. 응답 시점에만 의미 — Turn 2 입력에 echo 하지 않음."""

    web_search_queries: list[str] = []
    chunks: list[GroundingChunk] = []
    supports: list[GroundingSupport] = []


class LLMResponse(BaseModel):
    """LLM 호출 응답."""

    text: str | None = None
    model: str
    usage: TokenUsage | None = None
    finish_reason: str | None = None
    function_calls: list[FunctionCall] | None = None
    tool_calls: list[ToolCallInfo] | None = None
    tool_responses: list[ToolResponseInfo] | None = None
    code_blocks: list[CodeBlock] | None = None
    code_results: list[CodeResult] | None = None
    grounding: Grounding | None = None
    assistant_message: "Content | None" = None
    request_id: str

    def to_dict(self) -> dict[str, Any]:
        """JSON 텍스트를 dict로 파싱.

        Returns:
            파싱된 dict

        Raises:
            ValueError: text가 None이거나 유효한 JSON이 아닌 경우
        """
        if self.text is None:
            raise ValueError("text가 None입니다 (function_call 응답일 수 있음)")
        try:
            return _json.loads(self.text)
        except _json.JSONDecodeError as e:
            raise ValueError(f"JSON 파싱 실패: {e}") from e


class StreamChunk(BaseModel):
    """스트리밍 개별 청크."""

    text: str | None = None
    finish_reason: str | None = None
    usage: TokenUsage | None = None
    function_calls: list[FunctionCall] | None = None
    tool_calls: list[ToolCallInfo] | None = None
    tool_responses: list[ToolResponseInfo] | None = None
    code_blocks: list[CodeBlock] | None = None
    code_results: list[CodeResult] | None = None
    grounding: Grounding | None = None


class FileRef(BaseModel):
    """파일 참조."""

    name: str
    uri: str
    mime_type: str
    state: str
    display_name: str | None = None
    size_bytes: int | None = None
    expiration_time: str | None = None


class CacheRef(BaseModel):
    """캐시 참조."""

    name: str
    model: str
    display_name: str | None = None
    create_time: str | None = None
    expire_time: str | None = None
    update_time: str | None = None
    total_token_count: int | None = None


class FunctionResponse(BaseModel):
    """client-side 함수 실행 결과 (role='tool' 메시지의 part)."""

    name: str
    response: dict[str, Any]
    id: str | None = None


class FunctionCallPart(BaseModel):
    """모델이 요청한 client-side 함수 호출 (round-trip 시 history 보존용)."""

    name: str
    args: dict[str, Any] | None = None
    id: str | None = None
    thought_signature: str | None = None


class ToolCallPart(BaseModel):
    """Gemini server-side tool 호출 흔적 — round-trip 시 통째 보존 필수.

    tool_type None 인 part 는 handler 가 _convert_messages 단계에서 skip 하므로 round-trip 안 됨.
    None 으로 wire 에 실어 보내는 것 자체는 가능 (다운스트림 디버깅용).
    """

    tool_type: str | None = None
    args: dict[str, Any] | None = None
    id: str | None = None
    thought_signature: str | None = None


class ToolResponsePart(BaseModel):
    """Gemini server-side tool 응답 — tool_call.id 와 짝 보존. tool_type None 가능."""

    tool_type: str | None = None
    response: dict[str, Any] | None = None
    id: str | None = None
    thought_signature: str | None = None


class ExecutableCodePart(BaseModel):
    """Gemini code_execution 코드. language None 가능 — Gemini schema 위반 시."""

    language: str | None = None
    code: str
    id: str | None = None
    thought_signature: str | None = None


class CodeExecutionResultPart(BaseModel):
    """Gemini code_execution 실행 결과. outcome None 가능 — Gemini schema 위반 시."""

    outcome: str | None = None
    output: str | None = None
    id: str | None = None
    thought_signature: str | None = None


PartType = Literal[
    "text",
    "file",
    "inline",
    "function_response",
    "function_call",
    "tool_call",
    "tool_response",
    "executable_code",
    "code_execution_result",
]


class Part(BaseModel):
    """콘텐츠 파트 — Handler ContentPart 와 동일 wire 형식.

    type 별 활성 필드:
      - text: text (+ thought_signature)
      - file: file_uri + mime_type
      - inline: data (base64) + mime_type
      - function_response: function_response
      - function_call: function_call
      - tool_call: tool_call
      - tool_response: tool_response
      - executable_code: executable_code
      - code_execution_result: code_execution_result
    """

    type: PartType
    text: str | None = None
    file_uri: str | None = None
    mime_type: str | None = None
    data: str | None = None
    function_response: FunctionResponse | None = None
    function_call: FunctionCallPart | None = None
    tool_call: ToolCallPart | None = None
    tool_response: ToolResponsePart | None = None
    executable_code: ExecutableCodePart | None = None
    code_execution_result: CodeExecutionResultPart | None = None
    thought_signature: str | None = None

    @staticmethod
    def from_uri(
        file_uri: str,
        mime_type: str,
    ) -> "Part":
        """파일 URI 참조 파트 생성.

        Files API URI, 공개 HTTPS URL, pre-signed URL 모두 지원.

        Args:
            file_uri: 파일 URI (예: "files/xxx", "https://example.com/file.pdf")
            mime_type: MIME 타입 (필수 — URL에서 추론 불가)
        """
        return Part(type="file", file_uri=file_uri, mime_type=mime_type)

    @staticmethod
    def _detect_mp4_mime(data: bytes, detected: str) -> str:
        """MP4 컨테이너의 실제 MIME 타입을 판별.

        video/mp4로 감지된 경우, MP4 box 파싱으로 비디오 트랙 유무를 확인하여
        오디오 전용이면 audio/mp4로 보정한다.
        """
        if detected == "video/mp4":
            from ._common import _mp4_has_video_track

            if not _mp4_has_video_track(data):
                return "audio/mp4"
        return detected

    @staticmethod
    def from_bytes(
        data: bytes,
        mime_type: str | None = None,
    ) -> "Part":
        """인라인 바이너리 파트 생성.

        로컬 파일을 읽은 bytes, URL에서 다운로드한 bytes 등.
        내부적으로 base64 인코딩하여 HTTP 전송에 사용한다.
        mime_type 미지정 시 매직 바이트에서 자동 감지한다.

        Args:
            data: 파일 바이너리 데이터
            mime_type: MIME 타입 (미지정 시 자동 감지, 명시 시 감지값과 검증)

        Raises:
            SDKError: MIME 타입 감지 실패 또는 명시값과 감지값 불일치
        """
        from ._common import _resolve_mime_type

        guess = _filetype.guess(data)
        detected = guess.mime if guess else None
        if detected is not None:
            detected = Part._detect_mp4_mime(data, detected)
        resolved = _resolve_mime_type(mime_type, detected, "from_bytes")
        return Part(type="inline", data=_base64.b64encode(data).decode(), mime_type=resolved)

    @staticmethod
    def from_file(
        path: str,
        mime_type: str | None = None,
    ) -> "Part":
        """로컬 파일 경로에서 읽기 + MIME 자동 감지.

        확장자 기반(mimetypes)과 매직 바이트 기반(filetype) 이중 감지.
        mime_type 명시 시 감지값과 검증한다.

        Args:
            path: 로컬 파일 경로
            mime_type: MIME 타입 (미지정 시 자동 감지, 명시 시 감지값과 검증)

        Raises:
            SDKError: MIME 타입 감지 실패 또는 명시값과 감지값 불일치
        """
        from ._common import _resolve_mime_type

        file_path = _Path(path)
        data = file_path.read_bytes()

        # 확장자 기반 감지
        ext_mime, _ = _mimetypes.guess_type(str(file_path))
        # 매직 바이트 기반 감지
        guess = _filetype.guess(data)
        content_mime = guess.mime if guess else None

        detected = ext_mime or content_mime
        if detected is not None:
            detected = Part._detect_mp4_mime(data, detected)
        resolved = _resolve_mime_type(mime_type, detected, str(path))
        return Part(type="inline", data=_base64.b64encode(data).decode(), mime_type=resolved)


class EmbeddingStatistics(BaseModel):
    """임베딩 통계 (개별 텍스트별)."""

    token_count: int
    truncated: bool = False


class EmbeddingValue(BaseModel):
    """개별 임베딩 결과."""

    values: list[float]
    statistics: EmbeddingStatistics | None = None


class EmbedResponse(BaseModel):
    """임베딩 응답."""

    embeddings: list[EmbeddingValue]
    model: str
    request_id: str


class Content(BaseModel):
    """대화 메시지 (한 발화자의 발화) — handler GenerateRequest.messages 의 element.

    LLMResponse.assistant_message 가 이 형태로 반환되어 사용자가 다음 turn history 에
    그대로 echo 가능 (role + parts 통째 보존).
    """

    role: str
    content: str | list[Part]


# Service 측 parts.py 의 ContentPart 와 동일 명명 alias (= Part).
# 사용자가 from llm_handler import ContentPart 로 import 해도 동일하게 작동.
ContentPart = Part


LLMResponse.model_rebuild()
