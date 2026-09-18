"""대화 함수의 첨부 해소와 404 복구 — docs/38 §3.3.

공개 함수(`llm_chat_stream_async` · `llm_chat_async`)를 부르되 밑단 호출만 가짜로 바꾼다.
Files API 주소는 Redis 기록을 미리 심어 업로드 없이 재사용시킨다 — 이 폴더의 autouse
fixture 가 Redis 를 메모리로 격리하므로 `tests/_internal/` 에 둔다.
"""
from __future__ import annotations

import asyncio

import pytest

import llm_manager
from llm_manager._internal import chat
from llm_manager._internal.file_processing import cache_store, resolver
from llm_manager._internal.handler_client.exceptions import HandlerError
from llm_manager._internal.handler_client.models import (
    Content,
    LLMResponse,
    Part,
    StreamChunk,
)
from llm_manager._internal.keying import make_file_checksum
from llm_manager._internal.stored_file_resolver import resolve_stored_files
from llm_manager.exceptions import CacheError, FileResolveError, ValidationError
from llm_manager.models import ChatMessage, StoredFile

# ── 헬퍼 ──────────────────────────────────────────────────────


def _sf(file_name: str) -> StoredFile:
    return StoredFile(
        file_path=f"path/{file_name}",
        file_name=file_name,
        file_size=100,
        file_mtime=1700000000,
        mime_type="application/pdf",
        extension=".pdf",
    )


async def _seed_files_api(sf: StoredFile, uris: list[str]) -> str:
    """sf 의 Files API 주소 기록을 Redis 에 심는다 → checksum. 업로드를 우회한다."""
    [fi] = await resolve_stored_files([sf])
    cs = make_file_checksum(fi)
    cache_store.save_files_api(cs, {
        "file_checksum": cs,
        "file_name": fi.file_name,
        "file_size": fi.file_size,
        "mime_type": fi.mime_type,
        "uris": uris,
        "split": len(uris) > 1,
    })
    return cs


def _user(*content) -> ChatMessage:
    return ChatMessage(role="user", content=list(content))


def _text(text: str) -> Part:
    return Part(type="text", text=text)


def _shape(message: Content) -> list[tuple[str, str | None]]:
    """보낸 발화 하나를 (조각 종류, 글 또는 주소) 목록으로 줄인다."""
    return [(p.type, p.text if p.type == "text" else p.file_uri) for p in message.content]


class FakeHandler:
    """밑단 `stream`·`call` 대신 받은 인자를 남기고, 호출 차례마다 정해 둔 결과를 낸다.

    stream 의 차례 하나는 청크·예외 목록이다(예외를 만나면 거기서 던진다).
    call 의 차례 하나는 LLMResponse 또는 예외다.
    """

    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[dict] = []

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        for event in self.script.pop(0):
            if isinstance(event, Exception):
                raise event
            yield event

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        result = self.script.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _response(text: str = "답") -> LLMResponse:
    return LLMResponse(text=text, model="gemini-2.5-flash", request_id="req-1")


@pytest.fixture()
def handler(monkeypatch):
    """밑단 호출을 가짜로 바꾼다. 기본 대본은 청크 하나·응답 하나."""
    monkeypatch.setattr(llm_manager, "ensure_configured", lambda: None)
    fake = FakeHandler([[StreamChunk(text="ok")]])
    monkeypatch.setattr(chat, "aio_stream", fake.stream)
    monkeypatch.setattr(chat, "aio_call", fake.call)
    return fake


async def _stream(messages: list[ChatMessage], **kwargs) -> list[StreamChunk]:
    return [c async for c in llm_manager.llm_chat_stream_async(messages, **kwargs)]


# ── 첨부 해소 ──────────────────────────────────────────────────


