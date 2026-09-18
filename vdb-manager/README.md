# VDB Manager

> Kiwoom VDB Handler SDK v3.0 — HTTP 클라이언트 기반 벡터 검색 라이브러리

VDB Handler Service(REST API)를 통해 벡터 임베딩, 하이브리드 검색, 리랭킹, 컬렉션 관리를 수행하는 Python SDK입니다.
내부적으로 httpx를 사용하여 서비스와 HTTP 통신합니다.

## 요구사항

- Python >= 3.10
- httpx >= 0.27.0
- VDB Handler Service 실행 중

## 설치

```bash
pip install -e packages/vdb-manager
```

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `VDB_HANDLER_URL` | `http://192.7.9.45:8500` | VDB Handler Service URL |

## 디렉터리 구조

```
vdb_manager/
├── __init__.py        # 공개 API
├── _client.py         # httpx 기반 SyncClient (내부용)
├── handler.py         # VDBManager (Singleton 진입점)
├── dense.py           # DenseClient (Dense 임베딩 + 검색)
├── sparse.py          # SparseClient (Sparse 임베딩 + 검색)
├── hybrid.py          # HybridClient (Hybrid 임베딩 + 검색)
├── collection.py      # CollectionClient (컬렉션 관리 + 포인트 CRUD)
├── reranker.py        # RerankerClient (ColBERT 리랭킹)
└── exceptions.py      # VDBError, VDBConnectionError, VDBHandlerError
```

## Quick Start

```python
from vdb_manager import VDBManager

handler = VDBManager()

# Dense 임베딩 + 검색
vector = handler.dense.embed("키움증권 리포트")
results = handler.dense.search("질문", collection_name="kw_gp")

# Sparse 임베딩 + 검색
indices, values = handler.sparse.embed("텍스트")
results = handler.sparse.search("질문", collection_name="kw_gp")

# Hybrid 임베딩 + 검색
embeddings = handler.hybrid.embed("텍스트")
results = handler.hybrid.search("질문", collection_name="kw_gp")

# 컬렉션 관리 + CRUD
handler.collection.create("my_col")                    # hybrid (기본)
handler.collection.create("my_col", mode="dense")      # dense-only
handler.collection.get_points("my_col", ids=[1, 2])

# ColBERT 리랭킹
reranked = handler.reranker.rerank("질문", documents=results, top_k=5)

# 서비스 상태 확인
status = handler.health_check()
handler.close()
```

## API Reference

### 서브 클라이언트

VDBManager는 5개의 서브 클라이언트를 통해 기능을 분리합니다:

| 클라이언트 | 접근 | 역할 |
|-----------|------|------|
| `DenseClient` | `handler.dense` | Dense 모델 (임베딩 + 검색) |
| `SparseClient` | `handler.sparse` | Sparse 모델 (임베딩 + 검색) |
| `HybridClient` | `handler.hybrid` | Hybrid (Dense + Sparse 통합 임베딩 + 검색) |
| `CollectionClient` | `handler.collection` | Qdrant 컬렉션 관리 + 포인트 CRUD |
| `RerankerClient` | `handler.reranker` | ColBERT v2 리랭킹 |

#### handler.dense — DenseClient

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `embed(text, task_type)` | `List[float]` | 단일 텍스트 Dense 임베딩 (1536차원) |
| `embed_batch(texts, batch_size, task_type)` | `List[List[float]]` | 배치 Dense 임베딩 |
| `search(query, collection_name, top_k, score_threshold, payload_filter)` | `List[Dict]` | Dense 벡터 단독 검색 |
| `get_dimension()` | `int` | 임베딩 차원 조회 |
| `is_ready` (property) | `bool` | 모델 준비 상태 |

#### handler.sparse — SparseClient

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `embed(text)` | `Tuple[List[int], List[float]]` | 단일 SPLADE 임베딩 → (indices, values) |
| `embed_batch(texts, batch_size)` | `List[Tuple]` | 배치 SPLADE 임베딩 |
| `search(query, collection_name, top_k, score_threshold, payload_filter)` | `List[Dict]` | Sparse 벡터 단독 검색 |
| `is_ready` (property) | `bool` | 모델 준비 상태 |

