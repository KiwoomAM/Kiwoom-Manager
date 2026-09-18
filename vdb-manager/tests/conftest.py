# -*- coding: utf-8 -*-
"""공통 pytest fixture — VDBManager Singleton 상태 초기화와 HTTP 더블."""

from typing import Any, Dict, List, Tuple

import pytest

from vdb_manager.collection import CollectionClient


@pytest.fixture(autouse=True)
def reset_vdb_singleton():
    """각 테스트 전후로 VDBManager Singleton을 비워 격리."""
    from vdb_manager.handler import VDBManager

    VDBManager._instance = None
    yield
    VDBManager._instance = None


class FakeClient:
    """HTTP 메서드별 호출의 경로와 body 를 기록하는 테스트 더블.

    `SyncClient` 자리에 주입해 "어느 경로로 어떤 body 가 갔는지" 를 본다. 특히
    호출된 HTTP 메서드를 기록하는 것이 중요하다. payload 병합과 전체 교체는 경로와
    body 모양이 같고 메서드만 다르므로, 메서드를 보지 않으면 둘이 뒤바뀌어도
    테스트가 통과한다.

    `tests/test_collection_points.py` 에도 같은 이름의 오래된 사본이 있다. 그쪽은
    `patch`·`put` 을 모르며, 통합은 클라이언트 경계를 Protocol 로 세우는 #108 에서
    함께 한다.
    """

    def __init__(self, response: Any = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.response = response

    def _record(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append({"method": method, "path": path, **kwargs})
        return self.response

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._record("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Any:
        return self._record("POST", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> Any:
        return self._record("PATCH", path, **kwargs)

    def put(self, path: str, **kwargs: Any) -> Any:
        return self._record("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self._record("DELETE", path, **kwargs)


@pytest.fixture
def make_collection():
    """`CollectionClient` 와 그것이 쓴 더블을 함께 돌려주는 팩토리."""

    def _make(response: Any = None) -> Tuple[CollectionClient, FakeClient]:
        client = FakeClient(response)
        return CollectionClient(client), client  # type: ignore[arg-type]

    return _make
