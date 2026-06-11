# -*- coding: utf-8 -*-
"""health 진단 live 동작 회귀 테스트.

원 이슈의 두 결함을 역검증으로 고정한다:
    문제 1 (false-negative): fresh 프로세스에서 warmup 없이 즉시 connected
    문제 2 (false-positive): Redis 다운이 즉시 반영 (sticky 플래그 없음)
"""

import httpx
import pytest

from redis_manager.exceptions import RedisUnavailableError


def _connected(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/admin/health":
        return httpx.Response(200, json={"status": "connected", "url": "x", "redis_version": "7"})
    if request.url.path.endswith("/set"):
        return httpx.Response(200, json={"ok": True})
    return httpx.Response(200, json={"value": "v"})


def _redis_down(request: httpx.Request) -> httpx.Response:
    """handler는 살아있으나 Redis가 죽은 상태."""
    if request.url.path == "/v1/admin/health":
        return httpx.Response(200, json={"status": "error", "url": "x", "error": "down"})
    return httpx.Response(503, json={"error": "Redis 재시도 소진"})


def test_issue1_fresh_process_reports_connected_without_warmup(set_backend):
    """문제 1 역검증: get_client()/init_redis() 선행 호출 없이 즉시 connected."""
    manager = set_backend(_connected)
    result = manager.health_check()
    assert result["connected"] is True
    assert result["storage"] == "redis"


def test_issue2_redis_down_reported_immediately(set_backend):
    """문제 2 역검증: 한 번 connected였어도 Redis 다운이 즉시 반영."""
    manager = set_backend(_connected)
    assert manager.health_check()["connected"] is True

    manager = set_backend(_redis_down)
    result = manager.health_check()
    assert result["connected"] is False
    assert result["storage"] == "memory"


def test_handler_down_general_command_falls_back_to_memory(offline_manager):
    """handler 미도달: 일반 명령은 메모리 폴백 + 진단은 connected=False."""
    assert offline_manager.set("k", "v") is True   # 메모리 폴백
    assert offline_manager.get("k") == "v"
    assert offline_manager.health_check()["connected"] is False


def test_handler_down_nx_raises_unavailable(offline_manager):
    """handler 미도달: NX는 폴백 차단 — RedisUnavailableError."""
    with pytest.raises(RedisUnavailableError):
        offline_manager.setnx("lock", "owner", ttl=30)


def test_diagnosis_consistent_with_warmup_state(set_backend):
    """진단 결과가 호출 순서(warmup)에 무관함 — 같은 상태면 같은 결과."""
    manager = set_backend(_connected)
    before_any_command = manager.health_check()["connected"]
    manager.set("warm", "up")
    after_command = manager.health_check()["connected"]
    assert before_any_command == after_command is True
