# -*- coding: utf-8 -*-
"""공통 pytest fixture — VDBHandler Singleton 상태 초기화."""

import pytest


@pytest.fixture(autouse=True)
def reset_vdb_singleton():
    """각 테스트 전후로 VDBHandler Singleton을 비워 격리."""
    from vdb_manager.handler import VDBHandler

    VDBHandler._instance = None
    yield
    VDBHandler._instance = None