class TestResolveAttachments:
    async def test_stored_file_becomes_file_part_in_place(self, handler):
        """파일은 원래 발화의 원래 위치에 들어간다 — 앞으로 모으지 않는다."""
        sf_a, sf_b = _sf("a.pdf"), _sf("b.pdf")
        await _seed_files_api(sf_a, ["files/a1"])
        await _seed_files_api(sf_b, ["files/b1"])
        messages = [
            _user(_text("앞"), sf_a, _text("뒤")),
            ChatMessage(role="model", content=[_text("읽었습니다")]),
            _user(sf_b, _text("이건?")),
        ]

        await _stream(messages)

        sent = handler.calls[0]["messages"]
        assert [m.role for m in sent] == ["user", "model", "user"]
        assert _shape(sent[0]) == [("text", "앞"), ("file", "files/a1"), ("text", "뒤")]
        assert _shape(sent[1]) == [("text", "읽었습니다")]
        assert _shape(sent[2]) == [("file", "files/b1"), ("text", "이건?")]

    async def test_split_pdf_becomes_several_parts_in_place(self, handler):
        sf = _sf("big.pdf")
        await _seed_files_api(sf, ["files/p1", "files/p2"])

        await _stream([_user(sf, _text("요약"))])

        assert _shape(handler.calls[0]["messages"][0]) == [
            ("file", "files/p1"), ("file", "files/p2"), ("text", "요약"),
        ]

    async def test_slow_file_alone_becomes_notice(self, handler, monkeypatch):
        """파일 하나가 상한을 넘기면 그 파일만 안내 문구가 되고 나머지는 정상으로 간다."""
        slow, ok = _sf("slow.pdf"), _sf("ok.pdf")
        # 느린 파일도 주소를 심어 둔다 — 상한이 없으면 1초 뒤 이 주소로 풀려 테스트가 실패한다.
        await _seed_files_api(slow, ["files/slow1"])
        await _seed_files_api(ok, ["files/ok1"])
        real = chat._resolve_one_files_api

        async def resolve(fi, checksum, **kwargs):
            if fi.file_name == "slow.pdf":
                await asyncio.sleep(1)
            return await real(fi, checksum, **kwargs)

        monkeypatch.setattr(chat, "_resolve_one_files_api", resolve)
        monkeypatch.setattr(chat, "ATTACHMENT_RESOLVE_TIMEOUT", 0.05)

        await _stream([_user(slow, ok, _text("비교"))])

        assert _shape(handler.calls[0]["messages"][0]) == [
            ("text", "[slow.pdf: 첨부를 불러오지 못했습니다]"),
            ("file", "files/ok1"),
            ("text", "비교"),
        ]

    async def test_failed_file_alone_becomes_notice(self, handler, monkeypatch):
        """파일 하나에 매인 실패(파일매니저에서 못 찾음)면 그 파일만 안내 문구가 되고 호출은 계속된다."""
        bad, ok = _sf("bad.pdf"), _sf("ok.pdf")
        await _seed_files_api(ok, ["files/ok1"])
        real = chat._resolve_one_files_api

        async def resolve(fi, checksum, **kwargs):
            if fi.file_name == "bad.pdf":
                raise FileResolveError("file-manager 에 그 파일이 없다")
            return await real(fi, checksum, **kwargs)

        monkeypatch.setattr(chat, "_resolve_one_files_api", resolve)

        chunks = await _stream([_user(bad, ok)])

        assert [c.text for c in chunks] == ["ok"]
        assert _shape(handler.calls[0]["messages"][0]) == [
            ("text", "[bad.pdf: 첨부를 불러오지 못했습니다]"),
            ("file", "files/ok1"),
        ]

    async def test_infrastructure_failure_is_raised_not_hidden(self, handler, monkeypatch):
        """Redis·file-manager 장애는 파일 하나의 문제가 아니다 — 안내 문구로 덮지 않고 올린다.

        덮으면 장애 동안 모든 대화의 모든 첨부가 조용히 빠진 채 모델이 문서 없이 답한다.
        """
        async def resolve(fi, checksum, **kwargs):
            raise CacheError("Redis files_api 조회 실패: handler down")

        monkeypatch.setattr(chat, "_resolve_one_files_api", resolve)

        with pytest.raises(CacheError):
            await _stream([_user(_sf("a.pdf"), _text("요약"))])
        assert handler.calls == []


# ── 반환과 전달 ────────────────────────────────────────────────


class TestCallThrough:
    async def test_stream_yields_stream_chunk_objects(self, handler):
        handler.script = [[StreamChunk(text="가"), StreamChunk(text="나", finish_reason="STOP")]]

        chunks = await _stream([_user(_text("안녕"))])

        assert all(type(c) is StreamChunk for c in chunks)
        assert [c.text for c in chunks] == ["가", "나"]

    async def test_call_returns_llm_response(self, handler):
        expected = _response()
        handler.script = [expected]

        result = await llm_manager.llm_chat_async([_user(_text("안녕"))])

        assert result is expected

    async def test_arguments_and_service_name_reach_handler(self, handler):
        tools = [{"function_declarations": [{"name": "search"}]}]

        await _stream([_user(_text("안녕"))], system="시스템", tools=tools, tool_config="auto")

        sent = handler.calls[0]
        assert (sent["system"], sent["tools"], sent["tool_config"]) == ("시스템", tools, "auto")
        assert sent["service_name"] == "test-service"

    async def test_missing_service_name_fails_before_request(self, handler, monkeypatch):
        monkeypatch.delenv("SERVICE_NAME")

        with pytest.raises(ValidationError):
            await _stream([_user(_text("안녕"))])
        assert handler.calls == []


# ── Gemini 404 복구 ────────────────────────────────────────────


def _gone() -> HandlerError:
    """만료된 Files API 주소로 부르면 Gemini 가 내는 404."""
    return HandlerError("File not found", 404)


