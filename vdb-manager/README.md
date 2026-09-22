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

# 전수 순회 — 필요한 항목만 받는다
for point in handler.collection.iter_points("my_col", with_payload=["source_pdf"]):
    ...

# ColBERT 리랭킹
reranked = handler.reranker.rerank("질문", documents=results, top_k=5)

# 서비스 상태 확인
status = handler.status()   # 옛 이름 health_check() 도 같은 것
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
| `count_points(collection_name, payload_filter)` | `int` | 조건에 맞는 포인트 수 (벡터·query 불필요) |
| `has_points(collection_name, payload_filter)` | `bool` | 조건에 맞는 포인트가 있는지 |
| `get_points(collection_name, ids, with_vectors, with_payload)` | `Dict` | ID로 포인트 조회 (`{"points": [...], "count": N}`) |
| `delete_points(collection_name, ids)` | `Dict` | ID로 포인트 삭제 |
| `scroll_points(collection_name, limit, offset, with_vectors, with_payload)` | `Dict` | 포인트 페이지네이션 조회 (`{"points": [...], "next_offset": ...}`) |
| `iter_points(collection_name, limit, with_vectors, with_payload)` | `Iterator[Dict]` | 전수 순회. 페이지 이어받기와 종료 판정을 대신한다 |
| `set_payload(collection_name, ids, payload)` | `Dict` | payload에 주는 키만 더하거나 덮기 (나머지 보존) |
| `overwrite_payload(collection_name, ids, payload)` | `Dict` | payload를 통째로 교체 (기존 키 사라짐) |
| `delete_payload_keys(collection_name, ids, keys)` | `Dict` | payload에서 주는 키만 삭제 |
| `clear_payload(collection_name, ids)` | `Dict` | payload를 통째로 비우기 |
| `create_payload_index(collection_name, field, schema)` | `Dict` | payload 필드에 index 생성 |
| `list_payload_indexes(collection_name)` | `Dict[str, str]` | 필드별 index 현황 (`{필드명: 종류}`) |
| `delete_payload_index(collection_name, field)` | `Dict` | payload index 삭제 |
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
| `embed()`, `embed_batch()`, `get_embedding_dimension()` | `handler.dense` |
| `sparse_embed()`, `sparse_embed_batch()` | `handler.sparse` |
| `hybrid_search()`, `dense_search()`, `sparse_search()` | `handler.hybrid` / `handler.dense` / `handler.sparse` |
| `create_collection()`, `get_collection_info()`, `delete_collection()` | `handler.collection` |
| `get_points()`, `delete_points()`, `scroll_points()`, `iter_points()` | `handler.collection` |
| `count_points()`, `has_points()`, `upsert_points()` | `handler.collection` |
| `set_payload()`, `overwrite_payload()`, `delete_payload_keys()`, `clear_payload()` | `handler.collection` |
| `create_payload_index()`, `list_payload_indexes()`, `delete_payload_index()` | `handler.collection` |
| `hybrid_embed()`, `hybrid_embed_batch()` | `handler.hybrid` |
| `rerank()` | `handler.reranker` |

**바로가기 본문은 사람이 적지 않는다.** `vdb_manager/_shortcuts.py` 의 표에 한 줄을 더하고 `uv run python scripts/generate_shortcuts.py` 를 돌리면 원본 시그니처 그대로 만들어진다. 생성물이 낡으면 테스트가 잡는다. 미러링하지 않는 것은 같은 파일의 `NOT_MIRRORED` 에 **이유와 함께** 등록해야 하며, 등록도 미러링도 없는 메서드가 있으면 테스트가 실패한다.

`is_ready` 는 다섯 하위 클라이언트가 각각 갖지만 바로가기를 열지 않았다. 다섯을 각각 부르면 왕복이 다섯 번인데 `status()` 가 한 번에 넷을 다 준다.

### 예외

```
VDBError                        # 기본 에러
├── VDBConnectionError          # 서비스에 닿지 못함 (status_code=0)
└── VDBHandlerError             # 서비스가 에러 응답을 반환 (status_code 포함)
    └── VDBUnavailableError     # 서비스가 저장소에 닿지 못함 (503)
```

`VDBUnavailableError`는 `VDBHandlerError`를 상속하므로 기존 `except VDBHandlerError`가 그대로 잡는다. 재시도 여부를 타입으로 갈라 쓰려는 호출자만 따로 잡으면 된다.

