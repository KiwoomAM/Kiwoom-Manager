# -*- coding: utf-8 -*-
"""빈 조건(`payload_filter={}`)을 받는 모든 자리가 왕복 전에 막는지 본다.

`{}` 는 "조건 없음" 이 아니라 "조건을 만들려다 비어서 나온" 상태다. 게이트웨이도
422 로 거부하지만 층이 다른 두 방어다 — 서버의 것은 모든 소비자를 향하고, 이것은 이
SDK 사용자에게 호출 자리에서 알린다.

막는 자리를 손으로 적지 않는다. `payload_filter` 를 받는 메서드를 시그니처에서 세어
오므로, 그 인자를 받는 메서드가 새로 생기면 아무도 등록하지 않아도 검사 대상이 된다.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List, Tuple

import pytest

from vdb_manager.collection import CollectionClient
from vdb_manager.dense import DenseClient
from vdb_manager.hybrid import HybridClient
from vdb_manager.sparse import SparseClient

SUB_CLIENTS = (CollectionClient, DenseClient, HybridClient, SparseClient)


class RefusingClient:
    """요청이 나가면 그 자체로 실패다. 가드는 왕복 전에 막아야 한다."""

    def _refuse(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("가드가 막지 못해 요청이 나갔다")

    get = post = patch = put = delete = _refuse


def _dummy(parameter: inspect.Parameter) -> Any:
    """필수 인자를 채울 아무 값. 가드가 먼저 터지므로 값의 내용은 중요하지 않다."""
    annotation = str(parameter.annotation)
    if "List" in annotation or "list" in annotation:
        return ["x"]
    if "int" in annotation and "str" not in annotation:
        return 1
    return "x"


def _methods_taking_a_filter() -> List[Tuple[type, str]]:
    found = []
    for cls in SUB_CLIENTS:
        for name, member in inspect.getmembers(cls, inspect.isfunction):
            if name.startswith("_"):
                continue
            if "payload_filter" in inspect.signature(member).parameters:
                found.append((cls, name))
    return found


TAKES_A_FILTER = _methods_taking_a_filter()


def test_검사할_자리를_시그니처에서_찾아낸다():
    """못 찾으면 아래가 공허하게 통과한다."""
    assert TAKES_A_FILTER, "`payload_filter` 를 받는 메서드를 하나도 못 찾음"


def _instance(cls: type) -> Any:
    """하위 클라이언트를 만든다. `HybridClient` 처럼 다른 클라이언트를 받는 것도 있다."""
    http = RefusingClient()
    kwargs: Dict[str, Any] = {}
    for name, parameter in list(inspect.signature(cls.__init__).parameters.items())[1:]:
        if parameter.default is not inspect.Parameter.empty:
            continue
        kwargs[name] = {"dense": DenseClient(http), "sparse": SparseClient(http)}.get(name, http)
    return cls(**kwargs)


@pytest.mark.parametrize("cls,name", TAKES_A_FILTER, ids=lambda v: getattr(v, "__name__", v))
def test_빈_조건은_왕복_전에_막는다(cls, name):
    instance = _instance(cls)
    method = getattr(instance, name)

    kwargs: Dict[str, Any] = {"payload_filter": {}}
    for param_name, parameter in inspect.signature(method).parameters.items():
        if param_name == "payload_filter" or parameter.default is not inspect.Parameter.empty:
            continue
        kwargs[param_name] = _dummy(parameter)

    with pytest.raises(ValueError, match="payload_filter"):
        method(**kwargs)
