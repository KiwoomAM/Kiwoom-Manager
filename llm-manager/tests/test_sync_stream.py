"""sync `llm_request_stream` 증분 스트리밍 + 스트림 정리 계약 — docs/36.

두 갈래를 검증한다.
  (가) `_events()` 가 닫힐 때 **안쪽 스트림까지** 닫는가 (= standby 리더 토큰 해제 경로)
  (나) sync 래퍼가 전부 모으지 않고 chunk 마다 넘겨주는가

(가)가 먼저인 이유: 안쪽 정리가 보장되지 않으면 (나)의 정리도 의미가 없다.
docs/36 §4.1 — 초안은 `agen.aclose()` 가 락을 푼다고 적었으나 두 겹 구조라 그렇지 않았다.
"""
from __future__ import annotations

import asyncio
import gc

import pytest

import llm_manager
from llm_manager._internal.handler_client.models import StreamChunk


def _patch_stream(monkeypatch, inner_factory, meta=None):
    """공개 `llm_request_stream_async` 가 주어진 async 제너레이터를 쓰도록 갈아끼운다."""
    monkeypatch.setattr(llm_manager, "ensure_configured", lambda: None)

    async def fake_runner_stream(*args, **kwargs):
        return inner_factory(), (meta if meta is not None else {"source": "live"})

    monkeypatch.setattr(
        llm_manager.runner, "llm_request_stream_async", fake_runner_stream,
    )


# ── (가) `_events()` 정리 전파 (docs/36 §5-4) ──────────────────


class TestEventsClosePropagates:
    """바깥을 닫으면 안쪽 async 제너레이터의 `finally` 가 **즉시** 돌아야 한다.

    안쪽 `finally` 에 standby 리더 토큰 해제가 걸려 있다(runner.py:734-735).
    지금은 `_events()` 에 try/finally 가 없어 `async for` 가 안쪽을 닫지 않고,
    이벤트 루프의 asyncgen 파이널라이저가 나중에 대신 닫아 준다 — 즉 GC 타이밍 의존이다.
    """

    async def test_aclose_runs_inner_finally(self, monkeypatch):
        closed: list[str] = []

        def inner():
            async def _gen():
                try:
                    for i in range(5):
                        yield StreamChunk(text=f"c{i}", finish_reason=None)
                finally:
                    closed.append("inner")   # = release_standby 자리
            return _gen()

        _patch_stream(monkeypatch, inner)

        stream, _meta = await llm_manager.llm_request_stream_async(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        await stream.__anext__()          # 한 개만 소비하고
        await stream.aclose()             # 바깥을 닫는다

        assert closed == ["inner"], (
            "바깥 스트림을 닫았는데 안쪽 finally 가 돌지 않았다 — "
            "standby 리더 토큰이 파이널라이저 시점까지 안 풀린다"
        )

    async def test_full_drain_still_runs_inner_finally(self, monkeypatch):
        """회귀 — 끝까지 소비하는 정상 경로는 원래도 안쪽 finally 가 돈다."""
        closed: list[str] = []

        def inner():
            async def _gen():
                try:
                    yield StreamChunk(text="only", finish_reason="STOP")
                finally:
                    closed.append("inner")
            return _gen()

        _patch_stream(monkeypatch, inner)

        stream, _meta = await llm_manager.llm_request_stream_async(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        events = [ev async for ev in stream]

        assert [e["text"] for e in events] == ["only"]
        assert closed == ["inner"]


# ── (나) sync 래퍼 증분성 (docs/36 §5-1·2·3) ──────────────────


def _counting_inner(produced: list[int], closed: list[str], n: int = 3):
    """생산 시점을 기록하는 async 제너레이터 팩토리."""

    def factory():
        async def _gen():
            try:
                for i in range(n):
                    produced.append(i)          # ← 이 chunk 가 '만들어진' 시점
                    yield StreamChunk(text=f"c{i}", finish_reason=None)
            finally:
                closed.append("inner")
        return _gen()

    return factory


class TestSyncStreamIsIncremental:
    """sync 래퍼가 전부 모으지 않고 chunk 마다 넘겨줘야 한다.

    시간에 기대지 않고 **생산 시점**으로 판정한다 — 첫 `next()` 가 돌아온 시점에
    몇 개가 생산돼 있는지를 본다. 전부 모으는 구현이면 그 시점에 이미 전부 생산돼 있다.
    """

    def test_first_next_does_not_drain_whole_stream(self, monkeypatch):
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed))

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        first = next(it)

        assert first["text"] == "c0"
        assert produced == [0], (
            f"첫 next() 시점에 이미 {produced} 가 생산됐다 — 스트림을 전부 모았다는 뜻"
        )

    def test_meta_returns_before_any_chunk_is_produced(self, monkeypatch):
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed))

        _it, meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )

        assert meta is not None
        assert produced == [], (
            f"meta 를 받은 시점에 이미 {produced} 가 생산됐다 — 준비 단계에서 스트림을 소진했다"
        )

    def test_order_and_content_preserved(self, monkeypatch):
        """회귀 — 끝까지 소비하면 순서·내용이 그대로다."""
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed))

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        events = list(it)

        assert [e["text"] for e in events] == ["c0", "c1", "c2"]
        assert closed == ["inner"], "끝까지 소비했으면 안쪽 정리가 돌아야 한다"


