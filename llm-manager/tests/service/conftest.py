"""Service 테스트 공통 fixture."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    """FastAPI TestClient."""
    return TestClient(app)


@pytest.fixture()
def mock_collection():
    """MongoDB 컬렉션을 인메모리 dict 기반으로 모킹."""
    store: dict[str, dict] = {}

    col = MagicMock()

    def _find_one(query: dict):
        _id = query.get("_id")
        return store.get(_id)

    def _replace_one(filter_dict: dict, doc: dict, upsert: bool = False):
        _id = filter_dict.get("_id")
        if _id:
            store[_id] = doc

    col.find_one = MagicMock(side_effect=_find_one)
    col.replace_one = MagicMock(side_effect=_replace_one)

    with patch("app.logic.store._get_collection", return_value=col):
        yield store