def _forbidden() -> HandlerError:
    """지워진 Files API 파일에 Gemini 는 403 을 주고, handler 가 502 handler_ops 로 바꾼다.

    2026-09-17 실측 메시지 그대로 — 챗봇 실환경에서 이 모양으로 왔고, 404 판정으로는 안 걸렸다.
    """
    return HandlerError("Gemini stream 권한/인증 실패 (gemini_status=403)",
                        status_code=502, error_type="handler_ops")


@pytest.fixture()
def reupload(monkeypatch, fake_redis):
    """Redis 에 주소가 없을 때의 업로드를 대신한다 — 새 주소 `files/new` 를 준다."""
    async def upload(fi, checksum, uri_parts, file_refs):
        uri_parts.append(Part.from_uri("files/new", fi.mime_type))

    monkeypatch.setattr(resolver, "_upload_and_cache_file", upload)


class TestGemini404Recovery:
    async def test_stream_404_before_first_chunk_reresolves_and_retries_once(
        self, handler, reupload,
    ):
        """Redis 기록을 지워야 다시 풀 때 만료된 주소를 재사용하지 않고 새로 올린다."""
        sf = _sf("a.pdf")
        await _seed_files_api(sf, ["files/old"])
        handler.script = [[_gone()], [StreamChunk(text="ok")]]

        chunks = await _stream([_user(sf)])

        assert [c.text for c in chunks] == ["ok"]
        assert [_shape(call["messages"][0]) for call in handler.calls] == [
            [("file", "files/old")],
            [("file", "files/new")],
        ]

    async def test_stream_403_before_first_chunk_reresolves_and_retries_once(
        self, handler, reupload,
    ):
        sf = _sf("a.pdf")
        await _seed_files_api(sf, ["files/old"])
        handler.script = [[_forbidden()], [StreamChunk(text="ok")]]

        chunks = await _stream([_user(sf)])

        assert [c.text for c in chunks] == ["ok"]
        assert [_shape(call["messages"][0]) for call in handler.calls] == [
            [("file", "files/old")],
            [("file", "files/new")],
        ]

    async def test_call_403_reresolves_and_retries_once(self, handler, reupload):
        sf = _sf("a.pdf")
        await _seed_files_api(sf, ["files/old"])
        expected = _response()
        handler.script = [_forbidden(), expected]

        result = await llm_manager.llm_chat_async([_user(sf)])

        assert result is expected

    async def test_stream_404_again_after_retry_raises(self, handler, reupload):
        sf = _sf("a.pdf")
        await _seed_files_api(sf, ["files/old"])
        handler.script = [[_gone()], [_gone()]]

        with pytest.raises(HandlerError):
            await _stream([_user(sf)])
        assert len(handler.calls) == 2

    async def test_stream_404_after_first_chunk_is_not_retried(self, handler, reupload):
        """이미 내보낸 청크는 되돌릴 수 없어, 다시 부르면 답이 두 번 나간다."""
        sf = _sf("a.pdf")
        await _seed_files_api(sf, ["files/old"])
        handler.script = [[StreamChunk(text="가"), _gone()], [StreamChunk(text="ok")]]
        received: list[str | None] = []

        with pytest.raises(HandlerError):
            async for chunk in llm_manager.llm_chat_stream_async([_user(sf)]):
                received.append(chunk.text)
        assert received == ["가"]
        assert len(handler.calls) == 1

    async def test_404_without_attachments_is_not_retried(self, handler):
        """다시 풀 파일이 없으면 같은 요청을 한 번 더 보낼 뿐이다."""
        handler.script = [[_gone()], [StreamChunk(text="ok")]]

        with pytest.raises(HandlerError):
            await _stream([_user(_text("안녕"))])
        assert len(handler.calls) == 1

    async def test_404_with_only_notice_attachments_is_not_retried(self, handler, monkeypatch):
        """안내 문구로 바뀐 파일은 주소를 보내지 않았으니 404 의 원인이 아니다."""
        async def resolve(fi, checksum, **kwargs):
            raise FileResolveError("file-manager 에 그 파일이 없다")

        monkeypatch.setattr(chat, "_resolve_one_files_api", resolve)
        handler.script = [[_gone()], [StreamChunk(text="ok")]]

        with pytest.raises(HandlerError):
            await _stream([_user(_sf("bad.pdf"))])
        assert len(handler.calls) == 1

    async def test_call_404_reresolves_and_retries_once(self, handler, reupload):
        sf = _sf("a.pdf")
        await _seed_files_api(sf, ["files/old"])
        expected = _response()
        handler.script = [_gone(), expected]

        result = await llm_manager.llm_chat_async([_user(sf)])

        assert result is expected
        assert [_shape(call["messages"][0]) for call in handler.calls] == [
            [("file", "files/old")],
            [("file", "files/new")],
        ]
