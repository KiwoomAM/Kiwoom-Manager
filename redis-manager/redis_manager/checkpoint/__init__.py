# -*- coding: utf-8 -*-
"""LangGraph Redis 체크포인터 서브패키지 (임시방편 · 기존 httpx 구성과 완전 분리).

대상 Redis(Stack)에 직접 연결해 준비된 AsyncRedisSaver를 넘기는 async 컨텍스트
매니저 open_checkpointer() 하나만 제공한다. redis-py·langgraph-checkpoint-redis는
[langgraph] extra로 분리되며, 최상위 redis_manager는 이 모듈을 import하지 않는다.
"""
import os

_DEFAULT_URL = "redis://192.7.9.45:6379/0"


def _url() -> str:
    """대상 Redis URL. env 미설정 시 하드코딩 기본값."""
    return os.getenv("LANGGRAPH_REDIS_URL", _DEFAULT_URL)
