# -*- coding: utf-8 -*-
"""materialize_to_tempfile 테스트 (이슈 #81, B).

바이트를 임시파일로 떨궈 Path를 yield하고, 블록 종료 시 unlink한다.
정상 종료·예외 종료 양쪽에서 정리가 보장돼야 한다.
"""

from pathlib import Path

import pytest

from file_manager.handler import FileManagerHandler


@pytest.fixture(autouse=True)
def reset_singleton():
    FileManagerHandler.reset()
    yield
    FileManagerHandler.reset()


async def test_materialize_writes_bytes_and_yields_path():
    fm = FileManagerHandler()
    async with fm.materialize_to_tempfile(b"PDFDATA", suffix=".pdf") as path:
        assert isinstance(path, Path)
        assert path.exists()
        assert path.suffix == ".pdf"
        assert path.read_bytes() == b"PDFDATA"


async def test_materialize_cleans_up_after_normal_exit():
    fm = FileManagerHandler()
    async with fm.materialize_to_tempfile(b"X") as path:
        captured = path
    assert not captured.exists()   # 블록 벗어나면 삭제


async def test_materialize_cleans_up_if_write_fails(monkeypatch):
    """쓰기 자체가 실패해도 임시파일을 남기지 않는다 (orphan 방지)."""
    import file_manager.handler as handler_mod

    created = []
    real_ntf = handler_mod.tempfile.NamedTemporaryFile

    def recording_ntf(*args, **kwargs):
        tf = real_ntf(*args, **kwargs)
        created.append(tf.name)
        return tf

    monkeypatch.setattr(handler_mod.tempfile, "NamedTemporaryFile", recording_ntf)

    fm = FileManagerHandler()
    # str을 넘기면 바이너리 temp에 tf.write(str) → TypeError (쓰기 단계 실패 재현)
    with pytest.raises(TypeError):
        async with fm.materialize_to_tempfile("not-bytes", suffix=".bin"):
            pass

    assert created, "임시파일이 생성됐어야 한다"
    for name in created:
        assert not Path(name).exists()   # 쓰기 실패 경로에서도 정리 보장


async def test_materialize_cleans_up_on_exception():
    fm = FileManagerHandler()
    captured = {}

    with pytest.raises(RuntimeError):
        async with fm.materialize_to_tempfile(b"X", suffix=".bin") as path:
            captured["path"] = path
            assert path.exists()
            raise RuntimeError("boom")

    assert not captured["path"].exists()   # 예외 경로에서도 정리 보장
