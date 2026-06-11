# rdb-manager HTTP SDK 전환 설계

작성일: 2026-06-11
브랜치: `refactor/rdb-manager-http-sdk`
범위: `Kiwoom-Manager/rdb-manager` 만 (rdb-handler 서비스·서버 배포는 불변)

## 배경 / 문제

현재 `rdb-manager`는 다른 매니저(api/file/llm/mdb/vdb/winus)와 달리 **HTTP 요청 방식이 아니라 라이브러리 import 방식**으로 구현되어 있다.

- `pyproject.toml`이 `kiwoom-rdb-handler`를 git 의존성으로 끌어온다.
- `query.py`/`async_query.py`가 `from rdb_handler.connection import get_connection`으로 **psycopg를 통해 PG(PgBouncer)에 직접 접속**한다.
- `deps.py`/`async_deps.py`/`__init__.py`가 `get_connection`/`get_pg_connection`/`PostgresConfig`를 그대로 재export한다.

반면 요청을 받을 **rdb-handler 서비스는 이미 완성**되어 있다 (port 8700, PG 직결 5432, winus와 동일한 엔드포인트). 따라서 rdb-manager만 winus-manager와 동일한 httpx SDK 클라이언트로 전환하면 된다.

## 목표

`rdb-manager`를 `rdb_handler`/`psycopg` 의존 없이 rdb-handler 서비스(`http://192.7.9.45:8700`)에 HTTP 요청을 보내는 순수 클라이언트로 재작성한다. 공개 함수 API의 시그니처와 import 경로(`rdb_manager` 동기 / `rdb_manager.aio` 비동기)는 보존한다.

## 레퍼런스

`winus-manager`가 동일 패턴의 검증된 레퍼런스다. rdb-handler 서비스 엔드포인트가 winus-handler와 동일하므로 클라이언트 로직은 거의 1:1로 이식된다.

핸들러 서비스 엔드포인트(이미 구현됨):
- `POST /v1/query/dataframe` → `{columns, rows, dtypes, request_id}`
- `POST /v1/query/all` → `{rows, request_id}`
- `POST /v1/query/all-dict` → `{rows, request_id}`
- `POST /v1/query/one` → `{row|null, request_id}`
- `POST /v1/dml/execute` · `/execute-many` · `/insert` · `/update` → `{rowcount, request_id}`
- `GET /v1/check` → `{connected}`
- `GET /health`

요청 바디: SELECT는 `{query, params?}`, DML은 `{sql, params?}`, execute-many는 `{sql, params_list}`.

## 아키텍처

winus-manager와 **1:1 동형 구조**. 싱글톤 클라이언트(`_instance`)를 facade(`__init__.py` 동기 / `aio.py` 비동기)에서 lazy 생성하고, 함수 API가 이를 위임한다.

### 패키지 구성 (`rdb_manager/`)

| 파일 | 역할 | 출처 |
|---|---|---|
| `_config.py` | `HANDLER_BASE_URL = "http://192.7.9.45:8700"` 단일 출처 | 신규 |
| `_client.py` | `AsyncClient` (`httpx.AsyncClient`) | winus 이식 |
| `_sync_client.py` | `SyncClient` (`httpx.Client`) | winus 이식 |
| `models.py` | `DataFrameData`/`RowsData`/`DictRowsData`/`OneRowData`/`DmlResult` (pydantic) | winus 이식 |
| `exceptions.py` | `DatabaseException`/`ConnectionPoolException`/`QueryExecutionException` (호환) + `SDKError`/`HandlerConnectionError`/`HandlerError` | winus 이식 |
| `__init__.py` | 동기 facade (싱글톤 + 함수 API + `configure`/`close`) | 재작성 |
| `aio.py` | 비동기 facade | 재작성 |
| `query.py`, `async_query.py`, `deps.py`, `async_deps.py` | **삭제** (psycopg 직결 로직 제거) | — |

### 공개 API (현행 함수 시그니처 보존)

- SELECT: `query_to_dataframe`, `fetch_all`, `fetch_all_dict`, `fetch_one`
- DML: `execute_dml`, `execute_many_dml`, `execute_insert`, `execute_update`
- Health: `check_database_connection`
- Lifecycle: `configure(timeout=...)`, `close()`
- 동기 = `rdb_manager`, 비동기 = `rdb_manager.aio` (현행 import 경로 유지)
- **제거**: `get_connection`, `get_pg_connection`, `PostgresConfig` — HTTP SDK는 살아있는 psycopg 커넥션을 yield할 수 없으므로 완전 제거 (winus와 동일). 워크스페이스 내 외부 소비처 없음을 확인함.

## winus 대비 조정 사항

1. `base_url` → `:8700` (winus는 `:8200`)
2. 에러 메시지 prefix `[위너스 핸들러]` → `[RDB 핸들러]`
3. 바인드 변수 docstring/예시 → psycopg 스타일 `%(name)s` (winus는 Oracle `:name`). 서버가 바인딩하므로 SDK는 pass-through, 로직 변경 없음.
4. dtype 복원·요청 바디 구성 로직은 그대로 재사용.

## base_url 정책 (잠금)

`_config.py`의 `HANDLER_BASE_URL`을 단일 출처로 하드코딩하고, env 변수 및 `configure(base_url=...)` 인자를 **무시**한다 (winus와 동일, 엔드포인트 잠금 정책 일치). `configure()`는 `timeout`만 적용한다.

## pyproject.toml

```
dependencies = [
    "httpx>=0.27.0",
    "pydantic>=2.5.0",
    "pandas>=2.0.0",
]
```

`psycopg[binary]`, `kiwoom-rdb-handler` 제거 (winus-manager와 동일 구성).

## 에러 처리

- 연결 실패(`httpx.ConnectError`) → `HandlerConnectionError`
- HTTP 4xx/5xx → 응답 바디의 `error`/`detail`을 담은 `HandlerError(status_code=...)`
- `check_database_connection`은 예외를 삼키고 `False` 반환 (현행/winus 동작 보존)

## 테스트

- 기존 테스트(`conftest.py`/`test_aio`/`test_async_deps`/`test_async_query`/`test_integration`)는 psycopg 모킹 기반 → **삭제·대체**
- `test_base_url_locked.py` 이식: `LOCKED_URL = "http://192.7.9.45:8700"`, env/`configure(base_url=...)`/`_resolve_base_url` 모두 잠금 검증 (동기·비동기)
- httpx `MockTransport` 기반 클라이언트 단위 테스트 추가: 각 엔드포인트의 요청 path·바디 구성, 응답 파싱(DataFrame dtype 복원 포함), 에러 변환(`HandlerConnectionError`/`HandlerError`)

## 검증

- `pytest`(rdb-manager) 전부 통과
- 패키지 import smoke: `import rdb_manager`, `import rdb_manager.aio`가 psycopg/rdb_handler 없이 성공
- 제거된 심볼(`get_connection` 등)이 더 이상 export되지 않음을 확인

## 비범위 (Out of scope)

- rdb-handler 서비스·서버 코드 및 배포 (이미 완성, 읽기전용)
- 외부 소비처 마이그레이션 (현재 워크스페이스에 소비처 없음)
- main 머지·origin push (사용자 별도 지시 시)