#### handler.hybrid — HybridClient

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `embed(text, task_type)` | `Dict` | 단일 하이브리드 임베딩 `{"dense": [...], "sparse": (indices, values)}` |
| `embed_batch(texts, dense_batch_size, sparse_batch_size, task_type)` | `Dict` | 배치 하이브리드 임베딩 `{"dense": [[...]], "sparse": [...]}` |
| `search(query, collection_name, top_k, use_rerank, rerank_top_k, payload_filter)` | `List[Dict]` | Dense + Sparse + RRF 하이브리드 검색 |
| `is_ready` (property) | `bool` | Dense + Sparse 모두 준비 상태 |

#### handler.collection — CollectionClient

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `create(name, dense_dim, distance, recreate, mode)` | `Dict` | 컬렉션 생성 (mode: `"dense"` \| `"hybrid"`, 기본 `"hybrid"`) |
| `info(name)` | `Dict` | 컬렉션 정보 조회 |
| `delete(name)` | `Dict` | 컬렉션 삭제 |
| `upsert_points(collection_name, texts, dense_vectors, metadatas, ids, sparse_vectors, batch_size)` | `Dict` | 데이터 업로드. **포인트 id 를 직접 지정** |
| `upsert(collection_name, texts, dense_vectors, metadatas, sparse_vectors, start_id, batch_size)` | `Dict` | Deprecated — `upsert_points` 사용 |
| `count_points(collection_name, payload_filter)` | `int` | 조건에 맞는 포인트 수 (벡터·query 불필요) |
| `has_points(collection_name, payload_filter)` | `bool` | 조건에 맞는 포인트가 있는지 |
| `get_points(collection_name, ids, with_vectors)` | `List[Dict]` | ID로 포인트 조회 |
| `delete_points(collection_name, ids)` | `Dict` | ID로 포인트 삭제 |
| `scroll_points(collection_name, limit, offset, with_vectors)` | `Dict` | 포인트 페이지네이션 조회 |
| `set_payload(collection_name, ids, payload)` | `Dict` | payload에 주는 키만 더하거나 덮기 (나머지 보존) |
| `overwrite_payload(collection_name, ids, payload)` | `Dict` | payload를 통째로 교체 (기존 키 사라짐) |
| `delete_payload_keys(collection_name, ids, keys)` | `Dict` | payload에서 주는 키만 삭제 |
| `clear_payload(collection_name, ids)` | `Dict` | payload를 통째로 비우기 |
| `create_payload_index(collection_name, field, schema)` | `Dict` | payload 필드에 색인 생성 |
| `list_payload_indexes(collection_name)` | `Dict[str, str]` | 필드별 색인 현황 (`{필드명: 종류}`) |
| `delete_payload_index(collection_name, field)` | `Dict` | payload 색인 삭제 |
| `is_ready` (property) | `bool` | Qdrant 연결 상태 |

#### handler.reranker — RerankerClient

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `rerank(query, documents, top_k, text_key, method, model, max_docs, timeout, max_doc_length)` | `List[Dict]` | 문서 리랭킹 (`method`: `"colbert"` 서버 위임 \| `"gemini"` LLM) |
| `is_ready` (property) | `bool` | ColBERT 모델 준비 상태 (gemini는 미반영) |

- **`method="colbert"`** (기본): VDB Handler의 `/v1/search/rerank`에 위임. ColBERT v2 추론은 서버측.
- **`method="gemini"`**: `llm_manager`를 통한 Gemini LLM 리랭킹. `llm_manager`는 **선택적 의존성**이라 이 경로 호출 시점에 lazy import한다(미설치 시 `ImportError`). 타임아웃·실패 시 원본 상위 `top_k` 반환. `model`/`max_docs`/`timeout`/`max_doc_length`는 gemini 전용 (colbert에서는 무시).

