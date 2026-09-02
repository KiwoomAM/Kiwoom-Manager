"""_common — Sync/Async 클라이언트 및 세션이 공유하는 유틸리티.

공유 항목:
- build_body(): SDK 파라미터 → Handler HTTP body 변환
- parse_sse_line(): SSE 라인 파싱
- is_valid_response(): 응답 유효성 판정 (세션 이력 관리용)
- VALID_FINISH_REASONS: 유효 finish_reason 집합
- _done_payload_var: stream() 종료 시 SSE done event payload 를 호출 스코프 (task/thread) 에
  격리해 저장하는 ContextVar — 동일 client 위 동시 stream 호출 race 차단용.
"""

import json
import logging
import struct
from contextvars import ContextVar
from enum import Enum

from pydantic import BaseModel as PydanticBaseModel

from ._config import get_settings
from .exceptions import SDKError, from_error_type
from .models import Content, FileRef, LLMResponse, Part, StreamChunk

logger = logging.getLogger(__name__)

# httpx keep-alive idle 소켓 만료 (초). 서버(llm-handler) uvicorn 의
# timeout-keep-alive(기본 5s) 보다 짧게 두어, 클라이언트가 서버 마감 전에 idle 소켓을
# 폐기하도록 한다 → 서버가 닫은 소켓을 재사용해 RemoteProtocolError 가 나는 race 차단.
# (SyncClient/AsyncClient 양쪽이 import — 값의 SSOT)
KEEPALIVE_EXPIRY = 2.0

# stream() done event payload — task/thread 단위 격리.
# AsyncClient/SyncClient 의 stream() 이 set, Session/AsyncSession 이 `client._last_done_payload`
# property (호환 layer) 또는 직접 get 으로 read. 동일 client 인스턴스 위 동시 stream 호출이
# 있어도 task/thread 별 context 가 자동 분리되어 서로의 payload 를 덮어쓰지 않는다.
_done_payload_var: ContextVar[dict[str, object] | None] = ContextVar("llm_handler_done_payload", default=None)

# ── 타입 별칭 ──

ContentsType = str | list[str | FileRef | Part]


# 스트림 청크에 흩어져 오는 candidate-level 필드를 누적 규칙별로 나눈 분류 (SSOT).
# "어느 필드가 이어붙이기고 어느 것이 갈아끼우기인가" 의 단일 출처 — 아래
# extend_stream_field docstring 이 그 규칙을 설명하고, 이 두 튜플이 대상을 정한다.
# 누적하는 쪽(runner._StreamMeta)과 이벤트로 내보내는 쪽(llm_manager._events)이 함께 쓴다.
STREAM_EXTEND_FIELDS = (
    "function_calls",
    "tool_calls",
    "tool_responses",
    "code_blocks",
    "code_results",
)
STREAM_REPLACE_FIELDS = ("grounding", "url_context")


def extend_stream_field(dst: list | None, src: list | None) -> list | None:
    """스트림 청크마다 흩어져 오는 리스트 필드를 앞엣것을 버리지 않고 이어붙인다.

    parallel function call N개가 N개 청크로(1개씩) 흩어져 오는 등(llm-handler 계약: "client 누적은
    extend 필수"), function_calls·tool_calls·tool_responses·code_blocks·code_results 는 청크별로
    자기 몫만 실려 오므로 덮어쓰면 마지막 청크 것만 남는다. grounding·url_context 처럼 턴당 한 번
    오는 단발성 컨테이너에는 쓰지 않는다(그건 마지막 non-None 으로 덮어씀).
    """
    if src is None:
        return dst
    return src if dst is None else dst + src


# ── MIME 타입 감지/검증 ──


def _normalize_mime_type(mime: str) -> str:
    """감지된 MIME 타입을 Gemini API 공식 포맷으로 정규화."""

    return get_settings().gemini_mime_normalize.get(mime, mime)


