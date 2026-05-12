# MDB Handler SDK

> Kiwoom MongoDB Handler SDK v2.0 — HTTP 클라이언트 기반 MongoDB 접근 라이브러리

MDB Handler Service(REST API)를 통해 MongoDB에 접근하는 Python SDK입니다.
기존 pymongo 기반 인터페이스와 동일한 메서드 시그니처를 유지하면서, 내부적으로 HTTP 통신으로 전환되었습니다.

## 요구사항

- Python >= 3.10
- httpx >= 0.27.0
- MDB Handler Service 실행 중

## 설치

```bash
pip install -e packages/mdb-handler/sdk
```

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `MDB_HANDLER_URL` | `http://192.7.9.45:8300` | MDB Handler Service URL |

## 디렉터리 구조

```
mdb_handler/
├── __init__.py            # 공개 API (sync + aio 노출)
├── _client.py             # httpx 기반 SyncClient (내부용)
├── _async_client.py       # httpx 기반 AsyncClient (내부용)
├── _paths.py              # REST 경로/바디 빌더 (sync/async 공용)
├── _transport.py          # SyncTransport/AsyncTransport Protocol (DIP 경계)
├── handler.py             # MongoDBHandler (Singleton 동기 진입점)
├── exceptions.py          # MDBError, MDBConnectionError, MDBHandlerError
├── collections/
│   └── blocks.py          # BlocksCollection (동기 CRUD/집계)
└── aio/                   # 비동기 인터페이스
    ├── __init__.py        # AsyncMongoDBHandler 등 공개
    ├── handler.py         # AsyncMongoDBHandler (비-Singleton, 명시적 lifecycle)
    └── collections/
        └── blocks.py      # AsyncBlocksCollection (비동기 CRUD/집계)
```

## Quick Start

```python
from mdb_handler import MongoDBHandler

mdb = MongoDBHandler()  # 기본: kw_ai / agent_data

# 조회
docs = mdb.get_all()
doc = mdb.get_by_id("p1_b1")

# 삽입
mdb.insert_one({"block_id": "p1_b1", "content": "내용"})

# 수정
mdb.update_by_id("p1_b1", {"$set": {"content": "수정"}})

# 삭제
mdb.delete_by_id("p1_b1")

# 종료
mdb.close()
```

## API Reference

### MongoDBHandler

Singleton 패턴의 메인 진입점입니다.

```python
MongoDBHandler(
    uri=None,           # Deprecated (v2에서 무시됨)
    database=None,      # 기본값: "kw_ai"
    collection=None,    # 기본값: "agent_data"
)
```

#### Create

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `insert_one(document)` | `str` | 단일 문서 삽입, inserted_id 반환 |
| `insert_many(documents, ordered=True)` | `List[str]` | 다중 문서 삽입 |

#### Read

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `get_all()` | `List[Dict]` | 전체 문서 조회 |
| `get_by_id(block_id)` | `Optional[Dict]` | block_id로 조회 |
| `get_by_source(source_pdf)` | `List[Dict]` | source_pdf로 조회 |
| `get_sample(n=5)` | `List[Dict]` | n개 샘플 |
| `find(filter, **kwargs)` | `List[Dict]` | 조건 검색 (`projection`, `sort`, `limit`, `skip` 지원) |
| `find_one(filter, **kwargs)` | `Optional[Dict]` | 단일 조건 검색 (`projection` 지원) |

#### Update

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `update_one(filter, update, upsert=False)` | `int` | 조건 매칭 첫 문서 수정 |
| `update_many(filter, update, upsert=False)` | `int` | 조건 매칭 전체 수정 |
| `update_by_id(block_id, update, upsert=False)` | `int` | block_id로 수정 |
| `replace_one(filter, replacement, upsert=False)` | `int` | 문서 전체 교체 |

#### Delete

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `delete_one(filter)` | `int` | 조건 매칭 첫 문서 삭제 |
| `delete_many(filter)` | `int` | 조건 매칭 전체 삭제 |
| `delete_by_id(block_id)` | `int` | block_id로 삭제 |
| `delete_by_source(source_pdf)` | `int` | source_pdf 전체 삭제 |
| `drop()` | `None` | 컬렉션 삭제 |

#### 메타데이터 / 통계

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `get_source_list()` | `List[str]` | 고유 source_pdf 목록 |
| `count(filter=None)` | `int` | 문서 수 |
| `count_by_source()` | `Dict[str, int]` | source_pdf별 문서 수 |
| `aggregate(pipeline)` | `List[Dict]` | 집계 파이프라인 |

#### Database / Collection 접근

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `get_collection(name)` | `BlocksCollection` | 같은 DB, 다른 컬렉션 |
| `get_database(db, collection=None)` | `BlocksCollection` | 다른 DB 접근 |
| `list_collections(database=None)` | `List[str]` | 컬렉션 목록 조회 |

#### 생명주기

| 메서드 | 설명 |
|--------|------|
| `health_check()` | 서비스 상태 확인 |
| `close()` | HTTP 클라이언트 종료 |
| `reset()` (classmethod) | Singleton 인스턴스 초기화 |

### BlocksCollection

컬렉션 단위 CRUD 클래스입니다. `MongoDBHandler`의 모든 CRUD 메서드와 동일한 인터페이스를 갖습니다.
직접 생성하지 않고 `get_collection()` / `get_database()`로 얻어서 사용합니다.

```python
reports = mdb.get_collection("overseas_reports")
reports.get_all()
reports.find({"type": "analysis"}, limit=10)
```

### 예외

```
MDBError                    # 기본 에러 (message, status_code)
├── MDBConnectionError      # 서비스 연결 실패
└── MDBHandlerError         # 서비스 에러 응답 (HTTP 4xx/5xx)
```

