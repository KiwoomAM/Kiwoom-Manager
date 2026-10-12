# kiwoom-rdb-manager

PostgreSQL 공개 클라이언트. rdb-handler **서비스(HTTP)** 를 호출하는 얇은 HTTP 클라이언트다.
연결·풀링·자격증명은 전부 핸들러 서비스가 책임지며, 이 SDK 는 DSN·드라이버·커넥션 풀을 직접 갖지 않는다.

소비자는 이 패키지만 import 한다:

    from rdb_manager import query_to_dataframe, fetch_all_dict, execute_dml
    import rdb_manager.aio as aio   # async

핸들러 URL(`http://192.7.9.45:8700`)은 SDK 내부에 고정되어 있어 env 나 `configure(base_url=...)` 로 바꿀 수 없다 (timeout 은 조정 가능).

```
[agent code]  →  rdb_manager (httpx)  →  RDB Handler Service (:8700)  →  PostgreSQL
```

---

## 설치

```toml
# uv (권장) — pyproject 의 [tool.uv.sources]
kiwoom-rdb-manager = { git = "https://github.com/KiwoomAM/Kiwoom-Manager.git", subdirectory = "rdb-manager", rev = "main" }
```

```bash
# pip (git subdirectory)
pip install "git+https://github.com/KiwoomAM/Kiwoom-Manager.git@main#subdirectory=rdb-manager"
```

> 과거의 `pip install -e packages/rdb-handler` (저수준 psycopg 라이브러리) 단계는 더 이상 필요 없다 —
> rdb-manager 는 순수 HTTP 클라이언트라 `kiwoom-rdb-handler` 에 의존하지 않는다.

---

## 설정

이 클라이언트는 DB 자격증명이나 호스트/포트를 **읽지 않는다** — 그것들은 핸들러 서비스의 몫이다.
핸들러 URL 은 `http://192.7.9.45:8700` 으로 고정되어 있으며, 필요하면 timeout 을 조정한다:

```python
import rdb_manager
rdb_manager.configure(timeout=60.0)   # base_url 인자는 무시됨 (URL 고정), timeout 적용
```

### 연결 테스트

```python
from rdb_manager import check_database_connection

if check_database_connection():
    print("핸들러 → DB 연결 정상")
```

---

## 바인드 변수 형식

> **중요:** PostgreSQL 은 `%(name)s` 형식을 사용합니다. Oracle 의 `:name` 형식(winus-manager)과 다릅니다.

```python
# ✅ PostgreSQL (rdb_manager)
from rdb_manager import query_to_dataframe
df = query_to_dataframe(
    "SELECT * FROM users WHERE id = %(id)s",
    {"id": 1},
)

# ❌ Oracle 형식 — 동작하지 않음
df = query_to_dataframe(
    "SELECT * FROM users WHERE id = :id",
    {"id": 1},
)
```

---

## 사용법

### SELECT → DataFrame

```python
from rdb_manager import query_to_dataframe

df = query_to_dataframe(
    "SELECT * FROM company WHERE code = %(code)s",
    {"code": "A001"},
)
```

### SELECT → dict 리스트 / tuple

```python
from rdb_manager import fetch_all, fetch_all_dict, fetch_one

rows = fetch_all_dict("SELECT code, name FROM company")
# [{"code": "A001", "name": "키움증권"}, ...]

rows = fetch_all("SELECT code, name FROM company")
# [("A001", "키움증권"), ...]

row = fetch_one("SELECT COUNT(*) FROM company")
# (42,)
```

### INSERT / UPDATE / DML

```python
from rdb_manager import execute_insert, execute_update, execute_dml

# INSERT (자동 commit, 영향받은 행 수 반환)
execute_insert("INSERT INTO log (msg) VALUES (%(msg)s)", {"msg": "hello"})

# UPDATE (자동 commit, 영향받은 행 수 반환)
execute_update(
    "UPDATE company SET name = %(name)s WHERE code = %(code)s",
    {"name": "키움", "code": "A001"},
)

# 범용 DML (INSERT/UPDATE/DELETE 모두 가능)
affected = execute_dml("DELETE FROM log WHERE created_at < %(dt)s", {"dt": "2025-01-01"})
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
    params_list,
)
```

### database 고르기

조회·DML 함수는 keyword 인자 `database` 로 접속할 database 를 고른다. 고르지 않으면 핸들러의 기본
database(`kw_ai`)에 붙는다. 고를 수 있는 이름은 핸들러가 정하며(지금 `kw_ai`·`kw_sandbox`), 그 밖의
이름은 핸들러가 `422` 로 거절한다.