def _mp4_has_video_track(data: bytes) -> bool:
    """MP4 컨테이너에 비디오 트랙이 있는지 확인.

    moov → trak → mdia → hdlr box를 순회하여
    handler_type이 'vide'인 트랙이 있으면 True.
    """

    def _iter_boxes(data: bytes, start: int, end: int):
        """주어진 범위에서 MP4 box를 순회."""
        offset = start
        while offset + 8 <= end:
            size = struct.unpack(">I", data[offset : offset + 4])[0]
            box_type = data[offset + 4 : offset + 8]
            if size == 1 and offset + 16 <= end:
                size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
                header_size = 16
            elif size == 0:
                size = end - offset
                header_size = 8
            else:
                header_size = 8
            if size < header_size:
                break
            yield box_type, offset + header_size, offset + size
            offset += size

    for box_type, start, end in _iter_boxes(data, 0, len(data)):
        if box_type == b"moov":
            for trak_type, trak_s, trak_e in _iter_boxes(data, start, end):
                if trak_type == b"trak":
                    for mdia_type, mdia_s, mdia_e in _iter_boxes(data, trak_s, trak_e):
                        if mdia_type == b"mdia":
                            for hdlr_type, hdlr_s, hdlr_e in _iter_boxes(data, mdia_s, mdia_e):
                                if hdlr_type == b"hdlr" and hdlr_s + 12 <= hdlr_e:
                                    # hdlr: version(4) + pre_defined(4) + handler_type(4)
                                    handler = data[hdlr_s + 8 : hdlr_s + 12]
                                    if handler == b"vide":
                                        return True
            return False
    return False


def _resolve_mime_type(
    explicit: str | None,
    detected: str | None,
    source_hint: str = "",
) -> str:
    """명시값 우선으로 Gemini 호환 MIME 타입을 반환.

    명시값이 있으면 항상 우선한다. 감지값과 불일치 시 warning 로그를 남기되
    명시값을 사용한다 (사용자가 의도적으로 지정한 것으로 간주).

    Args:
        explicit: 사용자가 명시한 MIME 타입 (None이면 미지정)
        detected: 자동 감지된 MIME 타입 (None이면 감지 실패)
        source_hint: 에러 메시지에 포함할 출처 정보 (파일 경로 등)

    Returns:
        Gemini API 호환 MIME 타입 문자열

    Raises:
        SDKError: 명시값도 감지값도 없는 경우
    """
    norm_explicit = _normalize_mime_type(explicit) if explicit is not None else None
    norm_detected = _normalize_mime_type(detected) if detected is not None else None

    if norm_explicit is not None:
        if norm_detected is not None and norm_explicit != norm_detected:
            hint = f" ({source_hint})" if source_hint else ""
            logger.warning("MIME 타입 불일치: 지정=%s, 감지=%s%s — 명시값을 사용합니다", explicit, detected, hint)
        return norm_explicit
    if norm_detected is not None:
        return norm_detected
    raise SDKError(
        "MIME 타입을 감지할 수 없습니다. mime_type을 명시해주세요." + (f" ({source_hint})" if source_hint else "")
    )


# ── 세션 히스토리 관련 ──

VALID_FINISH_REASONS = frozenset({"STOP", "MAX_TOKENS"})


def is_valid_response(response: LLMResponse) -> bool:
    """응답이 curated history에 포함될 자격이 있는지 판정.

    유효 조건:
    1. 의미있는 part 가 하나라도 있으면 유효:
       function_calls / tool_calls / tool_responses / code_blocks / code_results
       — Gemini 3 server-side tool / code execution 단독 turn 도 history 에 포함되어야
       thought_signature round-trip 이 끊기지 않음.
    2. 위가 없으면 text 가 비어있지 않고 공백만이 아니어야 함
    3. finish_reason 이 STOP 또는 MAX_TOKENS
    """
    if (
        response.function_calls
        or response.tool_calls
        or response.tool_responses
        or response.code_blocks
        or response.code_results
    ):
        return True
    if not response.text or not response.text.strip():
        return False
    if response.finish_reason not in VALID_FINISH_REASONS:
        return False
    return True


# ── SSE 파싱 ──


