"""Gemini 예외 분류 유틸 — runner / context_cache_lifecycle 공유."""

from __future__ import annotations

from llm_handler.exceptions import HandlerError


def is_gemini_404(exc: Exception) -> bool:
    """Gemini 호출에서 '리소스 없음 (404)' 신호인지 판정.

    우선순위:
      1. HandlerError 의 status_code == 404 (정확 — llm-handler 가 Gemini HTTP
         status 그대로 전파).
      2. 그 외 raw 예외는 메시지 substring fallback ("404" / "not found") —
         llm-handler 가 wrap 하지 못한 경로 / 옛 코드 호환.
    """
    if isinstance(exc, HandlerError) and exc.status_code == 404:
        return True
    msg = str(exc).lower()
    return "404" in msg or "not found" in msg


def is_cache_too_small(exc: Exception) -> bool:
    """Gemini Context Cache 'min_total_token_count' 미달(400) 신호인지 판정.

    캐시 생성에 필요한 최소 토큰(현재 1024) 미달 시 Gemini 가 반환하는
    400 INVALID_ARGUMENT 를 식별. caller 는 이 경우 캐시를 skip(fail-soft)한다.
    `min_total_token_count` 는 매우 특정한 토큰이라 다른 400 을 잘못 삼키지 않는다.

    우선순위:
      1. HandlerError 의 status_code == 400 + 메시지에 min_total_token_count.
      2. wrap 못한 raw 예외는 메시지 substring fallback.
    """
    if (
        isinstance(exc, HandlerError)
        and exc.status_code == 400
        and "min_total_token_count" in str(exc).lower()
    ):
        return True
    return "min_total_token_count" in str(exc).lower()
