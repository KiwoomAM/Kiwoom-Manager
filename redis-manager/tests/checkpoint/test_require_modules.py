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


@pytest.mark.asyncio
async def test_missing_token_without_label_still_raises_runtimeerror(monkeypatch):
    # _REQUIRED_MODULES에 label 매핑이 없는 토큰이 추가돼도 KeyError가 아니라
    # 의도한 RuntimeError로 안내해야 한다(SSOT 이중선언 드리프트 방어).
    import redis_manager.checkpoint as ck

    monkeypatch.setattr(ck, "_REQUIRED_MODULES", ("rejson", "search", "timeseries"))
    client = _FakeClient([{b"name": b"ReJSON"}, {b"name": b"search"}])  # timeseries 없음
    with pytest.raises(RuntimeError) as exc:
        await ck._require_modules(client)
    assert "timeseries" in str(exc.value)  # label 폴백으로 토큰명 그대로 노출