| 상태 코드 | 뜻 | 무엇이 여기 들어오나 |
|---|---|---|
| (연결 실패, `status_code=0`) | 서비스에 닿지도 못함 | 서비스가 안 떠 있거나 네트워크가 끊김 |
| **503** | 서비스가 **지금 이 요청을 받지 못함** | 저장소 미도달 · SPLADE/ColBERT 로딩 중 · **그 모델이 꺼져 있음** · 동시 처리 한계 초과 |
| 500 | 서비스가 **받아서 처리하다 실패** | Qdrant 의 5xx · **Gemini 임베딩 오류** · 그 밖의 내부 실패 |
| 422 | 보낸 요청이 잘못됨 | 모르는 키, id 형식, 빈 조건, 길이 불일치 |
| 404 | 대상이 없음 | 없는 컬렉션, 슬래시 든 필드의 index 삭제 |

**재시도해도 되는지는 상태 코드가 말해 주지 않는다.** 503 에는 다시 부르면 되는 것(로딩 중·과부하)과 영영 안 되는 것(꺼진 모델)이 섞이고, 500 에도 다시 부르면 되는 것(Gemini 일시 오류)이 섞인다. 무엇이 일시적인지는 부르는 쪽만 안다.

```python
from vdb_manager import VDBConnectionError, VDBHandlerError, VDBUnavailableError

try:
    results = handler.hybrid.search("쿼리", collection_name="kw_gp")
except VDBConnectionError:
    print("서비스에 닿지 못했다")
except VDBUnavailableError as e:
    # 503 — 지금은 받지 못하는 상태. 로딩 중이나 과부하면 잠시 뒤 되지만,
    # 모델이 꺼져 있으면 설정을 바꾸기 전까지 계속 이 상태다. 메시지를 보고 판단한다.
    print(f"지금은 받지 못한다: {e}")
except VDBHandlerError as e:
    print(f"HTTP {e.status_code}: {e}")
```

**재시도는 SDK 가 하지 않는다.** 무엇이 일시적인지는 부르는 쪽의 도메인이고, SDK 는 상태를 갈라 주는 데까지만 한다.

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

### 자주 쓰는 조건에 index 걸기

