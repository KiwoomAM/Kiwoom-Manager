# kiwoom-rdb-manager

PostgreSQL 공개 클라이언트 (PgBouncer 경유). 저수준 연결은 `kiwoom-rdb-handler`에 의존한다.

소비자는 이 패키지만 import한다:

    from rdb_manager import query_to_dataframe, fetch_all_dict, execute_dml
    import rdb_manager.aio as aio   # async

엔드포인트(`192.7.9.45:6432`)는 handler의 config에 잠겨 있어 env로 변경 불가.

---

## 설치

```bash
# 로컬 개발
pip install -e packages/rdb-handler
pip install -e packages/rdb-manager

# Docker (git subdirectory)
pip install \
  "git+https://github.com/KIWOOMxSNU/Kiwoom-Rag.git@main#subdirectory=packages/rdb-handler" \
  "git+https://github.com/KIWOOMxSNU/Kiwoom-Rag.git@main#subdirectory=packages/rdb-manager"
```

---

## 처음 시작하기

### 환경변수 설정 (.env)

`PG_HOST` / `PG_PORT`는 SDK 내부에서 `192.7.9.45:6432`로 고정되어 env 값은 무시됩니다.  
자격증명만 설정하면 됩니다:

```env
PG_DATABASE=kw_ai
PG_USER=kiwoom
PG_PASSWORD=secret
```

### 연결 테스트

```python
from dotenv import load_dotenv
load_dotenv()  # ⚠️ 반드시 rdb_manager import 전에 호출

from rdb_handler import check_database_connection

if check_database_connection():
    print("DB 연결 성공!")
```

> **주의:** `load_dotenv()`는 반드시 import 이전에 호출해야 합니다.  
> 이미 import한 후라면 `from rdb_handler import PostgresConfig; PostgresConfig.reload_from_env()`를 호출하세요.

---

## 바인드 변수 형식

> **중요:** PostgreSQL은 `%(name)s` 형식을 사용합니다. Oracle의 `:name` 형식과 다릅니다.

```python
# ✅ PostgreSQL (rdb_manager)
from rdb_manager import query_to_dataframe
df = query_to_dataframe(
    "SELECT * FROM users WHERE id = %(id)s",
    {"id": 1}
)

# ❌ Oracle 형식 — 동작하지 않음
df = query_to_dataframe(
    "SELECT * FROM users WHERE id = :id",
    {"id": 1}
)
```

---

## 사용법

### SELECT → DataFrame

```python
from rdb_manager import query_to_dataframe

df = query_to_dataframe(
    "SELECT * FROM company WHERE code = %(code)s",
    {"code": "A001"}
)
print(df)
```

### SELECT → dict 리스트

```python
from rdb_manager import fetch_all_dict

rows = fetch_all_dict("SELECT code, name FROM company")
# [{"code": "A001", "name": "키움증권"}, ...]
```

### SELECT → tuple

```python
from rdb_manager import fetch_all, fetch_one

rows = fetch_all("SELECT code, name FROM company")
# [("A001", "키움증권"), ...]

row = fetch_one("SELECT COUNT(*) FROM company")
# (42,)
```

### INSERT / UPDATE / DML

```python
from rdb_manager import execute_insert, execute_update, execute_dml

# INSERT (자동 commit, 영향받은 행 수 반환)
execute_insert(
    "INSERT INTO log (msg) VALUES (%(msg)s)",
    {"msg": "hello"}
)

# UPDATE (자동 commit, 영향받은 행 수 반환)
execute_update(
    "UPDATE company SET name = %(name)s WHERE code = %(code)s",
    {"name": "키움", "code": "A001"}
)

# 범용 DML (INSERT/UPDATE/DELETE 모두 가능)
affected = execute_dml(
    "DELETE FROM log WHERE created_at < %(dt)s",
    {"dt": "2025-01-01"}
)
print(f"{affected}건 삭제")
```

### 배치 DML

```python
from rdb_manager import execute_many_dml

params_list = [
    {"code": "A001", "name": "키움증권"},
    {"code": "A002", "name": "키움투자"},
]
affected = execute_many_dml(
    "INSERT INTO company (code, name) VALUES (%(code)s, %(name)s)",
    params_list
)
```

### Connection 직접 사용

저수준 커넥션이 필요한 경우:

```python
from rdb_handler import get_connection

with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT 1")
        result = cur.fetchone()
```

---

## FastAPI 연동

```python
from fastapi import APIRouter, Depends
from rdb_manager import get_pg_connection

router = APIRouter()

@router.get("/users")
def get_users(conn=Depends(get_pg_connection)):
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM users")
        columns = [d[0] for d in cur.description]
        rows = cur.fetchall()
    return [dict(zip(columns, row)) for row in rows]
```

> 풀링은 PgBouncer가 담당하며, 클라이언트 측 풀은 제공하지 않습니다.

---

## Async API

FastAPI 등 async 환경에서 쓸 수 있는 native async 인터페이스. sync API와 1:1 대응되며 함수 이름·시그니처·반환 타입이 동일하고, import path로만 구분합니다.

