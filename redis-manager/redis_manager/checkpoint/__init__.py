# -*- coding: utf-8 -*-
"""LangGraph Redis 체크포인터 서브패키지 (임시방편 · 기존 httpx 구성과 완전 분리).

대상 Redis(Stack)에 직접 연결해 준비된 AsyncRedisSaver를 넘기는 async 컨텍스트
매니저 open_checkpointer() 하나만 제공한다. redis-py·langgraph-checkpoint-redis는
[langgraph] extra로 분리되며, 최상위 redis_manager는 이 모듈을 import하지 않는다.
"""
import os

_DEFAULT_URL = "redis://192.7.9.45:6379/0"

_REQUIRED_MODULES = ("rejson", "search")  # RedisJSON, RediSearch (소문자 비교)


def _url() -> str:
    """대상 Redis URL. env 미설정 시 하드코딩 기본값."""
    return os.getenv("LANGGRAPH_REDIS_URL", _DEFAULT_URL)


def _module_names(module_list) -> set:
    names = set()
    for entry in module_list or []:
        raw = entry.get(b"name", entry.get("name")) if isinstance(entry, dict) else None
        if raw is None:
            continue
        names.add(raw.decode() if isinstance(raw, bytes) else str(raw))
    return {n.lower() for n in names}


async def _require_modules(client) -> None:
    """대상 Redis에 RedisJSON·RediSearch가 없으면 명확히 raise (조용한 실패 금지).

    모듈명은 배포마다 다를 수 있어(ReJSON, searchlight 등) 정확 일치가 아니라
    부분 매칭한다: present의 어떤 이름이든 요구 토큰을 포함하면 존재로 본다.
    """
    present = _module_names(await client.module_list())
    missing = [m for m in _REQUIRED_MODULES if not any(m in p for p in present)]
    if missing:
        label = {"rejson": "ReJSON(RedisJSON)", "search": "search(RediSearch)"}
        names = ", ".join(label[m] for m in missing)
        raise RuntimeError(
            f"LangGraph 체크포인터에 필요한 Redis 모듈이 없습니다: {names}. "
            f"RedisJSON+RediSearch가 탑재된 Redis Stack을 LANGGRAPH_REDIS_URL로 지정하세요."
        )
