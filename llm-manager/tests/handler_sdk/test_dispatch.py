"""SDK error_type → exception sub-class dispatch 검증.

handler 가 보낸 error_type 문자열이 SDK 의 정확한 exception class 로 매핑되어
caller 가 `isinstance(e, TransportError)` 같은 분기를 할 수 있는지 검증.

backward compat: 구버전 error_type ("handler", None) → HandlerError base fallback.
"""

import pytest

from llm_manager._internal.handler_client.exceptions import (
    ContentBlockedError,
    HandlerError,
    HandlerInternalError,
    HandlerOpsError,
    ProviderError,
    TransportError,
    ValidationError,
    from_error_type,
)


class TestFromErrorType:
    """from_error_type 의 error_type → class 매핑."""

    @pytest.mark.parametrize(
        "error_type, expected_cls",
        [
            ("validation", ValidationError),
            ("content_blocked", ContentBlockedError),
            ("transport", TransportError),
            ("provider", ProviderError),
            ("handler_ops", HandlerOpsError),
            ("handler_internal", HandlerInternalError),
        ],
    )
    def test_each_error_type_returns_correct_subclass(
        self, error_type: str, expected_cls: type[HandlerError]
    ):
        exc = from_error_type("msg", 500, error_type)
        assert type(exc) is expected_cls
        assert isinstance(exc, HandlerError)

    def test_unknown_error_type_falls_back_to_base(self):
        """알 수 없는 error_type → HandlerError base (silent fallback)."""
        exc = from_error_type("msg", 500, "future_unknown_type")
        assert type(exc) is HandlerError

    def test_none_error_type_falls_back_to_base(self):
        """error_type=None (구버전 handler 응답) → HandlerError base."""
        exc = from_error_type("msg", 500, None)
        assert type(exc) is HandlerError

    def test_legacy_handler_error_type_falls_back_to_base(self):
        """legacy 'handler' error_type (구버전 응답 호환) → HandlerError base fallback."""
        exc = from_error_type("msg", 500, "handler")
        assert type(exc) is HandlerError

    def test_attributes_preserved(self):
        exc = from_error_type("custom msg", 504, "transport")
        assert exc.message == "custom msg"
        assert exc.status_code == 504
        assert exc.error_type == "transport"


class TestDetailCapture:
    """wire `detail` slug 가 SDK 예외까지 보존됨 (운영 alert 라우팅용)."""

    def test_detail_captured_when_present(self):
        """handler_internal 의 sub-cause slug 가 detail 속성으로 보존."""
        exc = from_error_type("내부 서버 오류", 500, "handler_internal", "pool_timeout")
        assert exc.detail == "pool_timeout"

    def test_detail_defaults_none_when_absent(self):
        """detail 미전달 (구버전/일반 케이스) → None."""
        exc = from_error_type("msg", 502, "provider")
        assert exc.detail is None

    def test_detail_on_base_fallback(self):
        """unknown error_type fallback (HandlerError base) 도 detail 보존."""
        exc = from_error_type("msg", 500, "handler", "LocalProtocolError")
        assert type(exc) is HandlerError
        assert exc.detail == "LocalProtocolError"


class TestOriginProperty:
    """HandlerError.origin 의 4 origin grouping 검증 — alert 라우팅 / 사용자 메시지용."""

    @pytest.mark.parametrize(
        "error_type, expected_origin",
        [
            ("validation", "caller"),
            ("content_blocked", "caller"),
            ("transport", "transport"),
            ("provider", "provider"),
            ("handler_ops", "handler"),
            ("handler_internal", "handler"),
            ("handler", "handler"),  # 구버전 호환
        ],
    )
    def test_origin_grouping(self, error_type: str, expected_origin: str):
        exc = from_error_type("msg", 500, error_type)
        assert exc.origin == expected_origin

    def test_unknown_error_type_origin_is_unknown(self):
        exc = from_error_type("msg", 500, "future_type")
        assert exc.origin == "unknown"


class TestSubclassCatchability:
    """`except HandlerError` 가 모든 sub-class 를 catch 함 (backward compat)."""

    @pytest.mark.parametrize(
        "error_type",
        [
            "validation",
            "content_blocked",
            "transport",
            "provider",
            "handler_ops",
            "handler_internal",
        ],
    )
    def test_handler_error_catches_all_subclasses(self, error_type: str):
        try:
            raise from_error_type("msg", 500, error_type)
        except HandlerError as e:
            assert e.error_type == error_type
        else:
            pytest.fail("HandlerError should have caught the exception")

    def test_specific_subclass_does_not_catch_other_subclass(self):
        """ValidationError catch 가 TransportError 를 잡지 않음."""
        exc = from_error_type("net", 502, "transport")
        with pytest.raises(TransportError):
            try:
                raise exc
            except ValidationError:
                pytest.fail("ValidationError should not catch TransportError")
