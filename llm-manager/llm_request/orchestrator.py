"""다중 LLM 요청 병렬 실행."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any

from llm_handler.models import LLMResponse, StreamChunk

from .runner import llm_request_async, llm_request_stream_async


async def run_many(
    states: Iterable[dict],
    *,
    concurrency: int = 5,
    database: str | None = None,
) -> AsyncIterator[tuple[LLMResponse, dict[str, Any]]]:
    """N개 state를 병렬로 LLM 호출하고, 완료되는 순서대로 yield한다.

    각 결과의 meta에 ``_index`` (입력 순서)와 ``_state`` (원본 state)를 첨부하여
    호출자가 식별/정렬할 수 있게 한다.

    Args:
        states: llm_request_async에 전달할 state dict의 iterable.
        concurrency: 동시 실행 최대 수 (Semaphore).
        database: MongoDB 데이터베이스 이름.

    Yields:
        (LLMResponse, meta) 튜플. meta에 ``_index``, ``_state`` 포함.
    """
    sem = asyncio.Semaphore(concurrency)
    state_list = list(states)

    async def _worker(idx: int, state: dict) -> tuple[LLMResponse, dict[str, Any]]:
        async with sem:
            response, meta = await llm_request_async(state, database=database)
            meta["_index"] = idx
            meta["_state"] = state
            return response, meta

    tasks = [asyncio.create_task(_worker(i, s)) for i, s in enumerate(state_list)]
    for coro in asyncio.as_completed(tasks):
        yield await coro


async def run_many_stream(
    states: Iterable[dict],
    *,
    concurrency: int = 5,
    database: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """N개 state를 병렬 stream으로 실행하고, chunk 단위로 실시간 yield한다.

    모든 이벤트에 ``meta`` 가 포함되어 호출자가 이벤트 타입에 관계없이
    ``event["meta"]`` 로 메타 정보에 접근할 수 있다.

    이벤트 타입:
        - ``{"_index": int, "_type": "chunk", "chunk": StreamChunk, "meta": dict}``
        - ``{"_index": int, "_type": "done", "meta": dict}``
        - ``{"_index": int, "_type": "cache_hit", "data": (LLMResponse, meta), "meta": dict}``
        - ``{"_index": int, "_type": "error", "error": Exception, "meta": dict}``

    Args:
        states: llm_request_stream_async에 전달할 state dict의 iterable.
        concurrency: 동시 실행 최대 수 (Semaphore).
        database: MongoDB 데이터베이스 이름.

    Yields:
        _index, _type, meta를 포함하는 이벤트 dict.
    """
    sem = asyncio.Semaphore(concurrency)
    state_list = list(states)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def _worker(idx: int, state: dict) -> None:
        meta: dict[str, Any] = {"_index": idx, "_state": state}
        async with sem:
            try:
                stream_iter, meta = await llm_request_stream_async(state, database=database)
                meta["_index"] = idx
                meta["_state"] = state

                if meta["cache_hit"]:
                    first_chunk: StreamChunk | None = None
                    async for chunk in stream_iter:
                        first_chunk = chunk
                        break
                    if first_chunk is not None:
                        response = LLMResponse(
                            text=first_chunk.text,
                            model=meta.get("meta", {}).get("model", "unknown"),
                            usage=first_chunk.usage,
                            finish_reason=first_chunk.finish_reason,
                            request_id="",
                        )
                        await queue.put(
                            {
                                "_index": idx,
                                "_type": "cache_hit",
                                "data": (response, meta),
                                "meta": meta,
                            }
                        )
                else:
                    async for chunk in stream_iter:
                        await queue.put(
                            {
                                "_index": idx,
                                "_type": "chunk",
                                "chunk": chunk,
                                "meta": meta,
                            }
                        )
                    await queue.put(
                        {
                            "_index": idx,
                            "_type": "done",
                            "meta": meta,
                        }
                    )
            except Exception as exc:
                await queue.put({
                    "_index": idx, "_type": "error",
                    "error": exc, "meta": meta,
                })

    async def _run_all() -> None:
        tasks = [asyncio.create_task(_worker(i, s)) for i, s in enumerate(state_list)]
        await asyncio.gather(*tasks, return_exceptions=True)
        await queue.put(None)

    runner = asyncio.create_task(_run_all())

    while True:
        event = await queue.get()
        if event is None:
            break
        yield event

    await runner
