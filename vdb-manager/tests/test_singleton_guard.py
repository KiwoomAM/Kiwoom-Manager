# -*- coding: utf-8 -*-
"""A failed VDBHandler construction must not poison the Singleton."""

import pytest

import vdb_manager.handler as handler_mod
from vdb_manager.handler import VDBHandler


def test_failed_construction_clears_singleton(monkeypatch):
    # Force SyncClient construction to blow up.
    def boom(*args, **kwargs):
        raise RuntimeError("client init failed")

    monkeypatch.setattr(handler_mod, "SyncClient", boom)
    with pytest.raises(RuntimeError):
        VDBHandler()

    # Singleton must be cleared so a later (healthy) construction works.
    assert VDBHandler._instance is None
