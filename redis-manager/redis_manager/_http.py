# -*- coding: utf-8 -*-
"""sync/async HandlerBackend 공용 순수 헬퍼 (I/O 없음).

handler HTTP 응답 → 도메인 예외 매핑과 키 경로 인코딩을 한 곳에 둔다.
httpx.Response를 받지만 네트워크 I/O는 하지 않는다 — .json()/.text는 이미
수신된 본문 접근이라 sync/async 양쪽에서 동일하게 안전하다.
"""

from urllib.parse import quote

import httpx

from .exceptions import RedisHandlerError, RedisRetryExhausted


def key_path(key: str) -> str:
    """키를 URL 경로 세그먼트로 인코딩 ('/' 등 특수문자 포함)."""
    return quote(key, safe="")


def error_message(resp: httpx.Response) -> str:
    """오류 응답 본문에서 {"error": ...} 추출, 실패 시 raw text."""
    try:
        return resp.json().get("error", resp.text)
    except Exception:  # noqa: BLE001 — 비JSON 오류 응답 방어
        return resp.text


def raise_for_handler_status(resp: httpx.Response) -> None:
    """handler 응답 status를 도메인 예외로 매핑. 2xx면 통과(반환 없음).

    503 → RedisRetryExhausted (handler가 Redis 재시도 소진 보고)
    그 외 4xx/5xx → RedisHandlerError
    """
    if resp.status_code == 503:
        raise RedisRetryExhausted(
            f"redis-handler: Redis 재시도 소진 — {error_message(resp)}"
        )
    if resp.status_code >= 400:
        raise RedisHandlerError(
            f"redis-handler 오류 ({resp.status_code}): {error_message(resp)}"
        )
