# -*- coding: utf-8 -*-
import redis_manager.checkpoint as ck

# _url()은 호출 시점에 os.getenv를 읽으므로(모듈 로드 시 상수화하지 않음) reload가 필요 없다.


def test_url_defaults_to_hardcoded(monkeypatch):
    monkeypatch.delenv("LANGGRAPH_REDIS_URL", raising=False)
    assert ck._url() == "redis://192.7.9.45:6379/0"


def test_url_reads_env_override(monkeypatch):
    monkeypatch.setenv("LANGGRAPH_REDIS_URL", "redis://example:6380/2")
    assert ck._url() == "redis://example:6380/2"