def parse_sse_line(line: str) -> StreamChunk | None:
    """SSE 라인 하나를 파싱하여 StreamChunk를 반환.

    Args:
        line: SSE 이벤트 라인 (strip 전 원본)

    Returns:
        파싱된 StreamChunk, 또는 무시할 라인이면 None.

    Raises:
        HandlerError: 서버에서 에러 이벤트를 보낸 경우.
        StopIteration: done 이벤트를 받은 경우 — `.value` 에 done event 의 full payload dict 가 담김
            (`assistant_message` 등 부가 필드 추출용). 기존 호출 코드는 value 무시해도 정상.
    """
    line = line.strip()
    if not line or line.startswith(":"):
        return None

    if line.startswith("data: "):
        data_str = line[6:]
    elif line.startswith("data:"):
        data_str = line[5:]
    else:
        return None

    try:
        data = json.loads(data_str)
    except json.JSONDecodeError:
        return None

    if "event" in data and data["event"] == "done":
        raise StopIteration(data)

    if "error" in data:
        raise from_error_type(
            data["error"],
            data.get("status_code", 500),
            data.get("error_type"),
            data.get("detail"),
        )

    return StreamChunk.model_validate(data)


# ── Contents 정규화 ──


def _normalize_contents(contents: ContentsType) -> str | list[dict]:
    """contents를 HTTP body에 넣을 수 있는 형태로 정규화.

    Args:
        contents: 사용자 입력 (텍스트, 또는 텍스트/파일/Part 혼합 리스트)

    Returns:
        문자열 (단순 텍스트) 또는 ContentPart dict 리스트 (멀티파트)
    """
    if isinstance(contents, str):
        return contents

    parts: list[dict] = []
    for item in contents:
        if isinstance(item, str):
            parts.append({"type": "text", "text": item})
        elif isinstance(item, FileRef):
            parts.append({"type": "file", "file_uri": item.uri, "mime_type": item.mime_type})
        elif isinstance(item, Part):
            parts.append(item.model_dump(exclude_none=True))
        else:
            raise SDKError(f"지원하지 않는 콘텐츠 타입: {type(item)}")
    return parts


# ── HTTP Body 구성 ──


def build_body(
    contents: ContentsType | None = None,
    *,
    messages: list[Content] | None = None,
    system: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    seed: int | None = None,
    output: str | None = None,
    schema: type[PydanticBaseModel] | type[Enum] | dict | None = None,
    thinking: str | None = None,
    tools: list[dict] | None = None,
    tool_config: str | None = None,
    history: list[dict] | None = None,
    cached_content: str | None = None,
    task_name: str | None = None,
    service_name: str | None = None,
    media_resolution: str | None = None,
) -> dict:
    """SDK 파라미터 → Handler HTTP body 변환.

    호출 형태 (mutually exclusive):
        1. contents=...               — 이번 새 user 발화 (history 와 결합 가능)
        2. contents=..., history=...  — user 발화 + 이전 dict 형식 history
        3. messages=[Content, ...]    — 전체 대화 명시 (advanced, type-safe)

    Args:
        contents: 한 user 발화의 multipart 내용. messages 와 mutually exclusive.
        messages: 전체 대화 (list[Content]). contents/history 와 mutually exclusive.
        system: 시스템 지시사항
        model: 사용할 모델명
        temperature: 생성 온도
        max_tokens: 최대 토큰 수
        seed: 재현성을 위한 시드값
        output: 출력 형식 ("json", "enum" 등)
        schema: Pydantic 모델, Enum 클래스, 또는 JSON Schema dict (자동으로 output 추론)
        thinking: 사고 모드 ("high" 등)
        tools: 도구 정의 목록
        tool_config: function calling 모드 ("auto", "any", "none", "validated")
        history: 이전 대화 이력 (contents 와만 결합)
        cached_content: Context Cache 이름
        task_name: 호출(task) 단위 라벨 (한 서비스 안에서 어떤 호출인지 구분)
        service_name: 실행 서비스 식별자 (env SERVICE_NAME). 호출 측에서 주입.
        media_resolution: 미디어 해상도 ("low", "medium", "high")

    Returns:
        Handler HTTP body dict

    Raises:
        ValueError: contents/messages 둘 다 지정/미지정, 또는 messages 와 history 동시 지정
    """
    if (contents is None) == (messages is None):
        raise ValueError("build_body requires exactly one of: contents, messages")
    if messages is not None and history is not None:
        raise ValueError("messages and history are mutually exclusive (messages already represents full conversation)")

    body: dict[str, object] = {}

    # messages 구성 — messages 명시 path 또는 contents+history path
    if messages is not None:
        body["messages"] = [m.model_dump(exclude_none=True) for m in messages]
    else:
        assert contents is not None  # 위 검증으로 보장됨
        msgs: list[dict] = []
        if history:
            msgs.extend(history)
        normalized = _normalize_contents(contents)
        msgs.append({"role": "user", "content": normalized})
        body["messages"] = msgs

    if system is not None:
        body["system_instruction"] = system
    if model is not None:
        body["model"] = model
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if seed is not None:
        body["seed"] = seed
    if cached_content is not None:
        body["cached_content"] = cached_content
    if task_name is not None:
        body["task_name"] = task_name
    if service_name is not None:
        body["service_name"] = service_name
    if tools is not None:
        body["tools"] = tools
    if tool_config is not None:
        body["tool_config"] = tool_config
    if thinking is not None:
        body["thinking"] = thinking
    if media_resolution is not None:
        body["media_resolution"] = media_resolution

    # schema → output 자동 추론 + response_schema 변환
    if schema is not None:
        if isinstance(schema, dict):
            if output is None:
                output = "json"
            body["response_schema"] = schema
        elif issubclass(schema, Enum):
            if output is None:
                output = "enum"
            body["response_schema"] = {"type": "STRING", "enum": [e.value for e in schema]}
        elif issubclass(schema, PydanticBaseModel):
            if output is None:
                output = "json"
            body["response_schema"] = schema.model_json_schema()

    if output is not None:
        body["output"] = output

    return body


