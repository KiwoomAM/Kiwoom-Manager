# VDB Handler SDK

> Kiwoom VDB Handler SDK v2.0 — HTTP 클라이언트 기반 벡터 검색 라이브러리

VDB Handler Service(REST API)를 통해 벡터 임베딩, 하이브리드 검색, 리랭킹, 컬렉션 관리를 수행하는 Python SDK입니다.
내부적으로 httpx를 사용하여 서비스와 HTTP 통신합니다.

## 요구사항

- Python >= 3.10
- httpx >= 0.27.0
- VDB Handler Service 실행 중

## 설치

```bash
pip install -e packages/vdb-handler/sdk
```

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `VDB_HANDLER_URL` | `http://192.7.9.45:8500` | VDB Handler Service URL |

## 디렉터리 구조

```
vdb_handler/
├── __init__.py        # 공개 API
├── _client.py         # httpx 기반 SyncClient (내부용)
├── handler.py         # VDBHandler (Singleton 진입점)
├── dense.py           # DenseEmbedClient (Dense 임베딩)
├── sparse.py          # SparseEmbedClient (Sparse 인코딩)
├── search.py          # SearchClient (하이브리드 검색 + 컬렉션 관리)
├── reranker.py        # RerankerClient (ColBERT 리랭킹)
└── exceptions.py      # VDBError, VDBConnectionError, VDBHandlerError
```

## Quick Start

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
reranked = handler.rerank("질문", documents=results, top_k=5)

# 서비스 상태 확인
status = handler.health_check()

# 종료
handler.close()
```

## API Reference

### VDBHandler

Singleton 패턴의 메인 진입점입니다.

```python
VDBHandler(timeout=120.0)
```

#### Dense Embedding

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `embed_text(text, task_type="RETRIEVAL_DOCUMENT")` | `List[float]` | 단일 텍스트 Dense 임베딩 (1536차원) |
| `embed_batch(texts, batch_size=100, task_type="RETRIEVAL_DOCUMENT")` | `List[List[float]]` | 배치 Dense 임베딩 |

`task_type`: 문서 저장 시 `"RETRIEVAL_DOCUMENT"`, 검색 쿼리 시 `"RETRIEVAL_QUERY"`

#### Sparse Embedding

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `sparse_encode(text)` | `Tuple[List[int], List[float]]` | 단일 SPLADE 인코딩 → (indices, values) |
| `sparse_encode_batch(texts, batch_size=8)` | `List[Tuple[List[int], List[float]]]` | 배치 SPLADE 인코딩 |

#### Hybrid Search

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `hybrid_search(query, collection_name, top_k=20, use_rerank=True, rerank_top_k=50, block_type_filter=None)` | `List[Dict]` | Dense + Sparse + RRF 하이브리드 검색 |

반환 문서 필드: `id`, `score`, `text`, `file_name`, `category`, `page`, `file_path`, `block_type`

#### Reranking

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `rerank(query, documents, top_k=10, text_key="text")` | `List[Dict]` | ColBERT v2 리랭킹 (`rerank_score` 필드 추가) |

#### 생명주기

| 메서드 | 설명 |
|--------|------|
| `health_check()` | 서비스 상태 확인 (Dense/Sparse/ColBERT/Qdrant) |
| `close()` | HTTP 클라이언트 종료 |
| `reset()` (classmethod) | Singleton 인스턴스 초기화 |

### 서브 클라이언트 직접 사용

```python
handler = VDBHandler()

# Dense 전용
handler.dense.embed_text("텍스트")
handler.dense.embed_batch(["텍스트1", "텍스트2"])
handler.dense.get_dimension()        # 임베딩 차원 조회

# Sparse 전용
handler.sparse.encode("텍스트")
handler.sparse.encode_batch(["텍스트1", "텍스트2"])
handler.sparse.is_ready              # 모델 준비 상태 (bool)

# 리랭커 전용
handler.reranker.rerank("질문", documents=[...])
handler.reranker.is_ready             # 모델 준비 상태 (bool)

# 검색 + 컬렉션 관리
handler.search.hybrid_search("질문", collection_name="kw_gp")
handler.search.create_collection("my_col", dense_dim=1536, distance="Cosine", recreate=False)
handler.search.get_collection_info("my_col")
handler.search.delete_collection("my_col")
handler.search.upsert_hybrid(
    collection_name="my_col",
    texts=["텍스트1"],
    dense_vectors=[[0.1, 0.2, ...]],
    sparse_vectors=[([1, 2], [0.5, 0.3])],
    metadatas=[{"file_name": "a.pdf", "page": 1}],
)
```

### 예외

```
VDBError                    # 기본 에러
├── VDBConnectionError      # 서비스 연결 실패
└── VDBHandlerError         # 서비스 에러 응답 (HTTP 4xx/5xx, status_code 포함)
```

```python
from vdb_handler import VDBConnectionError, VDBHandlerError

try:
    results = handler.hybrid_search("쿼리", collection_name="kw_gp")
except VDBConnectionError:
    print("서비스 연결 실패")
except VDBHandlerError as e:
    print(f"HTTP {e.status_code}: {e}")
```

## 사용 패턴

### 문서 임베딩 → 컬렉션 저장

```python
handler = VDBHandler()

texts = ["텍스트1", "텍스트2"]
metadatas = [
    {"file_name": "report.pdf", "category": "earnings", "page": 1},
    {"file_name": "report.pdf", "category": "earnings", "page": 2},
]

# 컬렉션 생성 → 임베딩 → 저장
handler.search.create_collection("my_reports")
dense_vectors = handler.embed_batch(texts)
sparse_vectors = handler.sparse_encode_batch(texts)
handler.search.upsert_hybrid(
    collection_name="my_reports",
    texts=texts,
    dense_vectors=dense_vectors,
    sparse_vectors=sparse_vectors,
    metadatas=metadatas,
)
```

### 검색 파이프라인

```python
results = handler.hybrid_search(
    "2024년 실적 전망",
    collection_name="kw_gp",
    top_k=10,
    use_rerank=True,
)
for r in results:
    print(f"[{r['score']:.4f}] {r['file_name']} p.{r['page']}: {r['text'][:80]}")
```

## 주의사항

- **Singleton**: 첫 생성 시 인자만 반영됨. 변경 필요 시 `VDBHandler.reset()` 후 재생성
- **task_type**: 문서 저장 시 `"RETRIEVAL_DOCUMENT"`, 검색 쿼리 시 `"RETRIEVAL_QUERY"` 사용 권장
- **타임아웃**: 기본 120초 (SyncClient 기준)
- **upsert 정합성**: `texts`, `dense_vectors`, `sparse_vectors`, `metadatas`는 동일 길이 필수
- **delete_collection()**: 비가역적. 모든 데이터가 영구 삭제됨
- **distance 옵션**: `"Cosine"`, `"Euclid"`, `"Dot"` 지원 (기본: `"Cosine"`)
- **에러 처리**: HTTP 통신 기반이므로 `VDBConnectionError`/`VDBHandlerError` 처리 권장
