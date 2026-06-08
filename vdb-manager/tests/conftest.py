# -*- coding: utf-8 -*-
"""공통 pytest fixture — VDBManager Singleton 상태 초기화."""

import pytest


@pytest.fixture(autouse=True)
def reset_vdb_singleton():
    """각 테스트 전후로 VDBManager Singleton을 비워 격리."""
    from vdb_manager.handler import VDBManager

    VDBManager._instance = None
    yield
    VDBManager._instance = None