def build_cache_body(
    contents: ContentsType,
    *,
    model: str,
    system: str | None = None,
    display_name: str | None = None,
    ttl: int = 3600,
    tools: list[dict] | None = None,
    tool_config: str | None = None,
) -> dict:
    """SDK cache 파라미터 → Handler HTTP body 변환.

    Args:
        contents: 캐싱할 콘텐츠 (텍스트, 또는 텍스트/파일/Part 혼합 리스트)
        model: 사용할 모델명
        system: 시스템 지시사항
        display_name: 캐시 표시 이름
        ttl: TTL (초)
        tools: 도구 정의 목록 (캐시 사용 시 자동 적용)
        tool_config: function calling 모드 ("auto", "any", "none")

    Returns:
        Handler HTTP body dict
    """
    normalized = _normalize_contents(contents)
    body: dict[str, object] = {
        "model": model,
        "contents": [{"role": "user", "content": normalized}],
        "ttl": ttl,
    }
    if system is not None:
        body["system_instruction"] = system
    if display_name is not None:
        body["display_name"] = display_name
    if tools is not None:
        body["tools"] = tools
    if tool_config is not None:
        body["tool_config"] = tool_config
    return body


def build_embed_body(
    contents: str | list[str],
    *,
    model: str | None = None,
    task_type: str | None = None,
    title: str | None = None,
    output_dimensionality: int | None = None,
    task_name: str | None = None,
    service_name: str | None = None,
) -> dict:
    """SDK embed 파라미터 → Handler HTTP body 변환.

    Args:
        contents: 임베딩할 텍스트 (단일 문자열 또는 배치 리스트)
        model: 임베딩 모델명
        task_type: 임베딩 작업 유형
        title: 문서 제목 (RETRIEVAL_DOCUMENT 전용)
        output_dimensionality: 출력 벡터 차원 수
        task_name: 호출(task) 단위 라벨 (한 서비스 안에서 어떤 호출인지 구분)
        service_name: 실행 서비스 식별자 (env SERVICE_NAME). 호출 측에서 주입.

    Returns:
        Handler HTTP body dict
    """
    body: dict[str, object] = {"contents": contents}

    if model is not None:
        body["model"] = model
    if task_type is not None:
        body["task_type"] = task_type
    if title is not None:
        body["title"] = title
    if output_dimensionality is not None:
        body["output_dimensionality"] = output_dimensionality
    if task_name is not None:
        body["task_name"] = task_name
    if service_name is not None:
        body["service_name"] = service_name

    return body
