"""테스트 공통 헬퍼 — `from tests.helpers import ...` 로 쓴다.

**conftest 에 두지 않는 이유.** pytest 는 각 폴더의 `conftest.py` 를 모두 `conftest` 라는
같은 모듈 이름으로 올린다. 그래서 `tests/_internal/conftest.py` 가 있는 상태에서
`from conftest import ...` 를 다른 폴더의 테스트가 쓰면, 수집 순서에 따라 엉뚱한 conftest 가
잡혀 `ImportError` 로 수집이 통째로 깨진다(같은 폴더 안에서 쓸 때만 안전하다).
평범한 모듈로 두고 `tests.helpers` 라는 고유 경로로 부르면 그 충돌이 구조적으로 없다.
"""
from __future__ import annotations

import llm_manager


def _patch_stream(monkeypatch, inner_factory, meta=None):
    """공개 `llm_request_stream_async` 가 주어진 async 제너레이터를 쓰도록 갈아끼운다.

    `test_sync_stream.py`(정리 계약·증분성)와 `test_public_stream_events.py`(필드 통과)가
    같은 패치 방식을 쓰므로 여기 한 곳에 둔다. 사본을 두면 한쪽만 고쳐져 어긋난다.
    """
    monkeypatch.setattr(llm_manager, "ensure_configured", lambda: None)

    async def fake_runner_stream(*args, **kwargs):
        return inner_factory(), (meta if meta is not None else {"source": "live"})

    monkeypatch.setattr(
        llm_manager.runner, "llm_request_stream_async", fake_runner_stream,
    )