```python
from mdb_handler import MDBConnectionError, MDBHandlerError

try:
    mdb.find({"type": "table"})
except MDBConnectionError:
    print("서비스 연결 실패")
except MDBHandlerError as e:
    print(f"HTTP {e.status_code}: {e.message}")
```

## 사용 패턴

### 여러 컬렉션 접근

```python
mdb = MongoDBHandler()
reports = mdb.get_collection("overseas_reports")
rag = mdb.get_database("kw_rag", "kw_gp")

reports.get_all()
rag.find({"status": "active"})
```

### 출처 기반 데이터 관리

```python
sources = mdb.get_source_list()
counts = mdb.count_by_source()
mdb.delete_by_source("old_report.pdf")
```

### 집계 파이프라인

```python
result = mdb.aggregate([
    {"$group": {"_id": "$_metadata.type", "count": {"$sum": 1}}},
    {"$sort": {"count": -1}}
])
```

### TTL — 문서 자동 만료

`insert_one`/`insert_many`에 `ttl: int`(단위: **초**)를 넘기면 MongoDB가 해당 문서를 자동 삭제한다.

```python
# 1시간 후 자동 삭제
mdb.insert_one({"block_id": "p1", "content": "..."}, ttl=3600)

# 다건 — 모든 문서에 동일 TTL 적용
mdb.insert_many([doc1, doc2], ttl=86400)

# 비동기도 동일
await aio_mdb.insert_one({"a": 1}, ttl=600)
```

내부 동작:
- 문서에 `_expires_at` Date 필드가 자동 추가됨 (`utcnow() + ttl`)
- 컬렉션에 `expireAfterSeconds: 0` TTL 인덱스가 자동 생성됨 (Service 측 메모리 캐시로 중복 호출 방지, 첫 호출에서만 실제 createIndex)
- `ttl=None`(기본)이면 기존 동작과 완전히 동일 — 만료 없음
- MongoDB TTL 모니터는 약 60초 주기로 동작 → 실제 삭제는 만료 시각 +α초 지연 가능
- `ttl<=0`은 422로 거부됨

## 주의사항

- **`uri` 파라미터**: v2에서 Deprecated. 전달 시 무시되며 DeprecationWarning 발생
- **Singleton**: 첫 생성 시 인자만 반영됨. 변경 필요 시 `MongoDBHandler.reset()` 후 재생성
- **`drop()`**: 비가역적. 컬렉션 전체가 삭제됨
- **타임아웃**: 기본 30초 (SyncClient 기준)
- **에러 처리**: HTTP 통신 기반이므로 `MDBConnectionError`/`MDBHandlerError` 처리 권장

## v1 → v2 마이그레이션

| 항목 | v1 | v2 |
|------|----|----|
| 통신 | pymongo 직접 연결 | HTTP (httpx) |
| `uri` 파라미터 | MongoDB URI | 무시됨 (Deprecated) |
| DB 연결 관리 | 클라이언트 측 | Service 측 |
| 환경변수 | `MONGODB_URI` 등 | `MDB_HANDLER_URL` |

기존 코드에서 `uri` 인자만 제거하면 대부분의 호출이 그대로 동작합니다.

## 비동기 사용법 (Async)

FastAPI, LangGraph 등 async 환경에서는 `mdb_handler.aio`를 사용하세요.
동기 인터페이스(`MongoDBHandler`)는 그대로 유지되며, 비동기 인터페이스는 별도 경로로 제공됩니다.

### 기본 사용 (context manager 권장)

```python
from mdb_handler.aio import AsyncMongoDBHandler

async def handler():
    async with AsyncMongoDBHandler(database="kw_rag", collection="kw_gp") as mdb:
        docs = await mdb.find({"type": "table"}, limit=10)
        return docs
```

### FastAPI lifespan 통합

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from mdb_handler.aio import AsyncMongoDBHandler


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.mdb = AsyncMongoDBHandler()
    try:
        yield
    finally:
        await app.state.mdb.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/docs")
async def list_docs(request: Request):
    return await request.app.state.mdb.find({}, limit=20)
```

### 병렬 쿼리 (성능 이득의 핵심 사용처)

```python
import asyncio
from mdb_handler.aio import AsyncMongoDBHandler

async def fan_out():
    async with AsyncMongoDBHandler() as mdb:
        tables, figures, count = await asyncio.gather(
            mdb.find({"type": "table"}, limit=10),
            mdb.find({"type": "figure"}, limit=10),
            mdb.count({"type": "text"}),
        )
        return tables, figures, count
```

### 동기 ↔ 비동기 비교

| 기능 | 동기 | 비동기 |
|------|------|--------|
| import | `from mdb_handler import MongoDBHandler` | `from mdb_handler.aio import AsyncMongoDBHandler` |
| 인스턴스 | Singleton (`MongoDBHandler()`) | 명시적 인스턴스 (매 호출 새 인스턴스 가능) |
| 호출 | `mdb.find(...)` | `await mdb.find(...)` |
| 종료 | `mdb.close()` / `MongoDBHandler.reset()` | `await mdb.aclose()` 또는 `async with` |
| 공개 메서드 | 동일 | 동일 (반환 타입 일치) |

### 주의사항

- 비동기 핸들러는 **Singleton이 아닙니다.** FastAPI lifespan, LangGraph 워크플로처럼 생명주기가 명확한 곳에서 인스턴스를 직접 관리하세요.
- `aclose()` 호출 후 메서드를 호출하면 `RuntimeError`가 발생합니다.
- 동기 코드와 혼용해도 안전합니다. 두 인터페이스는 **같은 Service API**를 사용하고 상태를 공유하지 않습니다.
