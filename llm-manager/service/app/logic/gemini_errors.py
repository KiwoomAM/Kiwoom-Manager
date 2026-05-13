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
