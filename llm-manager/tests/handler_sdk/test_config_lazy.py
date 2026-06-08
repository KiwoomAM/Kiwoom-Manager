"""_config.get_settings() lazy 로딩 검증.

handler_url 은 모듈 import 시점이 아니라 **첫 사용 시점**에 env 를 읽어야 한다.
소비자(llm-manager 등)가 import 이후 LLM_HANDLER_URL 을 세팅하는 경우를 위해서다.
"""

from __future__ import annotations

import pytest

import llm_manager._internal.handler_client._config as cfg


@pytest.fixture(autouse=True)
def _reset_settings():
    """각 테스트 전후로 lazy 싱글턴을 비워 격리."""
    cfg._settings = None
    yield
    cfg._settings = None


def test_reads_env_at_first_call_not_import(monkeypatch):
    """import 이후 세팅한 env 도 첫 get_settings() 호출에 반영됨 (동결 X)."""
    monkeypatch.setenv("LLM_HANDLER_URL", "http://late-set:9999")

    settings = cfg.get_settings()

    assert settings.handler_url == "http://late-set:9999"


def test_memoized_after_first_call(monkeypatch):
    """첫 호출 이후엔 1회 로드값으로 고정 (매번 재로드 안 함)."""
    monkeypatch.setenv("LLM_HANDLER_URL", "http://first:8100")
    first = cfg.get_settings()

    monkeypatch.setenv("LLM_HANDLER_URL", "http://second:8100")
    second = cfg.get_settings()

    assert first is second
    assert second.handler_url == "http://first:8100"


def test_default_when_env_absent(monkeypatch):
    """env 없으면 기본값(localhost:8100)."""
    monkeypatch.delenv("LLM_HANDLER_URL", raising=False)

    assert cfg.get_settings().handler_url == "http://localhost:8100"
