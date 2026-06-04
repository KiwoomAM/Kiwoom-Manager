"""gemini_errors 판정 유틸 테스트."""

from llm_handler.exceptions import HandlerError

from llm_request._internal.gemini_errors import is_cache_too_small


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
