"""SDK 테스트 공통 fixture."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def reset_sdk():
    """각 테스트 전 SDK 싱글톤 리셋."""
    import llm_request
    llm_request._instance = None
    yield
    llm_request._instance = None
