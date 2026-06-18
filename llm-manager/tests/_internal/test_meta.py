"""meta.py 테스트 — 메타정보 추출 + 요청 앞단 env 가드."""

from __future__ import annotations

import pytest

from llm_manager._internal.meta import build_meta, require_service_name
from llm_manager.exceptions import ValidationError


class TestBuildMeta:

    def test_service_name_from_env_task_name_from_input(self, monkeypatch):
        monkeypatch.setenv("SERVICE_NAME", "esg-evaluator")
        result = build_meta(
            {"task_name": "summary", "model": "flash", "contents": "hi"}
        )
        assert result == {
            "service_name": "esg-evaluator",
            "task_name": "summary",
            "model": "flash",
        }

    def test_service_name_none_when_env_missing(self, monkeypatch):
        """build_meta 자체는 강제하지 않는다 — env 미설정 시 None (강제는 require_service_name)."""
        monkeypatch.delenv("SERVICE_NAME", raising=False)
        result = build_meta({"task_name": "summary", "model": "flash"})
        assert result == {
            "service_name": None,
            "task_name": "summary",
            "model": "flash",
        }

    def test_task_name_and_model_missing_return_none(self, monkeypatch):
        monkeypatch.setenv("SERVICE_NAME", "esg-evaluator")
        result = build_meta({"contents": "hi"})
        assert result == {
            "service_name": "esg-evaluator",
            "task_name": None,
            "model": None,
        }


class TestRequireServiceName:
    """요청 앞단(runner.prepare) fail-fast — env SERVICE_NAME 강제."""

    def test_returns_env_value(self, monkeypatch):
        monkeypatch.setenv("SERVICE_NAME", "kiwoom-rm")
        assert require_service_name() == "kiwoom-rm"

    def test_raises_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("SERVICE_NAME", raising=False)
        with pytest.raises(ValidationError):
            require_service_name()

    def test_raises_when_env_empty(self, monkeypatch):
        monkeypatch.setenv("SERVICE_NAME", "")
        with pytest.raises(ValidationError):
            require_service_name()
