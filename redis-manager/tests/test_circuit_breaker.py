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
