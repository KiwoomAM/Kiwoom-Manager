"""gemini_errors 판정 유틸 테스트."""

from llm_manager._internal.gemini_errors import is_cache_too_small, is_gemini_file_unavailable
from llm_manager._internal.handler_client.exceptions import HandlerError

# ── 첨부 재업로드 신호 ─────────────────────────────────────────


def test_gemini_404_is_file_unavailable():
    e = HandlerError("File not found", status_code=404, error_type="provider")
    assert is_gemini_file_unavailable(e) is True


def test_handler_ops_403_is_file_unavailable():
    """지워진 Files API 파일에 Gemini 는 403 을 주고, handler 는 502 handler_ops 로 바꾼다.

    2026-09-17 실측 메시지 그대로 — 상태만 보면 502 라 404 판정으로는 안 걸린다.
    """
    e = HandlerError("Gemini stream 권한/인증 실패 (gemini_status=403)",
                     status_code=502, error_type="handler_ops")
    assert is_gemini_file_unavailable(e) is True


def test_unrelated_403_is_not_file_unavailable():
    """Gemini 가 낸 403 이 아니면 재업로드 신호가 아니다 — 남의 403 에 파일을 다시 올리지 않는다."""
    e = HandlerError("403 Forbidden", status_code=403, error_type="transport")
    assert is_gemini_file_unavailable(e) is False


def test_rate_limit_is_not_file_unavailable():
    e = HandlerError("429 RESOURCE_EXHAUSTED: quota", status_code=429, error_type="provider")
    assert is_gemini_file_unavailable(e) is False


def test_too_small_handler_error_true():
    e = HandlerError(
        "Gemini 캐시 생성 에러: 400 INVALID_ARGUMENT. Cached content is too small. "
        "total_token_count=561, min_total_token_count=1024",
        status_code=400, error_type="provider",
    )
    assert is_cache_too_small(e) is True


def test_other_400_false():
    e = HandlerError("400 INVALID_ARGUMENT: some other reason",
                     status_code=400, error_type="provider")
    assert is_cache_too_small(e) is False


def test_503_false():
    e = HandlerError("503 service unavailable", status_code=503, error_type="provider")
    assert is_cache_too_small(e) is False


def test_raw_exception_with_marker_true():
    # llm-handler 가 wrap 못한 raw 예외라도 메시지 마커로 잡는다.
    assert is_cache_too_small(ValueError("min_total_token_count=1024")) is True
