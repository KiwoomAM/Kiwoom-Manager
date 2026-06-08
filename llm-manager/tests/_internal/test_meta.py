"""meta.py 테스트 — 메타정보 추출."""

from __future__ import annotations

from llm_manager._internal.meta import build_meta


class TestBuildMeta:

    def test_extracts_service_name_and_model(self):
        result = build_meta({"service_name": "my-agent", "model": "flash", "contents": "hi"})
        assert result == {"service_name": "my-agent", "model": "flash"}

    def test_missing_fields_return_none(self):
        result = build_meta({"contents": "hi"})
        assert result == {"service_name": None, "model": None}

    def test_empty_input(self):
        result = build_meta({})
        assert result == {"service_name": None, "model": None}
