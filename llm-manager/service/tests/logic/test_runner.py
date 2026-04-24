"""runner._apply_resolved 단위 테스트.

Context Cache 사용 시 request-level 필드 중복 제거 검증:
- cached_content 없음 → inline_parts + uri_parts + text 모두 contents 에 포함
- cached_content 있음 → uri_parts 제외 + system/tools/tool_config 제거
  (이것들은 cache 에 이미 포함돼 있어 request 중복 전송 시 토큰 중복 과금 +
  Gemini INVALID_ARGUMENT)
"""

from __future__ import annotations

from llm_handler.models import Part

from app.logic.file_processing.resolver import ResolvedFiles
from app.logic.runner import _apply_resolved


# ── 헬퍼 ──────────────────────────────────────────────────────


def _make_inline_part() -> Part:
    return Part.from_bytes(data=b"x", mime_type="image/png")


def _make_uri_part(uri: str = "https://example.com/test.pdf") -> Part:
    return Part.from_uri(uri, mime_type="application/pdf")


# ── cached_content 없는 경로 ──────────────────────────────────


class TestApplyResolvedWithoutContextCache:
    """기본 경로 — cached_content 없음. 모든 parts 가 contents 에 합쳐짐."""

    def test_inline_and_uri_parts_both_injected_into_contents(self):
        resolved = ResolvedFiles(
            inline_parts=[_make_inline_part()],
            uri_parts=[_make_uri_part()],
            cached_content=None,
        )
        llm_input = {"contents": "분석해줘", "model": "gemini-2.5-flash"}

        result = _apply_resolved(llm_input, resolved)

        # inline + uri + text 순서로 합쳐짐
        assert len(result["contents"]) == 3
        assert result["contents"][-1] == "분석해줘"
        assert "cached_content" not in result

    def test_no_parts_keeps_only_text(self):
        resolved = ResolvedFiles(inline_parts=[], uri_parts=[], cached_content=None)
        llm_input = {"contents": "텍스트만"}

        result = _apply_resolved(llm_input, resolved)

        assert result["contents"] == ["텍스트만"]

    def test_system_and_tools_preserved_when_no_cache(self):
        """cached_content 없으면 system/tools/tool_config 는 그대로 보존."""
        resolved = ResolvedFiles(
            inline_parts=[],
            uri_parts=[_make_uri_part()],
            cached_content=None,
        )
        llm_input = {
            "contents": "x",
            "system": "너는 금융 전문가",
            "tools": [{"name": "calculator"}],
            "tool_config": "auto",
        }

        result = _apply_resolved(llm_input, resolved)

        assert result["system"] == "너는 금융 전문가"
        assert result["tools"] == [{"name": "calculator"}]
        assert result["tool_config"] == "auto"


# ── cached_content 있는 경로 — 중복 제거 ──────────────────────


class TestApplyResolvedWithContextCache:
    """context cache 사용 경로 — 중복 필드 제거가 핵심."""

    def test_cached_content_set_and_uri_parts_excluded_from_contents(self):
        """cached_content 있으면 uri_parts 는 cache 안에 있으니 contents 에서 제외."""
        resolved = ResolvedFiles(
            inline_parts=[_make_inline_part()],
            uri_parts=[_make_uri_part()],
            cached_content="cachedContents/abc",
        )
        llm_input = {"contents": "분석해줘", "model": "gemini-2.5-flash"}

        result = _apply_resolved(llm_input, resolved)

        assert result["cached_content"] == "cachedContents/abc"
        # inline_parts + text 만 (uri_parts 제외)
        assert len(result["contents"]) == 2
        assert result["contents"][-1] == "분석해줘"

    def test_cached_content_removes_system_tools_tool_config(self):
        """cached_content 있으면 system/tools/tool_config 제거 (cache 에 이미 포함)."""
        resolved = ResolvedFiles(
            inline_parts=[],
            uri_parts=[_make_uri_part()],
            cached_content="cachedContents/abc",
        )
        llm_input = {
            "contents": "x",
            "model": "gemini-2.5-flash",
            "system": "너는 금융 전문가",
            "tools": [{"name": "calculator"}],
            "tool_config": "auto",
        }

        result = _apply_resolved(llm_input, resolved)

        assert "system" not in result
        assert "tools" not in result
        assert "tool_config" not in result
        assert result["cached_content"] == "cachedContents/abc"
        assert result["model"] == "gemini-2.5-flash"  # model 은 유지

    def test_cached_content_preserves_inline_parts(self):
        """inline_parts 는 cache 에 안 들어가므로 cached_content 여도 유지."""
        inline = _make_inline_part()
        resolved = ResolvedFiles(
            inline_parts=[inline],
            uri_parts=[_make_uri_part()],
            cached_content="cachedContents/abc",
        )
        llm_input = {"contents": "질문"}

        result = _apply_resolved(llm_input, resolved)

        assert inline in result["contents"]
        assert "질문" in result["contents"]


# ── 공통: file_checksums 제거 ─────────────────────────────────


class TestApplyResolvedCommon:
    def test_file_checksums_always_removed(self):
        """cached_content 유무 관계없이 file_checksums 는 제거."""
        resolved = ResolvedFiles(inline_parts=[], uri_parts=[], cached_content=None)
        llm_input = {"contents": "x", "file_checksums": ["abc123"]}

        result = _apply_resolved(llm_input, resolved)

        assert "file_checksums" not in result

    def test_file_checksums_removed_with_cache(self):
        resolved = ResolvedFiles(
            inline_parts=[],
            uri_parts=[],
            cached_content="cachedContents/abc",
        )
        llm_input = {"contents": "x", "file_checksums": ["abc123"]}

        result = _apply_resolved(llm_input, resolved)

        assert "file_checksums" not in result
