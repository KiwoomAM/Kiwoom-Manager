"""메타정보 생성."""

from __future__ import annotations

import os

from llm_manager.exceptions import ValidationError

# env SERVICE_NAME 미설정 시 fail-fast 메시지.
# mdb-manager 가 envelope 진입점에서 caller 부재를 막는 것과 같은 취지지만,
# llm-manager 는 mdb 접근 시 caller 를 항상 "llm-manager" 로 고정 주입(with_caller)하므로
# mdb-manager 의 가드는 호스트 SERVICE_NAME 부재를 잡지 못한다. 따라서 저장 메타에
# 실행 주체를 None 으로 남기지 않도록 여기서 직접 강제한다.
_SERVICE_NAME_REQUIRED = (
    "agent_llm 저장 메타에는 실행 주체(service_name) 식별이 필요합니다. "
    "SERVICE_NAME 환경변수를 설정하세요."
)


def build_meta(llm_input: dict) -> dict:
    """llm_input에서 메타정보 dict를 생성한다.

    저장되는 두 축:
        - service_name: env ``SERVICE_NAME`` (실제 실행 주체. mdb-manager의
          ``X-Caller-Service``와 동일 출처). 입력이 아니라 환경에서 읽는다.
          미설정 강제는 요청 앞단 ``require_service_name`` (runner.prepare)에서
          한다. build_meta 자체는 env 를 읽기만 한다.
        - task_name: llm_input의 호출(task) 단위 라벨. 한 서비스 안에서
          어떤 호출이었는지 구분용. (구 ``service_name`` 입력 필드) — optional.

    Args:
        llm_input: llm_handler.call()에 전달할 인자 dict.

    Returns:
        저장/반환에 포함될 메타 dict.
    """
    return {
        "service_name": os.environ.get("SERVICE_NAME"),
        "task_name": llm_input.get("task_name"),
        "model": llm_input.get("model"),
    }


def require_service_name() -> str:
    """env ``SERVICE_NAME`` 을 강제한다. 미설정/빈 값이면 ``ValidationError``.

    llm_request 진입 시점(runner.prepare)에서 호출한다. service_name 은 metadata
    저장뿐 아니라 **handler 전송**에도 쓰이므로, 실행 주체를 모르면 요청을 진행할 수
    없다 — 저장 여부(save_mode)와 무관하게 요청 앞단에서 fail-fast 한다.
    (mdb-manager 의 caller 가드는 llm-manager 가 caller 를 "llm-manager" 로 고정
    주입하므로 호스트 SERVICE_NAME 부재를 잡지 못한다. 그래서 여기서 직접 강제.)

    Returns:
        env ``SERVICE_NAME`` 값.
    """
    service_name = os.environ.get("SERVICE_NAME")
    if not service_name:
        raise ValidationError(_SERVICE_NAME_REQUIRED)
    return service_name
