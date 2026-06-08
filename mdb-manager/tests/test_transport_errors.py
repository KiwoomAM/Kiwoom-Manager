# -*- coding: utf-8 -*-
"""Transport-level httpx errors (incl. timeouts) must surface as MDBConnectionError."""

import httpx
import pytest

from mdb_manager.exceptions import MDBConnectionError


def test_sync_client_wraps_read_timeout(monkeypatch):
    monkeypatch.setenv("SERVICE_NAME", "test-agent")
    from mdb_manager._client import SyncClient

    client = SyncClient()

    def boom(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(client._http, "request", boom)
    with pytest.raises(MDBConnectionError):
        client.get("/v1/admin/health")


async def test_async_client_wraps_read_timeout(monkeypatch):
    monkeypatch.setenv("SERVICE_NAME", "test-agent")
    from mdb_manager._async_client import AsyncClient

    client = AsyncClient()

    async def boom(*args, **kwargs):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(client._http, "request", boom)
    with pytest.raises(MDBConnectionError):
        await client.get("/v1/admin/health")