# ── 정리 불변식 (docs/36 §5-3·6·9·10·11) ──────────────────────


class TestSyncStreamCleanup:
    """루프는 이 객체가 소유한다 — 어떤 경로로 끝나든 반드시 회수돼야 한다.

    단정은 "제너레이터의 finally 가 돌았다" 가 아니라 **안쪽 정리 함수가 실제로 불렸는지**로
    본다. 초안이 틀린 계층(`aclose()`)을 방어선으로 지목했다가 리뷰에서 잡혔기 때문이다
    (docs/36 §4.1).
    """

    def test_never_iterated_closes_loop(self, monkeypatch):
        """§5-3 — 순회하지 않고 버려도 **루프는** 회수된다.

        안쪽 정리(= standby 해제)는 여기서 보장되지 않는다. 시작조차 안 된 async 제너레이터는
        `aclose()` 를 불러도 `finally` 가 없기 때문이다(PEP 525). 그리고 락은 제너레이터
        **바깥**(`runner.llm_request_stream_async`)에서 잡으므로 sync 래퍼가 손쓸 수 없다.

        **이는 async 판과 동일한 동작이다** — 아래 회귀 테스트가 그 동등성을 고정한다.
        해소하려면 락 획득 시점을 옮겨야 하고 그건 별건이다 (docs/36 §3.2·§7).
        """
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed))

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        loop = it._loop
        del it
        gc.collect()

        assert loop.is_closed(), "이벤트 루프가 회수되지 않았다 — 이건 래퍼의 책임이다"
        assert closed == [], (
            "안쪽 정리가 돌았다면 좋은 일이지만, 그렇다면 이 테스트의 전제(PEP 525)가 바뀐 것이니 "
            "docs/36 §3.2·§7 을 다시 판단해야 한다"
        )

    async def test_async_path_has_the_same_hole(self, monkeypatch):
        """위 동작이 sync 래퍼가 만든 것이 아니라 async 판과 같음을 고정한다."""
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed))

        stream, _meta = await llm_manager.llm_request_stream_async(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        await stream.aclose()          # 순회하지 않고 닫는다

        assert closed == [], "async 판의 동작이 바뀌었다 — docs/36 §3.2 재검토 필요"

    def test_partial_consume_then_close(self, monkeypatch):
        """일부만 소비하고 명시적으로 닫으면 즉시 정리된다."""
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed))

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        next(it)
        it.close()

        assert closed == ["inner"]
        assert it._loop.is_closed()

    def test_close_is_idempotent(self, monkeypatch):
        """§5-11 — 두 번 닫아도 정리는 한 번, 예외 없음."""
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed))

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        next(it)            # 시작시켜야 안쪽 finally 가 의미를 갖는다
        it.close()
        it.close()          # 두 번째는 아무 일도 하지 않아야 한다

        assert closed == ["inner"], "정리가 한 번은 돌아야 한다"

    def test_mid_stream_exception_propagates_and_cleans_up(self, monkeypatch):
        """§5-6 — 도중 예외는 같은 타입으로 오르고, 그때도 자원은 회수된다."""

        class BoomError(RuntimeError):
            pass

        closed: list[str] = []

        def inner():
            async def _gen():
                try:
                    yield StreamChunk(text="c0", finish_reason=None)
                    raise BoomError("mid-stream")
                finally:
                    closed.append("inner")
            return _gen()

        _patch_stream(monkeypatch, inner)

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        loop = it._loop
        assert next(it)["text"] == "c0"

        with pytest.raises(BoomError):
            next(it)

        assert closed == ["inner"]
        assert loop.is_closed(), "예외로 끝났는데 루프가 남았다"

    def test_aclose_failure_still_closes_loop(self, monkeypatch):
        """§5-9 — 정리 첫 단계가 던져도 루프는 반드시 닫힌다."""

        def inner():
            async def _gen():
                try:
                    yield StreamChunk(text="c0", finish_reason=None)
                    yield StreamChunk(text="c1", finish_reason=None)
                finally:
                    raise RuntimeError("teardown 폭발")   # httpx teardown 흉내
            return _gen()

        _patch_stream(monkeypatch, inner)

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        loop = it._loop
        next(it)
        it.close()          # 예외를 밖으로 내보내지 않아야 한다

        assert loop.is_closed(), "aclose 가 던지자 루프 정리가 통째로 날아갔다"

    def test_setup_failure_does_not_leak_loop(self, monkeypatch):
        """§5-10 — 준비 단계에서 실패해도 만들어 둔 루프를 흘리지 않는다."""
        created: list[asyncio.AbstractEventLoop] = []
        real_new_loop = asyncio.new_event_loop

        def spy_new_loop():
            loop = real_new_loop()
            created.append(loop)
            return loop

        monkeypatch.setattr(asyncio, "new_event_loop", spy_new_loop)
        monkeypatch.setattr(llm_manager, "ensure_configured", lambda: None)

        async def boom(*args, **kwargs):
            raise RuntimeError("준비 실패")

        monkeypatch.setattr(llm_manager.runner, "llm_request_stream_async", boom)

        with pytest.raises(RuntimeError, match="준비 실패"):
            llm_manager.llm_request_stream(
                {"contents": "x", "model": "gemini-2.5-flash"},
            )

        assert created, "루프가 만들어지지 않았다 — 테스트 전제가 깨졌다"
        assert all(lp.is_closed() for lp in created), "준비 실패 후 루프가 남았다"


