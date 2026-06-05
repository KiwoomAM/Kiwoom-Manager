# -*- coding: utf-8 -*-
"""Transport-level httpx errors (incl. timeouts) must surface as VDBConnectionError."""

import httpx
import pytest

from vdb_manager.exceptions import VDBConnectionError


def test_sync_client_wraps_read_timeout(monkeypatch):
    from vdb_manager._client import SyncClient

    client = SyncClient()

    def boom(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(client._http, "request", boom)
    with pytest.raises(VDBConnectionError):
        client.get("/v1/admin/status")