#### 바로가기 메서드

VDBManager에서 직접 호출할 수 있는 단축 메서드:

```python
# 이 두 호출은 동일합니다
handler.hybrid_search("질문", collection_name="kw_gp")
handler.hybrid.search("질문", collection_name="kw_gp")
```

| 바로가기 | 위임 대상 |
|---------|----------|
| `embed()`, `embed_batch()` | `handler.dense` |
| `sparse_embed()`, `sparse_embed_batch()` | `handler.sparse` |
| `hybrid_search()`, `dense_search()`, `sparse_search()` | `handler.hybrid` / `handler.dense` / `handler.sparse` |
| `create_collection()`, `get_collection_info()`, `delete_collection()`, `upsert()` | `handler.collection` |
| `get_points()`, `delete_points()`, `scroll_points()` | `handler.collection` |
| `count_points()`, `has_points()`, `upsert_points()` | `handler.collection` |
| `set_payload()`, `overwrite_payload()`, `delete_payload_keys()`, `clear_payload()` | `handler.collection` |
| `create_payload_index()`, `list_payload_indexes()`, `delete_payload_index()` | `handler.collection` |
| `rerank()` | `handler.reranker` |

### 예외

```
VDBError                    # 기본 에러
├── VDBConnectionError      # 서비스 연결 실패
└── VDBHandlerError         # 서비스 에러 응답 (HTTP 4xx/5xx, status_code 포함)
```

```python
from vdb_manager import VDBConnectionError, VDBHandlerError

try:
    results = handler.hybrid.search("쿼리", collection_name="kw_gp")
except VDBConnectionError:
    print("서비스 연결 실패")
except VDBHandlerError as e:
    print(f"HTTP {e.status_code}: {e}")
```

## 사용 패턴

### 문서 임베딩 → 컬렉션 저장 (Hybrid)

```python
import uuid

handler = VDBManager()

texts = ["텍스트1", "텍스트2"]
metadatas = [
    {"file_name": "report.pdf", "category": "earnings", "page": 1},
    {"file_name": "report.pdf", "category": "earnings", "page": 2},
]

# 하이브리드 컬렉션 생성
handler.collection.create("my_reports", mode="hybrid")

# 하이브리드 임베딩 (dense + sparse 한번에)
embeddings = handler.hybrid.embed_batch(texts)

# 문서 정체성에서 유도한 결정론적 id — 같은 문서를 다시 넣으면 중복이 아니라
# 덮어쓰기가 된다. 폭을 줄이지 말 것(작은 범위로 접으면 남의 포인트를 덮어쓴다).
ids = [str(uuid.uuid5(uuid.NAMESPACE_URL, f"report.pdf:{i}")) for i in range(len(texts))]

# 이미 들어가 있으면 건너뛴다 (벡터도 query 텍스트도 필요 없다)
if not handler.collection.has_points(
    "my_reports", {"must": [{"key": "file_name", "match": {"value": "report.pdf"}}]}
):
    handler.collection.upsert_points(
        collection_name="my_reports",
        texts=texts,
        dense_vectors=embeddings["dense"],
        metadatas=metadatas,
        sparse_vectors=embeddings["sparse"],
        ids=ids,
    )
```

### 문서 임베딩 → 컬렉션 저장 (Dense-only)

```python
handler = VDBManager()

texts = ["텍스트1", "텍스트2"]
metadatas = [
    {"file_name": "report.pdf", "category": "earnings", "page": 1},
    {"file_name": "report.pdf", "category": "earnings", "page": 2},
]

# dense-only 컬렉션 생성
handler.collection.create("my_reports", mode="dense")

# Dense 임베딩만 생성
dense_vectors = handler.dense.embed_batch(texts)

# 저장 (sparse_vectors 생략)
handler.collection.upsert_points(
    collection_name="my_reports",
    texts=texts,
    dense_vectors=dense_vectors,
    metadatas=metadatas,
    ids=[str(uuid.uuid5(uuid.NAMESPACE_URL, f"report.pdf:{i}")) for i in range(len(texts))],
)
```

