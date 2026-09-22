# -*- coding: utf-8 -*-
"""바로가기가 원본과 같은 시그니처를 유지하는지 본다.

`VDBManager` 는 하위 클라이언트의 메서드를 같은 이름·같은 인자로 한 벌 더 갖는다.
편의는 실재하지만 그 대가로 같은 시그니처가 두 파일에 산다. 인자 하나가 어긋나면
**조용히 기본값으로 떨어진 채** `vdb-handler`로 간다 — 예외도 경고도 없다.

#114 가 그 변이를 기록해 두었다. `CollectionClient.set_payload` 의 첫 인자 이름만
바꾸면 기존 테스트가 전부 초록인 채 `handler.set_payload` 호출자가 런타임에 죽는다.

그래서 여기서는 **바로가기 목록을 손으로 적지 않는다.** 소스에서 위임 관계를 읽어
와 대조하므로, 새 바로가기가 생기면 아무도 등록하지 않아도 자동으로 검사 대상이
된다. 손으로 적은 목록은 빠뜨린 것을 영영 못 본다.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Dict, List, Tuple

import vdb_manager.handler as handler_module
from vdb_manager.handler import VDBManager


def _class_body(cls) -> ast.ClassDef:
    tree = ast.parse(textwrap.dedent(inspect.getsource(cls)))
    node = tree.body[0]
    assert isinstance(node, ast.ClassDef)
    return node


def _sub_client_types() -> Dict[str, type]:
    """`self.<이름> = <클래스>(...)` 를 `__init__` 에서 읽어 하위 클라이언트를 찾는다.

    `_` 로 시작하는 것은 뺀다. `self._client` 는 HTTP 래퍼이지 바로가기의 원본이
    아니며, 넣으면 `status()` 처럼 직접 HTTP 를 치는 메서드가 위임으로 잡힌다.
    """
    found: Dict[str, type] = {}
    for method in _class_body(VDBManager).body:
        if not isinstance(method, ast.FunctionDef) or method.name != "__init__":
            continue
        for node in ast.walk(method):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            target = node.targets[0]
            called = node.value.func
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
                and isinstance(called, ast.Name)
                and not target.attr.startswith("_")
            ):
                cls = getattr(handler_module, called.id, None)
                if inspect.isclass(cls):
                    found[target.attr] = cls
    return found


SUB_CLIENTS = _sub_client_types()


def _delegations() -> Dict[str, Tuple[str, str]]:
    """각 public 메서드가 `self.<하위클라이언트>.<메서드>` 로 위임하는지 소스에서 읽는다."""
    found: Dict[str, Tuple[str, str]] = {}
    for method in _class_body(VDBManager).body:
        if not isinstance(method, ast.FunctionDef) or method.name.startswith("_"):
            continue
        for node in ast.walk(method):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            owner = node.func.value
            if (
                isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "self"
                and owner.attr in SUB_CLIENTS
            ):
                found[method.name] = (owner.attr, node.func.attr)
                break
    return found


DELEGATIONS = _delegations()


def _parameters(func) -> List[Tuple[str, str, object, object]]:
    return [
        (p.name, str(p.kind), p.default, p.annotation)
        for p in inspect.signature(func).parameters.values()
        if p.name != "self"
    ]


def test_하위_클라이언트를_소스에서_찾아낸다():
    """이 검사 자체가 동작하는지 먼저 본다. 못 찾으면 아래가 전부 공허하게 통과한다."""
    assert SUB_CLIENTS, "`__init__` 에서 하위 클라이언트를 하나도 못 찾음 — 검사가 무력하다"
    assert DELEGATIONS, "위임하는 바로가기를 하나도 못 찾음 — 검사가 무력하다"


def test_위임하는_메서드를_하나도_놓치지_않았다():
    """소스를 읽는 방식이 깨지면 조용히 0건을 검사하게 된다.

    그래서 "위임하는 것처럼 보이는데 목록에 없는" 메서드를 따로 센다. 개수를 박지
    않고 불변식으로 본다 — 개수는 바로가기가 늘 때마다 사람이 맞춰야 한다.
    """
    missed = []
    for name, member in inspect.getmembers(VDBManager, inspect.isfunction):
        # `upsert_hybrid = upsert` 같은 하위호환 별칭은 같은 함수를 가리킨다.
        # 정의된 이름으로 이미 검사했으므로 두 번 세지 않는다.
        if name.startswith("_") or name in DELEGATIONS or member.__name__ in DELEGATIONS:
            continue
        source = inspect.getsource(member)
        if any(f"self.{sub}." in source for sub in SUB_CLIENTS):
            missed.append(name)

    assert not missed, f"위임하는데 검사 대상에서 빠진 바로가기: {missed}"


def test_바로가기가_원본과_같은_인자를_받는다():
    """인자 이름·순서·기본값·종류·타입 주석까지 같아야 한다.

    하나라도 어긋나면 호출자가 준 값이 조용히 사라지거나 기본값으로 떨어진다.
    """
    drifted = []
    for shortcut_name, (sub, target_name) in sorted(DELEGATIONS.items()):
        shortcut = getattr(VDBManager, shortcut_name)
        target = getattr(SUB_CLIENTS[sub], target_name)
        if _parameters(shortcut) != _parameters(target):
            drifted.append(
                f"VDBManager.{shortcut_name} != {SUB_CLIENTS[sub].__name__}.{target_name}\n"
                f"      바로가기: {_parameters(shortcut)}\n"
                f"      원본    : {_parameters(target)}"
            )

    assert not drifted, "바로가기와 원본의 시그니처가 어긋남\n" + "\n".join(drifted)


def test_바로가기가_인자를_kwargs_로_뭉개지_않는다():
    """`**kwargs` 는 두 벌 관리를 피하지만 그 대가가 이 층의 목적과 어긋난다.

    IDE 자동완성과 타입 검사가 사라지고, 오타는 호출부가 아니라 SDK 안쪽에서
    `TypeError` 로 터진다. #114 가 핵심 기준으로 적은 것이 정확히 이 둘이다.
    """
    tolerant = []
    for shortcut_name in sorted(DELEGATIONS):
        kinds = [kind for _, kind, _, _ in _parameters(getattr(VDBManager, shortcut_name))]
        if "VAR_KEYWORD" in kinds:
            tolerant.append(shortcut_name)

    assert not tolerant, f"원본의 인자를 `**kwargs` 로 뭉갠 바로가기: {tolerant}"
