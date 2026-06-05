# -*- coding: utf-8 -*-
"""A failed VDBManager construction must not poison the Singleton."""

import pytest

import vdb_manager.handler as handler_mod
from vdb_manager.handler import VDBManager


def test_failed_construction_clears_singleton(monkeypatch):
    # Force SyncClient construction to blow up.
    def boom(*args, **kwargs):
        raise RuntimeError("client init failed")

    monkeypatch.setattr(handler_mod, "SyncClient", boom)
    with pytest.raises(RuntimeError):
        VDBManager()

    # Singleton must be cleared so a later (healthy) construction works.
    assert VDBManager._instance is None