### 저장된 payload만 고치기 (백필·정리)

벡터는 그대로 두고 payload만 바꾸므로 임베딩을 다시 돌리지 않는다.

```python
# 스키마가 바뀌어 기존 포인트에 필드를 채워 넣는다 (주지 않은 키는 보존됨)
handler.collection.set_payload(
    "my_reports",
    ids=[1001, 1002],
    payload={"file_path": "5f3a...-uuid"},
)

# 폐기된 필드를 걷어낸다
handler.collection.delete_payload_keys("my_reports", ids=[1001], keys=["위치"])
```

대상이 수천 개면 **호출자가 끊어 보낸다.** 한 요청에 다 실으면 Qdrant가 처리하는 동안
HTTP 요청 하나가 열려 있고, 타임아웃이 나도 서버 작업은 되돌려지지 않아 일부만 적용된
상태가 남는다. 어디까지 갔는지는 호출자의 루프만 알 수 있다.

```python
CHUNK = 500
for i in range(0, len(ids), CHUNK):
    handler.collection.delete_payload_keys(
        "my_reports", ids=ids[i : i + CHUNK], keys=["위치"]
    )
    logger.info("%d/%d 완료", min(i + CHUNK, len(ids)), len(ids))
```

### 자주 쓰는 조건에 색인 걸기

존재 확인(`has_points`)이나 검색의 `payload_filter`에 쓰는 필드에 색인이 없으면 Qdrant가
전수 주사한다. 컬렉션이 커질수록 같은 질의가 느려진다.

```python
# 배포 전에 지금 무엇이 걸려 있는지 본다 (없으면 빈 dict)
handler.collection.list_payload_indexes("my_reports")
# {"source_pdf": "keyword"}

handler.collection.create_payload_index("my_reports", field="source_pdf", schema="keyword")
```

### 검색 파이프라인

```python
# 기본 검색
results = handler.hybrid.search(
    "2024년 실적 전망",
    collection_name="kw_gp",
    top_k=10,
    use_rerank=True,
)

# payload_filter를 사용한 검색
results = handler.dense.search(
    "삼성전자 ETF",
    collection_name="etf_reports",
    payload_filter={
        "must": [
            {"key": "일자", "range": {"gte": 20250101, "lte": 20250401}},
            {"key": "category", "match": {"value": "ETF"}},
        ]
    },
)

for r in results:
    print(f"[{r['score']:.4f}] {r['file_name']} p.{r['page']}: {r['text'][:80]}")
    print(f"  payload: {r['payload']}")
```

## 주의사항

