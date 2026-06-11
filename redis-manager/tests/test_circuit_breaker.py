# -*- coding: utf-8 -*-
"""CircuitBreaker 단위 테스트 + HandlerBackend 통합 테스트 (sync).

통합 테스트(Task 2에서 추가)는 transport 호출 횟수를 세어
open 상태에서 HTTP 시도가 실제로 생략되는지 검증한다.
"""

import httpx
import pytest

from redis_manager._circuit import CircuitBreaker
from redis_manager.exceptions import RedisUnavailableError


# ── 단위: 상태 전이 ───────────────────────────────────────
def test_breaker_state_transitions(monkeypatch):
    t = {"now": 100.0}
    monkeypatch.setattr("redis_manager._circuit.time.monotonic", lambda: t["now"])
    cb = CircuitBreaker(failure_threshold=3, reset_timeout=2.0)

    assert cb.allow()                  # 초기: closed
    cb.record_failure()
    cb.record_failure()
    assert cb.allow()                  # 임계(3) 미달 — 아직 closed
    cb.record_failure()
    assert not cb.allow()              # open

    t["now"] += 2.0
    assert cb.allow()                  # 윈도우 경과 — half-open 프로브 허용
    cb.record_failure()                # 프로브 실패
    assert not cb.allow()              # 즉시 재-open

    t["now"] += 2.0
    assert cb.allow()
    cb.record_success()                # 프로브 성공 — 전체 리셋
    cb.record_failure()
    cb.record_failure()
    assert cb.allow()                  # 카운터가 0부터 다시 시작됐음을 입증


# ── 통합: sync HandlerBackend (offline_manager/set_backend fixture는 tests/conftest.py) ──
def _pin_breaker(mgr, threshold=3):
    """타이밍 비의존 검증을 위해 reset_timeout을 크게 고정."""
    mgr._backend._breaker = CircuitBreaker(failure_threshold=threshold, reset_timeout=60.0)
    return mgr


def test_command_path_skips_http_when_open(set_backend):
    calls = {"n": 0}

    def down(request):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    mgr = _pin_breaker(set_backend(down))
    for _ in range(3):
        assert mgr.get("k") is None    # transport 시도 → 실패 → 메모리 폴백
    assert calls["n"] == 3
    assert mgr.get("k") is None        # open — HTTP 시도 생략, 즉시 폴백
    assert calls["n"] == 3


def test_nx_raises_immediately_when_open(set_backend):
    calls = {"n": 0}

    def down(request):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    mgr = _pin_breaker(set_backend(down))
    for _ in range(3):
        mgr.get("k")
    assert calls["n"] == 3
    with pytest.raises(RedisUnavailableError):
        mgr.setnx("lock", "owner", ttl=30)   # open — 즉발, transport 미시도
    assert calls["n"] == 3


def test_health_bypasses_open_circuit(set_backend):
    """진단 경로는 open 중에도 live — #6 정신 보존."""
    state = {"n": 0}

    def flaky(request):
        state["n"] += 1
        if state["n"] <= 3:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"status": "connected", "url": "redis://x"})

    mgr = _pin_breaker(set_backend(flaky))
    for _ in range(3):
        mgr.get("k")                   # 3연속 실패 → open
    assert state["n"] == 3
    assert mgr.ping() is True          # 진단은 브레이커 우회 — 4번째 transport 호출
    assert state["n"] == 4
    mgr.get("k")                       # 명령 경로는 여전히 open
    assert state["n"] == 4


def test_success_resets_breaker(set_backend):
    state = {"n": 0}

    def recover_after_2(request):
        state["n"] += 1
        if state["n"] <= 2:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"value": "v"})

    mgr = _pin_breaker(set_backend(recover_after_2))
    mgr.get("k")
    mgr.get("k")                       # 실패 2회 (임계 미달 — closed 유지)
    assert mgr.get("k") == "v"         # 3번째 성공 → 카운터 리셋
    assert state["n"] == 3
    assert mgr.get("k") == "v"         # 계속 정상