존재 확인(`has_points`)이나 검색의 `payload_filter`에 쓰는 필드에 index 가 없으면 Qdrant가
전수 스캔한다. 컬렉션이 커질수록 같은 질의가 느려진다.

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
- **`upsert()`는 제거됨**: 서버가 `start_id + j`로 채번해 호출자가 커서를 정확히 기억할 때만 안전했고, 기본값 `0`은 0번부터 기존 포인트를 덮어썼다. 조용한 덮어쓰기는 에러로 드러나지 않는다. `upsert_points(..., ids=[...])`를 쓴다
- **포인트 id**: 부호 없는 정수 또는 UUID만 허용(형식 검증은 Service). 문서에서 유도한 id를 쓸 때 **폭을 줄이지 말 것** — 작은 범위로 접으면 기존 순번 id 구간을 덮어쓴다
- **빈 조건은 거부됨**: `payload_filter`를 받는 모든 메서드(`count_points()`·`has_points()`·검색 3종)에 빈 dict(`{}`)를 넘기면 왕복 전에 `ValueError`. `{}`는 "조건 없음"이 아니라 **조건을 만들려다 비어서 나온** 상태다. Service도 422로 거부하지만 여기서 막으면 호출 자리에서 알게 된다. 조건 없이 전체를 대상으로 하려면 인자를 생략
- **병합과 교체를 헷갈리지 말 것**: `set_payload()`는 주는 키만 더하거나 덮고 나머지를 남기지만, `overwrite_payload()`는 payload를 통째로 바꿔 **주지 않은 키가 전부 사라진다.** 둘은 같은 경로로 가고 HTTP 메서드만 다르다
- **어떤 payload 키도 보호되지 않음**: 존재 확인에 쓰는 정체성 키(`source_pdf` 등)도 `overwrite_payload()`·`clear_payload()`로 사라진다. 재적재 여부는 도메인 소비자가 판단할 일이라 Service가 막지 않는다 (근거는 `KiwoomAM/Kiwoom-Handler`의 ADR 0002)
- **없는 id 는 조용히 건너뛴다**: payload 쓰기에 없는 point id 를 섞어도 오류가 아니라 200이다. 있는 것에만 적용되고 없는 것은 대상이 되지 않는다. **무엇이 살아 있었는지 알아야 하면 `get_points(ids=...)`로 읽을 것** — 없는 id 를 섞어도 200에 있는 것만 돌려준다. 존재 판정은 쓰기가 아니라 읽기의 일이다
- **payload 쓰기 응답은 `count`가 아니라 `requested`**: Qdrant가 실제로 바뀐 개수를 돌려주지 않아 셀 수가 없어서, 요청한 id 수임을 이름으로 드러낸다. `count_points()`가 돌려주는 `count`는 진짜로 센 값이므로 서로 다른 것이다
- **payload 쓰기의 빈 인자는 거부됨**: `ids`·`payload`·`keys`·`field`·`schema`가 비면 왕복 전에 `ValueError`. 대상 0개로 통과하면 Service까지 갔다가 아무것도 바꾸지 않고 성공처럼 보인다. 형식(정수 폭·UUID 모양)은 SDK가 판정하지 않고 Service에 맡긴다
- **큰 id 목록은 호출자가 끊어 보낼 것**: SDK가 쪼개지 않는다. 중간에 실패하면 "몇 개까지 갔는지"를 예외가 실어 나를 수 없어, 그 정보를 아는 호출자의 루프가 필요하다. `upsert_points()`의 `batch_size`와 혼동하지 말 것 — 그것은 HTTP가 아니라 Service→Qdrant 호출을 나누는 인자다
- **index `schema`에 기본값이 없음**: 오타는 Service가 422로 잡지만, 날짜 필드에 `keyword`를 거는 것은 유효한 요청이라 성공하고 range 질의만 index 를 타지 못한 채 느려진다. index 는 오래 사는 물건이라 그 침묵이 길다
- **이름에 슬래시가 든 필드의 index 는 지울 수 없음**: 만들 수는 있고 조회에도 보이지만 삭제가 404로 돌아온다. 필드명을 잘못 적어서가 아니라 벤더 클라이언트 한계이며, `create_payload_index()`가 만드는 시점에 경고한다 (`KiwoomAM/Kiwoom-Handler#74`)
- **`has_points()`는 "적재 완료"가 아님**: 조건에 맞는 포인트가 하나라도 있으면 True다. 문서 하나가 포인트 여럿이 되는(청킹) 소비자가 이것을 완료 판정으로 쓰면 부분 적재가 영구히 굳는다
- **delete()**: 비가역적. 모든 데이터가 영구 삭제됨
- **받을 payload 항목을 고를 수 있음**: `get_points()`·`scroll_points()`·`iter_points()`의 `with_payload`는 `True`(전체) | `False`(받지 않음) | 항목명 목록이다. 전수 순회에서 효과가 가장 크다 — 운영 컬렉션 실측으로 100포인트가 344,732B에서 11,756B가 됐다. 빈 목록(`[]`)은 왕복 전에 `ValueError`이며, payload가 정말 필요 없으면 `False`를 준다
- **`with_payload`와 `payload_filter`를 헷갈리지 말 것**: 앞은 고른 포인트에서 **어떤 항목을 받을지**(SELECT)이고 뒤는 **어떤 포인트를 고를지**(WHERE)다. 이름이 비슷해 바꿔 쓰기 쉬운데, 서로 다른 축이라 한쪽을 다른 쪽 자리에 주면 조용히 다른 결과가 나온다
- **모르는 항목명은 막히지 않음**: Qdrant payload는 포인트마다 스키마가 없어 "항목명이 틀렸다"와 "그 키를 가진 포인트가 없다"가 같은 상태다. 오타를 내면 빈 payload가 돌아올 뿐 오류가 아니다. 빈 결과가 정상인지는 컬렉션의 도메인을 아는 호출자만 판단할 수 있다 — 순회 전체로 누적한 뒤 검사한다 (근거는 `KiwoomAM/Kiwoom-Handler`의 ADR 0004)
- **순회 종료는 `next_offset is None`으로 판정**: `scroll_points()`를 직접 쓸 때의 이야기다. `0`은 실재하는 포인트 id라 falsy로 끝을 판정하면 첫 페이지에서 멈춘다. `iter_points()`는 이 판정을 대신한다
- **distance 옵션**: `"Cosine"`, `"Euclid"`, `"Dot"` 지원 (기본: `"Cosine"`)
- **에러 처리**: HTTP 통신 기반이므로 `VDBConnectionError`/`VDBHandlerError` 처리 권장
- **하위호환**: v2 메서드명(`embed_text`, `encode`, `create_collection` 등)은 alias로 유지됨. 다만 `upsert()`·`upsert_hybrid()`는 **제거**됐다 — 조용한 덮어쓰기를 부르는 API 라 남겨 둘 수 없었다
- **mode**: 컬렉션 생성 시 `"dense"` 또는 `"hybrid"` 선택 가능 (기본: `"hybrid"`)
