# -*- coding: utf-8 -*-
"""실제 Redis Stack(RedisJSON+RediSearch) 대상 통합 테스트.

LANGGRAPH_REDIS_URL 이 가리키는 인스턴스에 모듈이 없거나 서버에 못 붙으면 skip한다
(통과로 위장하지 않는다). 실행 예:
    LANGGRAPH_REDIS_URL=redis://localhost:6379/0 python3 -m pytest \
        tests/checkpoint/test_integration.py -v

주의: aput/aget를 손으로 부르지 않는다 — 세이버는 channel_values를 인라인 저장하지
않고 channel_versions 기준으로 채널 blob을 쓰므로, 작은 StateGraph를 컴파일해 실제
저장/복원 경로를 태운다(왕복 검증의 신뢰성).
"""
import operator
import uuid
from typing import Annotated

import pytest
from typing_extensions import TypedDict

import redis.exceptions as rexc

from redis_manager.checkpoint import open_checkpointer, _url, _require_modules


async def _stack_reachable() -> bool:
    """대상이 '모듈 미탑재' 또는 '연결 불가'일 때만 skip 신호. 그 외 예외는
    삼키지 않고 전파해 실 버그가 SKIP으로 위장되지 않게 한다."""
    from redis.asyncio import Redis

    client = Redis.from_url(_url())
    try:
        await _require_modules(client)   # 모듈 없으면 RuntimeError
        return True
    except RuntimeError:
        return False                     # 모듈 미탑재 → skip
    except rexc.RedisError:
        return False                     # 연결/타임아웃 등 서버 미도달 → skip
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_state_persists_across_invocations():
    if not await _stack_reachable():
        pytest.skip("Redis Stack(RedisJSON+RediSearch) 미탑재/미도달 — 통합 테스트 skip")

    from langgraph.graph import StateGraph, START, END

    class S(TypedDict):
        messages: Annotated[list, operator.add]   # append reducer

    async def add(state):
        return {"messages": ["x"]}

    thread_id = f"itest-{uuid.uuid4().hex}"
    config = {"configurable": {"thread_id": thread_id}}

    async with open_checkpointer() as saver:
        graph = (
            StateGraph(S)
            .add_node("add", add)
            .add_edge(START, "add")
            .add_edge("add", END)
            .compile(checkpointer=saver)
        )
        r1 = await graph.ainvoke({"messages": ["a"]}, config)
        r2 = await graph.ainvoke({"messages": ["b"]}, config)

    # 불변식: 두 번째 호출이 첫 호출의 상태를 이어받았다(체크포인터가 복원).
    assert r2["messages"][0] == "a"                    # 첫 호출 입력이 살아남음
    assert len(r2["messages"]) > len(r1["messages"])   # 누적됨 (없으면 매번 초기화)


@pytest.mark.asyncio
async def test_asetup_is_idempotent():
    if not await _stack_reachable():
        pytest.skip("Redis Stack 미탑재/미도달 — 통합 테스트 skip")

    # open_checkpointer 는 진입 시마다 asetup 을 부른다. 두 번 열어도 예외가 없어야 한다.
    async with open_checkpointer():
        pass
    async with open_checkpointer():
        pass