# ── PR #100 리뷰 대응 (docs/36 §4.5) ──────────────────────────


class TestIteratorProtocol:
    """소진·폐기 뒤에도 이터레이터 프로토콜을 지켜야 한다.

    초판은 `StopAsyncIteration` 을 받으면 곧장 루프를 닫아서, 그 다음 호출이 닫힌 루프를
    만나 `RuntimeError` 를 냈다. `next(it, default)`·재순회·itertools 조합처럼 평범한
    소비 패턴이 깨진다 (PR #100 리뷰 🟠).
    """

    def test_repeated_next_after_exhaustion(self, monkeypatch):
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed, n=1))

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        assert next(it)["text"] == "c0"

        for _ in range(3):                       # 소진 뒤 여러 번 불러도
            with pytest.raises(StopIteration):
                next(it)

    def test_next_with_default_after_exhaustion(self, monkeypatch):
        """`next(it, default)` 방어 패턴이 살아 있어야 한다."""
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed, n=1))

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        list(it)                                  # 소진
        assert next(it, "SENTINEL") == "SENTINEL"

    def test_relist_after_exhaustion_is_empty(self, monkeypatch):
        """두 번째 `list()` 는 빈 리스트여야 한다 (예외가 아니라)."""
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed, n=2))

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        assert len(list(it)) == 2
        assert list(it) == []

    def test_next_after_explicit_close(self, monkeypatch):
        """명시적으로 닫은 뒤에도 StopIteration 이다."""
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed))

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        next(it)
        it.close()
        with pytest.raises(StopIteration):
            next(it)


class TestContextManager:
    """`with` 로 확정 해제할 수 있어야 한다 — docstring 이 요구하는 "닫아라" 의 강제 수단."""

    def test_with_closes_on_exit(self, monkeypatch):
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed))

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        with it as stream:
            assert next(stream)["text"] == "c0"

        assert closed == ["inner"], "with 블록을 나올 때 정리가 안 됐다"
        assert it._loop.is_closed()

    def test_with_closes_on_exception(self, monkeypatch):
        """블록 안에서 예외가 나도 닫힌다."""
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed))

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        with pytest.raises(ValueError):
            with it as stream:
                next(stream)
                raise ValueError("소비자 쪽 실패")

        assert closed == ["inner"]
        assert it._loop.is_closed()


class TestReturnContract:
    """docstring 이 요구하는 것(`close()`·`with`)이 **타입에도** 있어야 한다.

    반환 타입이 `Iterator[dict]` 이던 시절에는 docstring 이 시키는 대로 쓰면 타입 검사가
    깨졌다 — 타입체커를 쓰는 성실한 소비자일수록 계약을 어기게 된다(PR #100 리뷰).
    """

    def test_stream_iterator_is_public(self):
        assert "StreamIterator" in llm_manager.__all__
        assert hasattr(llm_manager, "StreamIterator")

    def test_returned_object_satisfies_contract(self, monkeypatch):
        produced: list[int] = []
        closed: list[str] = []
        _patch_stream(monkeypatch, _counting_inner(produced, closed))

        it, _meta = llm_manager.llm_request_stream(
            {"contents": "x", "model": "gemini-2.5-flash"},
        )
        try:
            for name in ("__iter__", "__next__", "close", "__enter__", "__exit__"):
                assert hasattr(it, name), f"반환 객체에 {name} 이 없다 — 계약 위반"
        finally:
            it.close()
