"""메타정보 생성."""

from __future__ import annotations


def build_meta(llm_input: dict) -> dict:
    """llm_input에서 메타정보 dict를 생성한다.

    Args:
        llm_input: llm_handler.call()에 전달할 인자 dict.

    Returns:
        저장/반환에 포함될 메타 dict.
    """
    return {
        "service_name": llm_input.get("service_name"),
        "model": llm_input.get("model"),
    }
