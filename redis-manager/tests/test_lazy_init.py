# -*- coding: utf-8 -*-
"""Importing the package must NOT eagerly establish a Redis connection."""

import importlib


def test_package_import_does_not_call_init_redis(monkeypatch):
    import redis_manager
    import redis_manager.connection as conn

    calls = {"n": 0}

    def counting_init():
        calls["n"] += 1
        return False

    monkeypatch.setattr(conn, "init_redis", counting_init)
    importlib.reload(redis_manager)

    assert calls["n"] == 0, "import should be lazy; init_redis must not run at import time"