```python
import rdb_manager.aio as aio

# async SELECT → dict
rows = await aio.fetch_all_dict("SELECT * FROM t WHERE id = %(id)s", {"id": 1})

# async DML
await aio.execute_dml("INSERT INTO t (col) VALUES (%(v)s)", {"v": "data"})

# AsyncConnection 직접 사용
from rdb_handler.async_connection import get_connection
async with get_connection() as conn:
    async with conn.cursor() as cur:
        await cur.execute("SELECT 1")
```

### FastAPI 비동기 의존성

```python
from fastapi import Depends
from rdb_manager.aio import get_pg_connection

@router.get("/example")
async def example(conn = Depends(get_pg_connection)):
    async with conn.cursor() as cur:
        await cur.execute("SELECT 1")
```

### 정책

- Connection 획득은 sync와 동일 per-call 방식. PgBouncer가 서버측 풀링을 담당하므로 클라이언트 측 풀은 두지 않는다.
- `prepare_threshold=0`: PgBouncer transaction 모드 호환을 위해 서버 사이드 prepared statement 비활성화.
- 기존 sync API(`from rdb_manager import ...`)는 변경 없음.

---

## API 레퍼런스

### 쿼리 함수 (`from rdb_manager import ...`)

| 함수 | 반환값 | 설명 |
|------|--------|------|
| `query_to_dataframe(sql, params)` | `DataFrame` | SELECT 결과를 pandas DataFrame으로 반환 |
| `fetch_all(sql, params)` | `List[tuple]` | 전체 행을 튜플 리스트로 반환 |
| `fetch_all_dict(sql, params)` | `List[Dict]` | 전체 행을 딕셔너리 리스트로 반환 |
| `fetch_one(sql, params)` | `Optional[tuple]` | 첫 번째 행만 반환 |
| `execute_dml(sql, params)` | `int` | DML 실행, 자동 commit, 영향받은 행 수 반환 |
| `execute_many_dml(sql, params_list)` | `int` | 배치 DML 실행 |
| `execute_insert(sql, params)` | `int` | `execute_dml`의 별칭 (의미상 INSERT 용도) |
| `execute_update(sql, params)` | `int` | `execute_dml`의 별칭 (의미상 UPDATE 용도) |

> `params`는 PostgreSQL named bind 형식 (`%(name)s`)을 사용합니다.  
> `execute_insert` / `execute_update`는 SQL 종류를 검증하지 않습니다.

### 커넥션 (`from rdb_handler import ...`)

| 함수 | 반환값 | 설명 |
|------|--------|------|
| `get_connection()` | context manager → `Connection` | 커넥션 획득 (with문 사용, per-call) |
| `check_database_connection()` | `bool` | 연결 테스트 (SELECT 1) |

### FastAPI 의존성

| 함수 | 설명 |
|------|------|
| `from rdb_manager import get_pg_connection` | `Depends()`용 sync 커넥션 제너레이터 |
| `from rdb_manager.aio import get_pg_connection` | `Depends()`용 async 커넥션 제너레이터 |

---

## 예외 처리

> **중요:** HTTP SDK 전환 이후 **쿼리·연결 오류는 `HandlerError` / `HandlerConnectionError`로 통일**되었습니다. 클라이언트는 더 이상 `QueryExecutionException` / `ConnectionPoolException` / `DatabaseException`을 raise하지 않습니다. 기존 `except QueryExecutionException` 코드는 오류를 잡지 못하므로 아래 패턴으로 변경하세요.

```python
from rdb_manager import query_to_dataframe, HandlerError, HandlerConnectionError

try:
    df = query_to_dataframe("SELECT * FROM no_table")
except HandlerError as e:
    print(e.message)       # "[RDB 핸들러] ..."
    print(e.status_code)   # 핸들러 HTTP 상태 코드 (예: 400, 500)
    print(e.detail)        # 핸들러가 내려준 상세 (있을 때)
except HandlerConnectionError as e:
    print(e.message)       # 핸들러 미응답 / 타임아웃 / 연결 실패
```

예외 계층:

```
SDKError                      # SDK 기본 에러
├── HandlerConnectionError    # 핸들러 연결 실패·타임아웃 (status_code=0)
└── HandlerError              # 핸들러가 4xx/5xx 에러 응답 (status_code 보존)
```

> 레거시 예외(`DatabaseException` / `ConnectionPoolException` / `QueryExecutionException`)는
> import 호환을 위해 export만 유지되며, 현재 클라이언트 경로에서는 raise되지 않습니다.

---

## 환경변수

| 변수명 | 필수 | 기본값 | 설명 |
|--------|------|--------|------|
| `PG_HOST` | ✗ 무시 | `192.7.9.45` (고정) | 운영 정책상 SDK 내부 고정 |
| `PG_PORT` | ✗ 무시 | `6432` (고정) | 운영 정책상 SDK 내부 고정 |
| `PG_DATABASE` | O | - | 데이터베이스명 |
| `PG_USER` | O | - | 접속 사용자 |
| `PG_PASSWORD` | O | - | 비밀번호 |
| `PG_CONNECT_TIMEOUT` | - | `5` | 접속 타임아웃(초) |

---

## 의존성

| 패키지 | 버전 |
|--------|------|
| `kiwoom-rdb-handler` | 동일 레포 |
| `psycopg[binary]` | >= 3.1.0 |
| `pandas` | >= 2.0.0 |
| `python-dotenv` | >= 1.0.0 |
| Python | >= 3.10 |
