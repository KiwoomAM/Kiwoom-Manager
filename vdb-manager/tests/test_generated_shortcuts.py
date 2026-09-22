# -*- coding: utf-8 -*-
"""바로가기를 사람이 두 벌 적지 않는다는 계약.

`VDBManager` 의 바로가기 본문은 `scripts/generate_shortcuts.py` 가 원본 시그니처에서
만들어 낸다. 사람이 관리하는 것은 `vdb_manager/_shortcuts.py` 의 표뿐이다.

여기서 보는 것은 둘이다 — 생성물이 표·원본과 어긋나지 않았는가, 그리고 미러링하지
않은 것이 **이유와 함께** 등록돼 있는가. 이유 없는 빈칸을 허용하면 다음 사람이 그것이
실수인지 결정인지 구별할 수 없고, "어떤 것은 handler 로 되고 어떤 것은 안 된다" 는
외워야 할 경계가 다시 생긴다.
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from typing import Any, Dict

import pytest

from vdb_manager._shortcuts import ALIASES, NOT_MIRRORED, SHORTCUTS
from vdb_manager.handler import VDBManager

_ROOT = Path(__file__).resolve().parent.parent


def _generator() -> Any:
    spec = importlib.util.spec_from_file_location(
        "generate_shortcuts", _ROOT / "scripts" / "generate_shortcuts.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


GENERATOR = _generator()


def _sub_clients() -> Dict[str, type]:
    from vdb_manager.collection import CollectionClient
    from vdb_manager.dense import DenseClient
    from vdb_manager.hybrid import HybridClient
    from vdb_manager.reranker import RerankerClient
    from vdb_manager.sparse import SparseClient

    return {
        "collection": CollectionClient,
        "dense": DenseClient,
        "hybrid": HybridClient,
        "reranker": RerankerClient,
        "sparse": SparseClient,
    }


def test_생성된_바로가기가_표와_원본에_맞춰져_있다():
    """표나 원본을 고치고 생성을 안 돌리면 여기서 드러난다.

    `ruff format --check` 와 같은 방식이다. 생성물을 손으로 고친 경우도 함께 잡힌다.
    """
    source = GENERATOR.HANDLER.read_text(encoding="utf-8")
    block = source.split(GENERATOR.BEGIN, 1)[1].rsplit(GENERATOR.END, 1)[0]

    expected = GENERATOR.build_block()
    expected_block = expected.split(GENERATOR.BEGIN, 1)[1].rsplit(GENERATOR.END, 1)[0]

    assert block == expected_block, (
        "생성된 바로가기가 낡았다 — `uv run python scripts/generate_shortcuts.py` 를 돌릴 것"
    )


def test_표에_적힌_바로가기가_실제로_붙어_있다():
    for name, _, _ in SHORTCUTS:
        assert callable(getattr(VDBManager, name, None)), f"{name} 이 생성되지 않았다"
    for alias, canonical in ALIASES:
        assert getattr(VDBManager, alias) is getattr(VDBManager, canonical)


def test_미러링하지_않은_것은_이유와_함께_등록돼_있다():
    mirrored = {f"{sub}.{target}" for _, sub, target in SHORTCUTS}
    unregistered = []
    for attr, cls in _sub_clients().items():
        members = dict(inspect.getmembers(cls, inspect.isfunction))
        members.update({n: m for n, m in inspect.getmembers(cls, lambda o: isinstance(o, property))})
        for name in members:
            if name.startswith("_"):
                continue
            key = f"{attr}.{name}"
            if key not in mirrored and key not in NOT_MIRRORED:
                unregistered.append(key)

    assert not unregistered, (
        "미러링도 안 하고 이유도 안 적은 메서드: "
        f"{sorted(unregistered)} — `_shortcuts.SHORTCUTS` 에 넣거나 `NOT_MIRRORED` 에 이유와 함께 등록할 것"
    )


def test_등록부에_사라진_이름이_남아_있지_않다():
    """원본에서 없어진 것을 등록부가 계속 가리키면 그 표가 사실과 멀어진다."""
    clients = _sub_clients()
    stale = [key for key in NOT_MIRRORED if not hasattr(clients[key.split(".")[0]], key.split(".")[1])]

    assert not stale, f"이제 존재하지 않는데 등록부에 남은 이름: {stale}"


@pytest.mark.parametrize("name,sub,target", SHORTCUTS, ids=[s[0] for s in SHORTCUTS])
def test_바로가기가_원본과_같은_인자를_받는다(name, sub, target):
    """생성기가 시그니처를 옮기다 흘린 것이 없는지 본다."""

    def parameters(func):
        return [
            (p.name, p.kind, p.default, p.annotation)
            for p in inspect.signature(func).parameters.values()
            if p.name != "self"
        ]

    assert parameters(getattr(VDBManager, name)) == parameters(getattr(_sub_clients()[sub], target))
