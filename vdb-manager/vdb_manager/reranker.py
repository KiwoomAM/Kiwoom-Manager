# -*- coding: utf-8 -*-
"""리랭킹 SDK 클라이언트

두 가지 리랭킹 방식을 단일 진입점 ``rerank(method=...)``으로 노출한다.

- ``method="colbert"`` (기본): VDB Handler Service의 ``/v1/search/rerank``
  (ColBERT v2)에 위임한다. 모델 추론은 서버측에서 일어난다.
- ``method="gemini"``: ``llm_manager``를 통한 Gemini LLM 기반 리랭킹.
  ``llm_manager``는 선택적 의존성이라 이 경로 호출 시점에 lazy import한다
  (미설치 시 ImportError로 즉시 실패). 타임아웃·실패 시에는 원본 상위
  ``top_k``개를 그대로 반환한다(리랭킹 강등).
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Dict, List

from vdb_manager._client import SyncClient

logger = logging.getLogger(__name__)

# Gemini 리랭킹 기본값 (호출 시 인자로 override 가능)
_DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"
_DEFAULT_MAX_DOCS = 50  # 최대 리랭킹 대상 문서 수 (N)
_DEFAULT_MAX_DOC_LENGTH = 500  # 각 문서당 최대 길이
_DEFAULT_TIMEOUT = 10.0  # 리랭킹 타임아웃 (초)


class RerankerClient:
    """Service의 /v1/search/rerank(ColBERT) 또는 Gemini LLM 리랭킹 클라이언트"""

    def __init__(self, client: SyncClient) -> None:
        self._client = client

    def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = 10,
        text_key: str = "text",
        method: str = "colbert",
        *,
        model: str = _DEFAULT_GEMINI_MODEL,
        max_docs: int = _DEFAULT_MAX_DOCS,
        timeout: float = _DEFAULT_TIMEOUT,
        max_doc_length: int = _DEFAULT_MAX_DOC_LENGTH,
    ) -> List[Dict[str, Any]]:
        """문서 리랭킹. ``method``로 ColBERT(서버) / Gemini(LLM) 택일.

        ``model``/``max_docs``/``timeout``/``max_doc_length``는 ``method="gemini"``
        에서만 의미가 있고, ``method="colbert"``에서는 무시된다.
        """
        if method == "colbert":
            resp = self._client.post(
                "/v1/search/rerank",
                json={
                    "query": query,
                    "documents": documents,
                    "top_k": top_k,
                    "text_key": text_key,
                },
            )
            return resp["documents"]

        if method == "gemini":
            return self._rerank_gemini(
                query,
                documents,
                top_k=top_k,
                text_key=text_key,
                model=model,
                max_docs=max_docs,
                timeout=timeout,
                max_doc_length=max_doc_length,
            )

        raise ValueError(
            f"unknown rerank method: {method!r} (expected 'colbert' or 'gemini')"
        )

    def _rerank_gemini(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        *,
        top_k: int,
        text_key: str,
        model: str,
        max_docs: int,
        timeout: float,
        max_doc_length: int,
    ) -> List[Dict[str, Any]]:
        """Gemini LLM 기반 리랭킹. 타임아웃·실패 시 원본 상위 top_k 반환."""
        if not documents:
            return documents

        # 결과가 top_k 이하면 리랭킹 불필요
        if len(documents) <= top_k:
            logger.debug("리랭킹 스킵: 결과 수(%d) <= top_k(%d)", len(documents), top_k)
            return documents

        import llm_manager  # 선택적 의존성 — gemini 경로 호출 시점에만 필요

        candidates = documents[:max_docs]
        start_time = time.time()

        try:
            docs_text = _build_rerank_docs_text(candidates, text_key, max_doc_length)
            prompt = _build_rerank_prompt(query, docs_text, top_k)

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    llm_manager.call,
                    contents=prompt,
                    model=model,
                )
                response = future.result(timeout=timeout)

            response_text = response.text.strip()
            reranked = _parse_rerank_response(response_text, candidates, top_k)

            elapsed = time.time() - start_time
            logger.info(
                "리랭킹 완료: %d개 → %d개, 소요시간: %.2fs",
                len(candidates),
                len(reranked),
                elapsed,
            )
            return reranked

        except FuturesTimeoutError:
            elapsed = time.time() - start_time
            logger.warning(
                "Gemini 리랭킹 타임아웃 (%.2fs, limit=%.1fs), 원본 결과 반환",
                elapsed,
                timeout,
            )
            return documents[:top_k]
        except Exception as e:
            elapsed = time.time() - start_time
            logger.warning("리랭킹 실패 (%.2fs): %s, 원본 결과 반환", elapsed, e)
            return documents[:top_k]

    @property
    def is_ready(self) -> bool:
        """ColBERT 모델 준비 상태 확인"""
        try:
            resp = self._client.get("/v1/admin/status")
            return resp["colbert"].get("status") == "ready"
        except Exception:
            return False


def _build_rerank_docs_text(
    candidates: List[Dict[str, Any]], text_key: str, max_doc_length: int
) -> str:
    """리랭킹용 문서 텍스트 생성. file_name/page는 있으면 표기, 없으면 생략."""
    docs_parts = []
    for i, doc in enumerate(candidates, start=1):
        full_text = doc.get(text_key, "")
        text = full_text[:max_doc_length]
        if len(full_text) > max_doc_length:
            text += "..."

        score = doc.get("score")
        if not isinstance(score, (int, float)):
            score = 0.0  # 실제 payload는 score가 None일 수 있다 — 포맷 크래시 방지
        header = f"[{i}] (벡터유사도: {score:.3f})"
        file_name = doc.get("file_name")
        if file_name is not None:
            header += f" {file_name} p.{doc.get('page', 0)}"

        docs_parts.append(f"{header}\n{text}")

    return "\n\n".join(docs_parts)


def _build_rerank_prompt(query: str, docs_text: str, top_k: int) -> str:
    """LLM 리랭킹 프롬프트 생성."""
    return f"""당신은 문서 관련성 평가 전문가입니다.

