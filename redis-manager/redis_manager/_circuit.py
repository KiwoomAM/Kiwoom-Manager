# -*- coding: utf-8 -*-
"""경량 서킷 브레이커 — handler 미도달(transport) 실패 전용 (Issue #11).

연속 실패가 임계치에 도달하면 reset_timeout 동안 HTTP 시도를 건너뛰어,
블랙홀(timeout) 장애 시 호출마다 HANDLER_TIMEOUT을 소진하는 누적을 방지한다.
순수 상태 객체(I/O 없음) — sync/async 백엔드가 공용으로 사용한다.

적용 범위: 명령 경로 전용. 진단 경로(health/ping)는 적용 제외 —
"매 호출 실제 상태 반영"(#6 live 진단)을 보존한다.
"""

import threading
import time


class CircuitBreaker:
    """연속 실패 카운터 + open 윈도우.

    - allow(): open 윈도우 안이면 False (호출 건너뛰기). 윈도우 경과 후에는
      True를 반환해 half-open 프로브를 허용한다.
    - record_failure(): 카운터 증가, 임계 도달 시 윈도우 갱신. half-open
      프로브 실패 시 카운터가 이미 임계 이상이므로 즉시 재-open된다.
    - record_success(): 전체 리셋 (closed 복귀).

    threading.Lock 보호 — sync 멀티스레드 환경 대비. async 단일 루프에서도
    획득 시간이 µs 수준이라 무해하다.
    """

    def __init__(self, failure_threshold: int = 3, reset_timeout: float = 1.0):
        self._threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._failures = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """호출 허용 여부. open 윈도우 중에는 False."""
        with self._lock:
            return time.monotonic() >= self._open_until

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold:
                self._open_until = time.monotonic() + self._reset_timeout

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0


__all__ = ["CircuitBreaker"]
