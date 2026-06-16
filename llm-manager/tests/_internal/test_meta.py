"""meta.py 테스트 — 메타정보 추출 + 저장 직전 가드."""

from __future__ import annotations

import pytest

from llm_manager._internal.meta import build_meta, require_service_name_for_save
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
        """build_meta 자체는 강제하지 않는다 — 미설정 시 None (조회 경로 허용)."""
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


class TestRequireServiceNameForSave:

    def test_passes_when_service_name_present(self):
        require_service_name_for_save(
            {"service_name": "esg-evaluator", "task_name": "summary"}
        )  # raise 없으면 통과

    def test_raises_when_service_name_none(self):
        with pytest.raises(ValidationError):
            require_service_name_for_save({"service_name": None, "task_name": "x"})

    def test_raises_when_service_name_missing_key(self):
        with pytest.raises(ValidationError):
            require_service_name_for_save({"task_name": "x"})

    def test_raises_when_service_name_empty(self):
        with pytest.raises(ValidationError):
            require_service_name_for_save({"service_name": ""})