질문: {query}

아래 문서들 중 질문과 가장 관련 있는 문서 {top_k}개를 선택하세요.
각 문서의 관련성을 평가하여 가장 관련 있는 순서대로 문서 번호를 나열하세요.

문서들:
{docs_text}

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요:
{{"rankings": [{{"doc_id": 문서번호, "score": 관련성점수(0-10)}}]}}

예시:
{{"rankings": [{{"doc_id": 3, "score": 9}}, {{"doc_id": 1, "score": 7}}, {{"doc_id": 5, "score": 6}}]}}"""


def _parse_rerank_response(
    response_text: str, candidates: List[Dict[str, Any]], top_k: int
) -> List[Dict[str, Any]]:
    """LLM 리랭킹 응답 파싱 (JSON). 실패 시 정규식 폴백."""
    try:
        json_text = response_text
        if "```json" in json_text:
            json_text = json_text.split("```json")[1].split("```")[0]
        elif "```" in json_text:
            json_text = json_text.split("```")[1].split("```")[0]

        data = json.loads(json_text.strip())
        rankings = data.get("rankings", [])

        reranked = []
        seen_ids = set()

        for rank in rankings:
            doc_id = rank.get("doc_id")
            if doc_id is None:
                continue

            idx = doc_id - 1

            if 0 <= idx < len(candidates) and idx not in seen_ids:
                doc = candidates[idx].copy()
                doc["rerank_score"] = rank.get("score", 0)
                reranked.append(doc)
                seen_ids.add(idx)

            if len(reranked) >= top_k:
                break

        if len(reranked) < top_k:
            for i, doc in enumerate(candidates):
                if i not in seen_ids:
                    reranked.append(doc)
                    if len(reranked) >= top_k:
                        break

        return reranked

    except json.JSONDecodeError as e:
        logger.warning("리랭킹 JSON 파싱 실패: %s, 정규식 파싱 시도", e)
        return _parse_rerank_fallback(response_text, candidates, top_k)


def _parse_rerank_fallback(
    response_text: str, candidates: List[Dict[str, Any]], top_k: int
) -> List[Dict[str, Any]]:
    """리랭킹 응답 파싱 폴백 (정규식)."""
    numbers = re.findall(r'"doc_id"\s*:\s*(\d+)', response_text)

    if not numbers:
        numbers = re.findall(r"\b(\d+)\b", response_text)

    reranked = []
    seen_ids = set()

    for num_str in numbers:
        idx = int(num_str) - 1
        if 0 <= idx < len(candidates) and idx not in seen_ids:
            reranked.append(candidates[idx])
            seen_ids.add(idx)

        if len(reranked) >= top_k:
            break

    if len(reranked) < top_k:
        for i, doc in enumerate(candidates):
            if i not in seen_ids:
                reranked.append(doc)
                if len(reranked) >= top_k:
                    break

    return reranked
