# -*- coding: utf-8 -*-
import pytest

from redis_manager.checkpoint import _require_modules


class _FakeClient:
    def __init__(self, modules):
        self._modules = modules

    async def module_list(self):
        return self._modules


@pytest.mark.asyncio
async def test_passes_when_both_modules_present():
    client = _FakeClient([{b"name": b"ReJSON"}, {b"name": b"search"}])
    await _require_modules(client)  # 예외 없이 반환


@pytest.mark.asyncio
async def test_raises_and_names_missing_module():
    client = _FakeClient([{b"name": b"search"}])  # ReJSON 없음
    with pytest.raises(RuntimeError) as exc:
        await _require_modules(client)
    assert "ReJSON" in str(exc.value)


@pytest.mark.asyncio
async def test_raises_when_no_modules():
    client = _FakeClient([])
    with pytest.raises(RuntimeError) as exc:
        await _require_modules(client)
    msg = str(exc.value)
    assert "ReJSON" in msg and "search" in msg


@pytest.mark.asyncio
async def test_accepts_variant_module_names():
    # 부분 매칭: 'searchlight'(Enterprise)도 'search' 토큰을 포함하므로 통과해야 한다
    client = _FakeClient([{b"name": b"ReJSON"}, {b"name": b"searchlight"}])
    await _require_modules(client)  # 예외 없이 반환