- **Singleton**: 첫 생성 시 인자만 반영됨. 변경 필요 시 `VDBManager.reset()` 후 재생성
- **task_type**: 문서 저장 시 `"RETRIEVAL_DOCUMENT"`, 검색 쿼리 시 `"RETRIEVAL_QUERY"` 사용 권장
- **타임아웃**: 기본 120초 (SyncClient 기준)
- **upsert 정합성**: `texts`, `dense_vectors`, `metadatas`는 동일 길이 필수. `sparse_vectors`·`ids` 전달 시 동일 길이 필수
- **`upsert()`는 Deprecated**: 호출 시 `DeprecationWarning` 발생. 서버가 `start_id + j`로 채번하므로 호출자가 커서를 정확히 기억할 때만 안전하고, 기본값 `0`은 0번부터 기존 포인트를 덮어쓴다. 신규 적재는 `upsert_points(..., ids=[...])`
- **포인트 id**: 부호 없는 정수 또는 UUID만 허용(형식 검증은 Service). 문서에서 유도한 id를 쓸 때 **폭을 줄이지 말 것** — 작은 범위로 접으면 기존 순번 id 구간을 덮어쓴다
- **빈 조건은 거부됨**: `count_points()`·`has_points()`에 빈 dict(`{}`)를 넘기면 `ValueError`. Service는 빈 조건을 "필터 없음"으로 보아 컬렉션 전체를 세므로, 조건을 코드로 만들다 비어서 나오면 그 결과가 "이미 적재됨"으로 읽혀 적재가 누락된다. 전체 개수를 원하면 인자를 생략
- **Deprecation 경고가 안 보일 수 있음**: 파이썬 기본 설정은 `__main__`에서 난 `DeprecationWarning`만 표시한다. 서비스 모듈 안에서 `upsert()`를 부르면 화면에 안 뜨므로, 남은 호출부를 찾으려면 `PYTHONWARNINGS=default::DeprecationWarning` 또는 `python -W default::DeprecationWarning`으로 켠다
- **병합과 교체를 헷갈리지 말 것**: `set_payload()`는 주는 키만 더하거나 덮고 나머지를 남기지만, `overwrite_payload()`는 payload를 통째로 바꿔 **주지 않은 키가 전부 사라진다.** 둘은 같은 경로로 가고 HTTP 메서드만 다르다
- **어떤 payload 키도 보호되지 않음**: 존재 확인에 쓰는 정체성 키(`source_pdf` 등)도 `overwrite_payload()`·`clear_payload()`로 사라진다. 재적재 여부는 도메인 소비자가 판단할 일이라 Service가 막지 않는다 (근거는 `KiwoomAM/Kiwoom-Handler`의 ADR 0002)
- **payload 쓰기의 빈 인자는 거부됨**: `ids`·`payload`·`keys`·`field`가 비면 왕복 전에 `ValueError`. 대상 0개로 통과하면 Service까지 갔다가 아무것도 바꾸지 않고 성공처럼 보인다. 형식(정수 폭·UUID 모양)은 SDK가 판정하지 않고 Service에 맡긴다
- **큰 id 목록은 호출자가 끊어 보낼 것**: SDK가 쪼개지 않는다. 중간에 실패하면 "몇 개까지 갔는지"를 예외가 실어 나를 수 없어, 그 정보를 아는 호출자의 루프가 필요하다. `upsert_points()`의 `batch_size`와 혼동하지 말 것 — 그것은 HTTP가 아니라 Service→Qdrant 호출을 나누는 인자다
- **색인 `schema`에 기본값이 없음**: 오타는 Service가 422로 잡지만, 날짜 필드에 `keyword`를 거는 것은 유효한 요청이라 성공하고 range 질의만 색인을 타지 못한 채 느려진다. 색인은 오래 사는 물건이라 그 침묵이 길다
- **이름에 슬래시가 든 필드의 색인은 지울 수 없음**: 만들 수는 있고 조회에도 보이지만 삭제가 404로 돌아온다. 필드명을 잘못 적어서가 아니라 벤더 클라이언트 한계이며, `create_payload_index()`가 만드는 시점에 경고한다 (`KiwoomAM/Kiwoom-Handler#74`)
- **`has_points()`는 "적재 완료"가 아님**: 조건에 맞는 포인트가 하나라도 있으면 True다. 문서 하나가 포인트 여럿이 되는(청킹) 소비자가 이것을 완료 판정으로 쓰면 부분 적재가 영구히 굳는다
- **delete()**: 비가역적. 모든 데이터가 영구 삭제됨
- **distance 옵션**: `"Cosine"`, `"Euclid"`, `"Dot"` 지원 (기본: `"Cosine"`)
- **에러 처리**: HTTP 통신 기반이므로 `VDBConnectionError`/`VDBHandlerError` 처리 권장
- **하위호환**: v2 메서드명(`embed_text`, `encode`, `create_collection`, `create_hybrid_collection`, `upsert_hybrid` 등)은 alias로 유지됨
- **mode**: 컬렉션 생성 시 `"dense"` 또는 `"hybrid"` 선택 가능 (기본: `"hybrid"`)
