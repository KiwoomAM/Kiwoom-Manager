"""Gemini 예외 분류 유틸 — runner / context_cache_lifecycle 공유."""

from __future__ import annotations

from llm_manager._internal.handler_client.exceptions import HandlerError


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


def is_gemini_file_unavailable(exc: Exception) -> bool:
    """첨부 주소 기록을 지우고 다시 올릴 신호인지 — Gemini 가 그 파일을 못 쓴다고 진 것.

    404 만 보면 놓친다. 지워지거나 만료된 Files API 파일을 참조하면 Gemini 는 403 을 주고,
    llm-handler 는 401·403 을 자기 운영 문제로 분류해 `handler_ops`(502)로 바꿔 올린다
    (Kiwoom-Handler `llm-handler/service/app/core/errors.py` 의 `classify_api_error`). 그래서
    상태 코드로는 갈라지지 않고 메시지에 실려 오는 `gemini_status=403` 까지 본다
    (2026-09-17 실측 메시지: "Gemini stream 권한/인증 실패 (gemini_status=403)").

    API 키·쿼터 문제도 같은 모양이라 함께 걸린다. 그때는 재업로드가 같은 이유로 실패해 원래
    예외가 올라가므로, 대가는 헛돈 업로드 한 번이다(복구는 1회로 제한된다).

    Context Cache 만료 처리는 이 판정을 쓰지 않는다 — 거기서 403 을 "이미 없음"으로 보면
    인증 실패가 조용한 성공이 된다. 그쪽은 `is_gemini_404` 를 그대로 쓴다.
    """
    if is_gemini_404(exc):
        return True
    return "gemini_status=403" in str(exc)


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
