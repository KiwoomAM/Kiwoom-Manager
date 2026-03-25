"""메타정보 생성."""

from __future__ import annotations


def build_meta(state: dict) -> dict:
    """state에서 메타정보 dict를 생성한다.

    Args:
        state: 요청 전체 상태 dict.

    Returns:
        저장/반환에 포함될 메타 dict.
    """
    llm_input = state.get("llm_input", {})
    return {
        "service_name": llm_input.get("service_name"),
        "model": llm_input.get("model"),
        "provider": llm_input.get("provider"),
    }
