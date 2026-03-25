"""캐시 키 생성: canonicalize → hash.

LLM 입력(llm_input subset)을 결정적(deterministic) JSON 문자열로 변환하고,
해시 기반 캐시 키를 생성한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

# ── 상수 / 설정 ──────────────────────────────────────────────

# llm_handler.call() / aio.call() 인자와 동일 (캐시 키용 subset)
_LLM_INPUT_FIELDS: tuple[str, ...] = (
    "contents",
    "system",
    "history",
    "model",
    "max_tokens",
    "temperature",
    "cached_content",
    "service_name",
    "seed",
    "output",
    "schema",
    "thinking",
    "tools",
    "tool_config",
    "media_resolution",
)
# 살릴 소수점 자리수
DEFAULT_FLOAT_NDIGITS: int = 4
# 리스트 순서 유지 여부 False일 때 정렬하여 순서 무시
DEFAULT_PRESERVE_LIST_ORDER: bool = True
# 무시할 키 집합 (time정보가 포함되어 매번 없는 cache로 조회되는 것 방지)
DEFAULT_VOLATILE_KEYS: frozenset[str] = frozenset(
    {
        "timestamp",
        "ts",
        "created_at",
        "updated_at",
        "request_id",
        "trace_id",
    }
)


# ── A. subset 생성 헬퍼 ──────────────────────────────────────


def build_llm_input_subset(
    llm_input: dict,
    fields: tuple[str, ...] = _LLM_INPUT_FIELDS,
) -> dict:
    """llm_input에서 지정 필드만 추출하여 subset dict를 만든다.

    None 값은 빈 문자열("")로 치환하여 키를 유지한다.

    Args:
        llm_input: 원본 LLM 입력 dict.
        fields: 추출할 필드 이름 tuple.

    Returns:
        필요한 필드만 포함된 dict.
    """
    subset = {k: llm_input[k] for k in fields if k in llm_input}
    subset = {k: ("" if v is None else v) for k, v in subset.items()}
    return subset


# ── B. canonicalize ──────────────────────────────────────────


def canonicalize_llm_input(
    llm_input: dict,
    *,
    float_ndigits: int = DEFAULT_FLOAT_NDIGITS,
    preserve_list_order: bool = DEFAULT_PRESERVE_LIST_ORDER,
    volatile_keys: frozenset[str] = DEFAULT_VOLATILE_KEYS,
) -> str:
    """llm_input subset dict를 결정적(deterministic) JSON 문자열로 직렬화한다.

    키 정렬·None→빈문자열 치환·volatile 키 제거·float 라운딩 등 normalize를 수행하여
    동일 의미의 입력은 항상 동일 문자열을 반환한다.

    Args:
        llm_input: _LLM_INPUT_FIELDS만 포함된 subset dict.
        float_ndigits: float 라운딩 자릿수.
        preserve_list_order: False이면 리스트를 정렬하여 순서 무시.
        volatile_keys: 무시할 volatile 키 집합.

    Returns:
        canonical JSON 문자열. 예외 시에도 항상 문자열을 반환한다.
    """

    def _normalize(obj: Any) -> Any:  # noqa: C901
        if isinstance(obj, dict):
            result: dict[str, Any] = {}
            for k, v in obj.items():
                sk = str(k)
                if sk in volatile_keys:
                    continue
                normalized_v = _normalize(v)
                if normalized_v is None:
                    normalized_v = ""
                result[sk] = normalized_v
            return result

        if isinstance(obj, (list, tuple)):
            items = [_normalize(elem) for elem in obj]
            if not preserve_list_order:
                items.sort(
                    key=lambda e: json.dumps(
                        e,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                )
            return items

        if isinstance(obj, float):
            return round(obj, float_ndigits)

        if isinstance(obj, Decimal):
            return str(obj.normalize())

        if isinstance(obj, datetime):
            return obj.isoformat()

        if isinstance(obj, date):
            return obj.isoformat()

        if isinstance(obj, (bytes, bytearray)):
            return obj.hex()

        if isinstance(obj, (str, int, bool)) or obj is None:
            return obj

        try:
            return str(obj)
        except Exception:
            return ""

    try:
        normalized = _normalize(llm_input)
        return json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    except Exception:
        try:
            return json.dumps(
                str(llm_input),
                ensure_ascii=False,
            )
        except Exception:
            return "{}"


# ── C. wrapper ───────────────────────────────────────────────


def canonicalize_input(llm_input: dict, **kwargs: Any) -> str:
    """llm_input에서 subset을 만들고 canonical JSON 문자열을 반환한다.

    Args:
        llm_input: llm_handler.call()에 전달할 인자 dict.
        **kwargs: canonicalize_llm_input에 전달할 추가 옵션.

    Returns:
        canonical JSON 문자열.
    """
    subset = build_llm_input_subset(llm_input)
    return canonicalize_llm_input(subset, **kwargs)


# ── hash 기반 캐시 키 ────────────────────────────────────────

_WARN_CANONICAL_VALUES = (None, "", "{}")


def make_cache_key(canonical_json: str, *, debug: bool = False) -> str:
    """canonical JSON 문자열로부터 SHA-256 해시 기반 캐시 키를 생성한다.

    Args:
        canonical_json: canonicalize_llm_input의 반환값(UTF-8 문자열).
        debug: True이면 비정상 입력 시 경고를 출력한다.

    Returns:
        길이 64의 lowercase hex SHA-256 digest 문자열.
    """
    if not isinstance(canonical_json, str):
        canonical_json = str(canonical_json)

    if debug and canonical_json in _WARN_CANONICAL_VALUES:
        logger.warning("캐시 키 생성: 값이 비어있거나 유효하지 않습니다. len=%d", len(canonical_json))

    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
