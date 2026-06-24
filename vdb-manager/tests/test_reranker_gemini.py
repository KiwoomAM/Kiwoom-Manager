# -*- coding: utf-8 -*-
"""RerankerClient.rerank(method="gemini") — llm_manager.call 기반 LLM 리랭킹.

llm_manager는 vdb-manager 환경에 설치돼 있지 않을 수 있으므로(선택적 의존성),
gemini 경로는 호출 시점에 lazy import한다. 테스트는 sys.modules에 가짜
llm_manager를 주입해 실제 설치 없이 lazy import가 가짜를 집도록 한다.
"""

import json
import sys
import time
from types import SimpleNamespace

import pytest

from vdb_manager import reranker as reranker_mod
from vdb_manager.reranker import RerankerClient


def _docs(n):
    return [
        {"id": f"d{i}", "text": f"문서{i}", "file_name": "f", "page": i, "score": 0.1 * i}
        for i in range(n)
    ]


def _reranker_with_fake_llm(monkeypatch, fake_call):
    """가짜 llm_manager 모듈을 주입한 RerankerClient를 만든다.

    gemini 경로는 SyncClient를 쓰지 않으므로 client 자리에는 더미를 넣는다.
    """
    fake_mod = SimpleNamespace(call=fake_call)
    monkeypatch.setitem(sys.modules, "llm_manager", fake_mod)
    return RerankerClient(client=SimpleNamespace())


def test_gemini_rerank_uses_llm_manager_call(monkeypatch):
    captured = {}

    def fake_call(*, contents, model):
        captured["model"] = model
        captured["contents"] = contents
        # 3번, 1번 문서를 상위로
        return SimpleNamespace(
            text=json.dumps(
                {"rankings": [{"doc_id": 3, "score": 9}, {"doc_id": 1, "score": 8}]}
            )
        )

    reranker = _reranker_with_fake_llm(monkeypatch, fake_call)
    out = reranker.rerank("질문", _docs(20), top_k=15, method="gemini")

    assert captured["model"] == reranker_mod._DEFAULT_GEMINI_MODEL
    assert len(out) == 15
    assert out[0]["id"] == "d2"  # doc_id 3 → index 2
    assert out[0]["rerank_score"] == 9


def test_gemini_skips_when_below_top_k(monkeypatch):
    # top_k 이하면 LLM 호출 없이 원본 그대로 — call이 불렸으면 실패
    def boom(**kwargs):
        raise AssertionError("llm_manager.call이 불려선 안 됨")

    reranker = _reranker_with_fake_llm(monkeypatch, boom)
    docs = _docs(5)
    assert reranker.rerank("q", docs, top_k=15, method="gemini") == docs


def test_gemini_timeout_returns_original_top_k(monkeypatch):
    # llm_manager.call이 timeout보다 오래 걸리면 원본 상위 top_k 반환 (강등)
    def slow_call(*, contents, model):
        time.sleep(0.2)
        return SimpleNamespace(text="{}")

    reranker = _reranker_with_fake_llm(monkeypatch, slow_call)
    docs = _docs(20)
    out = reranker.rerank("q", docs, top_k=15, method="gemini", timeout=0.01)

    assert out == docs[:15]


def test_gemini_tolerates_none_score(monkeypatch):
    # 실제 payload는 score가 None일 수 있다 — 포맷 크래시로 리랭킹이 무력화되면 안 됨
    def fake_call(*, contents, model):
        return SimpleNamespace(text=json.dumps({"rankings": [{"doc_id": 1, "score": 7}]}))

    reranker = _reranker_with_fake_llm(monkeypatch, fake_call)
    docs = [{"id": f"d{i}", "text": f"문서{i}", "score": None} for i in range(20)]
    out = reranker.rerank("q", docs, top_k=15, method="gemini")

    # 강등(원본 그대로)이 아니라 실제 리랭킹이 일어났는지 확인
    assert out[0]["id"] == "d0"  # doc_id 1 → index 0
    assert out[0]["rerank_score"] == 7


def test_gemini_missing_llm_manager_raises_import_error(monkeypatch):
    # 선택적 의존성 미설치 시 method="gemini"는 ImportError로 즉시 실패 (FAIL-LOUD)
    monkeypatch.setitem(sys.modules, "llm_manager", None)  # import 시 ImportError 유발
    reranker = RerankerClient(client=SimpleNamespace())
    with pytest.raises(ImportError):
        reranker.rerank("q", _docs(20), top_k=15, method="gemini")


def test_gemini_malformed_json_falls_back_to_regex(monkeypatch):
    # JSON 파싱 실패 시 정규식 폴백으로 doc_id를 뽑아 순서를 복원
    def junk_then_ids(*, contents, model):
        # 유효 JSON이 아니지만 "doc_id": N 패턴은 들어있음
        return SimpleNamespace(text='쓰레기... "doc_id": 4, "doc_id": 2 ...끝')

    reranker = _reranker_with_fake_llm(monkeypatch, junk_then_ids)
    out = reranker.rerank("q", _docs(20), top_k=15, method="gemini")

    assert len(out) == 15
    assert out[0]["id"] == "d3"  # doc_id 4 → index 3
    assert out[1]["id"] == "d1"  # doc_id 2 → index 1


def test_unknown_method_raises(monkeypatch):
    reranker = _reranker_with_fake_llm(monkeypatch, lambda **k: None)
    with pytest.raises(ValueError):
        reranker.rerank("q", _docs(20), top_k=15, method="bm25")


def test_colbert_method_delegates_to_http():
    # 기본 method="colbert"는 /v1/search/rerank로 위임 — 동작 무변화
    captured = {}

    class FakeClient:
        def post(self, path, json):
            captured["path"] = path
            captured["json"] = json
            return {"documents": [{"id": "x", "score": 1.0}]}

    reranker = RerankerClient(client=FakeClient())
    out = reranker.rerank("질문", _docs(3), top_k=2)

    assert captured["path"] == "/v1/search/rerank"
    assert captured["json"]["top_k"] == 2
    assert out == [{"id": "x", "score": 1.0}]


def test_handler_rerank_shortcut_forwards_method():
    # handler.rerank 바로가기가 method를 reranker로 전달해야 gemini도 단축 호출 가능
    from vdb_manager import VDBManager

    handler = VDBManager()
    captured = {}

    class FakeReranker:
        def rerank(self, **kwargs):
            captured.update(kwargs)
            return ["ok"]

    handler.reranker = FakeReranker()
    out = handler.rerank("q", [{"text": "a"}], top_k=5, method="gemini")

    assert captured["method"] == "gemini"
    assert out == ["ok"]
