"""챗봇용 대화 함수 `llm_chat_async` · `llm_chat_stream_async` — docs/38.

첨부는 `messages` 안 발화에 `StoredFile` 로 들어오고, 매니저가 파일마다 Files API 주소로 풀어
원래 위치에 파일 조각으로 넣는다. 캐시·저장·meta 는 없다.
"""
from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError as PydanticValidationError

import llm_manager
from llm_manager import aio as public_aio
from llm_manager._internal.handler_client import aio as handler_aio
from llm_manager._internal.handler_client.models import (
    Content,
    FunctionCallPart,
    FunctionResponse,
    Part,
)
from llm_manager.models import ChatMessage, StoredFile


def _sf(file_name: str = "a.pdf", size: int = 100) -> StoredFile:
    return StoredFile(
        file_path=f"path/{file_name}",
        file_name=file_name,
        file_size=size,
        file_mtime=1700000000,
        mime_type="application/pdf",
        extension=".pdf",
    )


# ── ChatMessage (§3.2) ─────────────────────────────────────────


class TestChatMessage:
    def test_accepts_stored_file_and_non_file_parts(self):
        msg = ChatMessage(role="user", content=[
            _sf(),
            Part(type="text", text="요약해줘"),
            Part(type="function_call", function_call=FunctionCallPart(name="search")),
            Part(type="function_response",
                 function_response=FunctionResponse(name="search", response={"hits": 0})),
        ])

        assert isinstance(msg.content[0], StoredFile)
        assert [p.type for p in msg.content[1:]] == ["text", "function_call", "function_response"]

    @pytest.mark.parametrize("part", [
        Part(type="inline", data="AAAA", mime_type="application/pdf"),
        Part.from_uri("files/abc", "application/pdf"),
    ], ids=["inline", "file"])
    def test_rejects_file_parts(self, part):
        """첨부는 StoredFile 한 가지다 — 바이트·Files API 주소 조각을 직접 넣으면 막는다."""
        with pytest.raises(PydanticValidationError):
            ChatMessage(role="user", content=[part])

    @pytest.mark.parametrize("role", ["assistant", "tool", "system"])
    def test_rejects_role_other_than_user_or_model(self, role):
        """Gemini 역할 이름만 받는다 — assistant→model 변환은 소비자 몫이다."""
        with pytest.raises(PydanticValidationError):
            ChatMessage(role=role, content=[Part(type="text", text="x")])


# ── 공개 표면 (§3.1 · §3.4) ─────────────────────────────────────

# 밑단 호출에는 있지만 대화 함수가 받지 않는 인자. 대화 전체는 messages 로만 받고,
# 캐시는 두지 않으며, service_name 은 env 에서 넣는다.
_NOT_FROM_HANDLER = {"contents", "history", "cached_content", "service_name"}

_CHAT_FUNCTIONS = [
    ("llm_chat_stream_async", handler_aio.stream),
    ("llm_chat_async", handler_aio.call),
]


class TestPublicSurface:
    @pytest.mark.parametrize("chat_name, handler_fn", _CHAT_FUNCTIONS, ids=["stream", "call"])
    def test_parameters_are_handler_minus_excluded(self, chat_name, handler_fn):
        """밑단에 인자가 늘면 여기서 터진다 — 대화 함수에서 조용히 빠지지 않게."""
        chat_params = set(inspect.signature(getattr(llm_manager, chat_name)).parameters)
        handler_params = set(inspect.signature(handler_fn).parameters)

        assert chat_params == handler_params - _NOT_FROM_HANDLER

    @pytest.mark.parametrize("arg", [
        "contents", "history", "cached_content", "cache_mode", "save_mode", "context_cache_ttl",
    ])
    @pytest.mark.parametrize("chat_name", ["llm_chat_stream_async", "llm_chat_async"])
    def test_rejects_single_shot_and_cache_arguments(self, chat_name, arg):
        with pytest.raises(TypeError):
            getattr(llm_manager, chat_name)([], **{arg: None})

    def test_functions_are_exported(self):
        assert {"llm_chat_async", "llm_chat_stream_async"} <= set(llm_manager.__all__)

    @pytest.mark.parametrize("surface", [llm_manager, public_aio], ids=["llm_manager", "aio"])
    def test_message_types_are_exported(self, surface):
        """소비자가 `_internal` 경로로 타입을 가져오지 않게 한다."""
        assert {"ChatMessage", "Content", "FunctionResponse"} <= set(surface.__all__)
        assert surface.ChatMessage is ChatMessage
        assert surface.Content is Content
        assert surface.FunctionResponse is FunctionResponse
