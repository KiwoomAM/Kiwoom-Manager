# Kiwoom VDB Handler SDK

VDB Handler Service와 통신하는 경량 HTTP 클라이언트입니다.

## 설치

```bash
pip install -e ./packages/vdb-handler/sdk
```

## 환경변수

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `VDB_HANDLER_URL` | VDB Handler Service URL | `http://192.7.9.45:8500` |

## 사용법

```python
from vdb_handler import VDBHandler

handler = VDBHandler()

# Dense 임베딩
vector = handler.embed_text("키움증권 리포트")
vectors = handler.embed_batch(["텍스트1", "텍스트2"])

# Sparse 임베딩 (SPLADE)
indices, values = handler.sparse_encode("텍스트")

# 하이브리드 검색 (Dense + Sparse + RRF + Rerank)
results = handler.hybrid_search("질문", collection_name="kw_gp")

# ColBERT 리랭킹
reranked = handler.rerank("질문", documents=[...])

# 서비스 상태 확인
status = handler.health_check()

# 종료
handler.close()
```

## 서브 클라이언트 직접 사용

```python
from vdb_handler import VDBHandler

handler = VDBHandler()

# Dense 전용
handler.dense.embed_text("텍스트")
handler.dense.embed_batch(["텍스트1", "텍스트2"])

# Sparse 전용
handler.sparse.encode("텍스트")
handler.sparse.encode_batch(["텍스트1", "텍스트2"])

# 리랭커 전용
handler.reranker.rerank("질문", documents=[...])

# 검색/컬렉션 관리
handler.search.hybrid_search("질문", collection_name="kw_gp")
handler.search.create_collection("my_collection")
handler.search.get_collection_info("my_collection")
handler.search.delete_collection("my_collection")
```
