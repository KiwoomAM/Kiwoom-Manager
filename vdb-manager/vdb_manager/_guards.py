# -*- coding: utf-8 -*-
"""왕복 전에 막는 것들.

`vdb-handler`도 같은 것을 422 로 거부한다. 층이 다른 두 방어다 — `vdb-handler`의 것은 모든
소비자(다른 언어·curl·다음에 생길 SDK)를 향하고, 이것은 이 SDK 사용자에게 호출
자리에서 알린다.

**빈 것만 보고 형식은 판정하지 않는다.** u64 범위나 UUID 모양, 항목명의 공백까지
흉내 내면 `vdb-handler`가 규칙을 넓힐 때 SDK 만 옛 규칙으로 정당한 요청을 막는다. `vdb-handler`가
판정하지 않는 사실(슬래시 든 필드명의 index 한계)만 경고로 전한다.

이 모듈에 모아 두는 이유는 `payload_filter` 를 받는 자리가 컬렉션과 검색 셋에
흩어져 있기 때문이다. 한 곳에 두지 않으면 한쪽만 고쳐지고 나머지가 남는다.
"""

from __future__ import annotations

from typing import Any


def reject_if_empty(name: str, value: Any, reason: str) -> None:
    """빈 인자를 `vdb-handler`에 보내기 전에 막는다.

    Args:
        name: 인자 이름. 메시지에 그대로 실린다.
        value: 검사할 값.
        reason: 왜 막는지. 호출자마다 다르다.

    Raises:
        ValueError: `value` 가 비었을 때.
    """
    if not value:
        raise ValueError(f"{name} 비어 있음 — {reason}")


def reject_empty_filter(payload_filter: Any) -> None:
    """빈 조건을 막는다.

    `{}` 는 "조건 없음" 이 아니라 "조건을 만들려다 비어서 나온" 상태다. 조건 없음은
    `None` 이고, 둘을 같게 보면 같은 실수가 정반대 결과를 낸다 — 존재 확인에서는 그
    결과가 "이미 적재됨" 으로 읽혀 적재가 누락되고, 검색에서는 걸려 있어야 할 조건이
    사라진 채 결과가 넓어진다.

    Args:
        payload_filter: 포인트를 고르는 조건. None 이면 조건 없음이므로 통과한다.

    Raises:
        ValueError: 빈 dict 일 때.
    """
    if payload_filter is None:
        return
    reject_if_empty(
        "payload_filter",
        payload_filter,
        "조건을 만들려다 비어서 나온 상태, 조건 없이 전체를 대상으로 하려면 인자를 생략",
    )


def reject_empty_selection(with_payload: Any) -> None:
    """고를 payload 항목 목록이 비었으면 막는다.

    빈 목록은 "고르지 않음" 이 아니라 "고르려다 비어서 나온" 상태다. payload 가
    정말 필요 없으면 False 를 준다.

    Args:
        with_payload: True/False 또는 받을 항목명 목록.

    Raises:
        ValueError: 항목명 목록이 비었을 때.
    """
    if isinstance(with_payload, bool):
        return
    reject_if_empty(
        "with_payload",
        with_payload,
        "고르려다 비어서 나온 상태, payload 가 필요 없으면 False 사용",
    )