```python
from rdb_manager import execute_dml, fetch_all_dict

execute_dml("CREATE TABLE my_schema.scratch (id int)", database="kw_sandbox")
rows = fetch_all_dict("SELECT * FROM my_schema.scratch", database="kw_sandbox")
```

---

## Async API (`rdb_manager.aio`)

sync 와 함수 이름·시그니처·반환 타입이 1:1 동일하며 import path 로만 구분한다 (async 는 `await`).

```python
from rdb_manager.aio import fetch_all_dict, execute_dml

rows = await fetch_all_dict("SELECT * FROM t WHERE id = %(id)s", {"id": 1})
await execute_dml("INSERT INTO t (col) VALUES (%(v)s)", {"v": "data"})
```

### 수명주기 (FastAPI lifespan 등)

async 클라이언트는 내부에 httpx 커넥션 풀을 두므로, 애플리케이션 종료 시 정리해야 한다:

```python
from rdb_manager.aio import close

# 앱 종료 또는 FastAPI lifespan 종료 시
await close()   # AsyncClient 풀 정리
```

> `aio.configure()` 는 동기 함수라, 살아있는 인스턴스를 둔 채 재호출하면 이전 AsyncClient 풀을
> 닫지 못해 누수된다(런타임 경고). 재설정하려면 먼저 `await close()` 를 호출한 뒤 `configure()` 하라.

---

## API 레퍼런스

sync(`from rdb_manager import ...`)와 async(`from rdb_manager.aio import ...`)가 **동일 시그니처**를 노출한다 (async 는 `await`).

| 함수 | 반환값 | 설명 |
|------|--------|------|
| `query_to_dataframe(sql, params, *, database=None)` | `DataFrame` | SELECT 결과를 pandas DataFrame 으로 반환 |
| `fetch_all(sql, params, *, database=None)` | `list[tuple]` | 전체 행을 튜플 리스트로 반환 |
| `fetch_all_dict(sql, params, *, database=None)` | `list[dict]` | 전체 행을 딕셔너리 리스트로 반환 |
| `fetch_one(sql, params, *, database=None)` | `tuple` 또는 `None` | 첫 번째 행만 반환 |
| `execute_dml(sql, params, *, database=None)` | `int` | DML 실행, 자동 commit, 영향받은 행 수 반환 |
| `execute_many_dml(sql, params_list, *, database=None)` | `int` | 배치 DML 실행 |
| `execute_insert(sql, params, *, database=None)` | `int` | `execute_dml` 별칭 (의미상 INSERT 용도) |
| `execute_update(sql, params, *, database=None)` | `int` | `execute_dml` 별칭 (의미상 UPDATE 용도) |
| `check_database_connection()` | `bool` | 연결 테스트 |
| `configure(base_url=None, timeout=30.0)` | `None` | timeout 적용 (base_url 무시, URL 고정) |
| `close()` | `None` | 클라이언트/풀 정리 (async 는 `await close()`) |

> `params` 는 PostgreSQL named bind 형식(`%(name)s`)을 사용합니다.
> `execute_insert` / `execute_update` 는 SQL 종류를 검증하지 않습니다 (둘 다 `execute_dml` 별칭).

---

## 예외 처리

> **중요:** HTTP SDK 전환 이후 **쿼리·연결 오류는 `HandlerError` / `HandlerConnectionError` 로 통일**되었습니다.
> 클라이언트는 더 이상 `QueryExecutionException` / `ConnectionPoolException` / `DatabaseException` 을 raise 하지 않습니다.

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
> import 호환을 위해 export 만 유지되며, 현재 클라이언트 경로에서는 raise 되지 않습니다.

### 접근 등급

SDK 는 요청마다 핸들러의 접근 등급을 정하는 키를 함께 보낸다(Kiwoom-Handler `docs/adr/0001`). 소비자가
설정할 것은 없다 — SDK 를 거친 호출은 지금처럼 모든 기능을 쓴다. 핸들러가 키를 거절하면 `HandlerError` 의
`status_code` 가 `401`(키를 알아볼 수 없음)이나 `403`(그 등급에서 쓸 수 없는 경로)이다. 재시도하지 말고
SDK 버전을 확인한다. `check_database_connection()` 은 이때도 `False` 를 돌려주되 원인을 ERROR 로그로 남긴다.

---

## 의존성

| 패키지 | 용도 |
|--------|------|
| `httpx` | 핸들러 서비스 HTTP 호출 |
| `pydantic` | 요청/응답 모델 |
| `pandas` | `query_to_dataframe` 반환 |

> 저수준 드라이버(`psycopg`)나 `kiwoom-rdb-handler` 에는 의존하지 않는다 — DB 접속은 핸들러 서비스가 담당한다.
