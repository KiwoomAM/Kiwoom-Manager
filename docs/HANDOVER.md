# Oracle/RDB 핸들러·매니저 인수인계 (HANDOVER)

> 작성일 2026-06-25 / 대상: 다음 유지보수 담당자 / 범위: winus-manager + rdb-manager + (Kiwoom-Handler) rdb-handler
>
> 본 문서는 `Kiwoom-Manager` 레포에 둔다. 사실 검증은 실제 소스 파일을 읽어 수행했으며,
> 확인하지 못한 항목은 명시적으로 `확인 필요`로 표기했다.

---

## 1. 개요

이 인수인계가 다루는 컴포넌트는 **소비처(ETF / EM / pdfCollector / ESG / Rag 등)가 사내 Oracle·PostgreSQL DB에 접근하는 경로**를 구성하는 3개 부품이다.

| 컴포넌트 | 레포 / 위치 | 역할 | 대상 DB |
|----------|-------------|------|---------|
| `winus-manager` | `Kiwoom-Manager/winus-manager` | 소비처가 import하는 **HTTP SDK 클라이언트** | Oracle(WINUS) |
| `rdb-manager` | `Kiwoom-Manager/rdb-manager` | 소비처가 import하는 **HTTP SDK 클라이언트** | PostgreSQL |
| `rdb-handler` | `Kiwoom-Handler/rdb-handler` | DB 연결 + 제너릭 SQL 실행기 **서버**(FastAPI) | PostgreSQL |

접근 흐름:

```
[ETF / EM / pdfCollector / ESG / Rag 등 소비처 코드]
        │  import winus_manager / rdb_manager  (httpx)
        ▼
  Handler 서비스 (FastAPI, 운영 서버 192.7.9.45)
    ├─ winus-handler : 8200  →  Oracle(WINUS)
    └─ rdb-handler   : 8700  →  PostgreSQL(5432, 직결)
```

핵심: 소비처는 **매니저만 import**하고, 매니저는 **HTTP로 핸들러 서비스를 호출**한다. 소비처가 psycopg/oracledb로 DB에 직접 붙는 일은 없다.

> ⚠ **레포 분리 이력(중요).** 과거에는 핸들러·매니저가 모두 `Kiwoom-Rag/packages/` 아래 있었으나, 이후 `Kiwoom-Handler`(핸들러 서버 모음) / `Kiwoom-Manager`(클라이언트 SDK 모음) 두 레포로 **분리**되었다. `Kiwoom-Rag` main에서는 `packages/`가 통째로 제거됐다(커밋 `04de82c "refactor: remove packages/ after split into Kiwoom-Handler/Manager repos"`). 따라서 작업 디렉터리에 남아 있는 `Kiwoom-Rag/packages/rdb-handler/`는 **stale한 로컬 잔재**(git에 추적되지 않으며 .py 소스 없이 `build/`·`.pyc`만 남음)이고, **권위 있는 현행 소스는 `Kiwoom-Handler/rdb-handler/`** 다. 이 문서의 핸들러 사실은 `Kiwoom-Handler` 기준으로 작성했다.

---

## 2. 설계 원칙 (경계를 지킬 것)

1. **핸들러(handler) = 연결 + 제너릭 SQL 실행기.**
   - 핸들러는 DB 커넥션 풀 관리 + 들어온 SQL을 그대로 실행하는 제너릭 엔드포인트(`/v1/query/*`, `/v1/dml/*`)만 제공한다.
   - **도메인 SQL(특정 테이블/비즈니스 쿼리)을 핸들러에 넣지 말 것.** 도메인 SQL은 소비처 service 쪽에 둔다.
2. **매니저(manager) = HTTP SDK 클라이언트.**
   - 매니저는 핸들러 서비스에 httpx로 요청을 보내는 얇은 클라이언트다. DB 드라이버(psycopg/oracledb) 의존이 없다.
   - 공개 함수(`query_to_dataframe`, `fetch_all_dict`, `execute_dml` 등)의 시그니처/반환 타입은 sync(`rdb_manager`)·async(`rdb_manager.aio`)가 1:1 동일하고 import 경로로만 구분한다.
3. **엔드포인트는 코드에 잠겨 있다.** 매니저의 핸들러 URL은 단일 출처 상수(`_config.py`의 `HANDLER_BASE_URL`)에 고정되며 env/`configure(base_url=...)`로 바꿀 수 없다. 자격증명/timeout만 외부 설정 가능.

이 경계를 어기면(예: 매니저에 psycopg를 다시 끌어오거나, 핸들러에 도메인 쿼리를 박으면) 분리 구조가 무너진다.

---

## 3. 컴포넌트별 상세

### 3.1 winus-manager (Oracle 클라이언트 SDK)

- **무엇:** WINUS(Oracle) 게이트웨이 서버(`winus-handler`, 포트 8200)를 호출하는 httpx 클라이언트. import 이름은 `winus_manager`.
- **공개 API**(`winus_manager/__init__.py`, 동기 / `winus_manager/aio.py`, 비동기):
  `query_to_dataframe`, `fetch_all`, `fetch_all_dict`, `fetch_one`, `execute_dml`, `execute_many_dml`, `execute_insert`, `execute_update`, `check_database_connection`, `configure`, `close`.
- **바인드 변수 형식:** Oracle이므로 `:name` (예: `"... WHERE code = :code", {"code": "A001"}`).
- **엔드포인트 잠금:** `winus_manager/_config.py` → `HANDLER_BASE_URL = "http://192.7.9.45:8200"`. env `WINUS_HANDLER_URL`·`configure(base_url=...)` 모두 무시(테스트 `tests/test_base_url_locked.py`가 잠금 검증).
- **의존성:** httpx, pydantic, pandas (oracledb 없음 — DataFrame은 서버가 보내는 columns+rows+dtypes로 클라이언트에서 복원).

#### Oracle 11.2.0.3 / Thick 모드 제약 (서버 측, 반드시 이해할 것)

이 제약은 **핸들러(winus-handler) 서버**의 근본 제약이며, 매니저가 native-async를 못 쓰는 이유이기도 하다. 근거 문서: `Kiwoom-Handler/winus-handler/ASYNC.md`.

- WINUS 운영 Oracle 서버(`WRI1`) 버전이 **11.2.0.3.0**.
- `python-oracledb`의 **Thin 모드는 Oracle 12.1 이상만 지원**한다 → 11.2에 Thin으로 붙으면 즉시 `DPY-3010: connections to this database server version are not supported by python-oracledb in thin mode`로 끊긴다.
- 따라서 **Thick 모드(= Oracle Instant Client C 라이브러리 위임) 필수**.
- 그런데 oracledb의 **native async API(`AsyncConnection`, `create_pool_async`)는 Thin 모드에서만** 존재하고 Thick 모드엔 async 메서드 자체가 없다 → **winus-handler를 native-async로 만들 수 없음.**
- 우회: 서버는 동기 Oracle 호출을 `asyncio.to_thread()`로 별도 스레드에서 돌려 이벤트 루프 블로킹을 피한다(`WINUS.md` Gotchas 참조).
- Instant Client는 Docker 이미지에 포함(`winus-handler/service/Dockerfile`이 Instant Client **19.21** 설치). 로컬에 클라이언트가 없으면 `USE_THICK_MODE=false`로 Thin을 쓸 수 있으나, **운영 11.2에는 Thin 불가**이므로 로컬 한정.

#### logon trigger MODULE 차단 + `exec -a` 우회 (자주 나는 함정)

- WINUS DB에는 **MODULE이 `python*`로 시작하는 wruser 세션을 차단**하는 logon trigger가 있다. 그냥 `python -m uvicorn ...`으로 띄우면 OCI가 MODULE을 `python@<host>`로 보고하여 로그인 트리거에 막힌다.
- 우회: 컨테이너 CMD를 `exec -a <name>`으로 감싸 argv[0]을 바꾼다. 실제 코드(`winus-handler/service/Dockerfile`):
  ```dockerfile
  CMD ["bash", "-c", "exec -a winus_handler python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8200"]
  ```
  → OCI가 MODULE을 `winus_handler@<host>`로 보고하므로 트리거를 통과한다.
- **새 서비스/스크립트를 WINUS에 붙일 때마다 이 래핑이 필요**하다. (메모리 `project_winus_logon_trigger` 와 일치.)

#### 연결 정보 위치

- 매니저 측: Oracle 접속정보를 알 필요 없음(핸들러가 갖고 있음). 핸들러 URL만 상수로 고정.
- 핸들러 측: `winus-handler/service/app/core/config_oracle.py` (`DatabaseConfig`)가 env로 읽음 — `ORACLE_HOST`/`ORACLE_PORT(기본 1521)`/`ORACLE_SID`(예 `WRI1`) 또는 `ORACLE_SERVICE_NAME`/`ORACLE_USER`/`ORACLE_PASSWORD`/`USE_THICK_MODE`.

### 3.2 rdb-manager (PostgreSQL 클라이언트 SDK)

- **무엇:** PostgreSQL 핸들러 서비스(`rdb-handler`, 포트 8700)를 호출하는 httpx 클라이언트. import 이름은 `rdb_manager`.
- **현재 형태 = HTTP SDK** (2026-06-11 마이그레이션 완료, 설계 `docs/superpowers/specs/2026-06-11-rdb-manager-http-sdk-design.md`, 계획 `docs/superpowers/plans/2026-06-11-rdb-manager-http-sdk.md`). 과거의 `psycopg`/`rdb_handler` 직결 라이브러리에서 winus-manager와 동일한 httpx 클라이언트로 재작성됨.
- **사용법 (SDK API):**
  ```python
  import rdb_manager
  df   = rdb_manager.query_to_dataframe("SELECT * FROM company WHERE code = %(code)s", {"code": "A001"})
  rows = rdb_manager.fetch_all_dict("SELECT code, name FROM company")
  rdb_manager.execute_dml("INSERT INTO log (msg) VALUES (%(msg)s)", {"msg": "hi"})

  import rdb_manager.aio as aio   # async, 함수명·시그니처 동일
  rows = await aio.fetch_all_dict("SELECT * FROM t WHERE id = %(id)s", {"id": 1})
  ```
- **바인드 변수 형식:** PostgreSQL이므로 **`%(name)s`** (Oracle의 `:name`과 다름 — 헷갈리기 쉬움).
- **공개 API:** winus-manager와 동일 함수 집합(`query_to_dataframe`/`fetch_all`/`fetch_all_dict`/`fetch_one`/`execute_dml`/`execute_many_dml`/`execute_insert`/`execute_update`/`check_database_connection`/`configure`/`close`).
- **엔드포인트 잠금:** `rdb_manager/_config.py` → `HANDLER_BASE_URL = "http://192.7.9.45:8700"`. env `RDB_HANDLER_URL`·`configure(base_url=...)` 무시(`tests/test_base_url_locked.py` 검증).
- **예외:** HTTP SDK 전환 후 오류는 `HandlerError`(핸들러 4xx/5xx, status_code 보존) / `HandlerConnectionError`(연결 실패·타임아웃)로 통일. 레거시 `DatabaseException`/`ConnectionPoolException`/`QueryExecutionException`은 import 호환용으로만 export되고 현재 경로에서 raise되지 않음.
- **의존성:** httpx, pydantic, pandas (psycopg 없음).

> ⚠ **README 주의(스테일).** `rdb-manager/README.md`는 옛 라이브러리 시절 설명(`from rdb_handler import get_connection`, PgBouncer 직결, `pip install -e packages/rdb-handler` 등)이 상당 부분 남아 있다. **현재 코드(`rdb_manager/*.py`)와 일부 불일치**하므로 README보다 소스와 본 문서를 우선하라. `_config.py`/`__init__.py`/`aio.py`가 권위 있는 현행이다. (개선 시 README를 HTTP SDK 기준으로 갱신 권장.)

### 3.3 rdb-handler (Kiwoom-Handler, PostgreSQL 핸들러 서버)

- **무엇:** PostgreSQL 게이트웨이 FastAPI 서버. 권위 있는 현행 위치는 **`Kiwoom-Handler/rdb-handler/`**.
- **두 부분:**
  1. `rdb-handler/rdb_handler/` — 저수준 연결 라이브러리(`PostgresConfig`, `get_connection`, `check_database_connection`, 예외). README상 "공개 query/DML API는 rdb-manager로 이전"되어 외부 소비자가 직접 import할 일은 드묾.
  2. `rdb-handler/service/` — **서버화된 FastAPI 게이트웨이**(이게 매니저가 호출하는 8700 서비스).
- **서버화 현황(핵심):** 2026-06-10 시점에 "라이브러리+PgBouncer"에서 **자체 풀 + PG 직결 서버**로 서버화되었고, 현재는 **`Kiwoom-Handler` main에 머지·배포 상태**다(아래 §6 참조). 이전의 PgBouncer(6432) 경유 라이브러리 모델은 **우회**되었다.
- **서버 핵심 사실**(`rdb-handler/service/app/`):
  - 포트 **8700** (`config.py`, `Dockerfile` `EXPOSE 8700`).
  - **자체 커넥션 풀:** `psycopg_pool.AsyncConnectionPool`로 PostgreSQL에 **직결(host=192.7.9.45, port 5432, PgBouncer 우회)**. (`app/core/pool.py`, `app/config.py`)
    - 풀 사이즈: `PG_POOL_MIN`(기본 2) / `PG_POOL_MAX`(기본 8) / `PG_POOL_TIMEOUT`(기본 10초). max+timeout이 백프레셔 역할(풀 초과 시 `OverloadError` → 503).
  - **엔드포인트:** `/v1/query/dataframe|all|all-dict|one`, `/v1/dml/execute|execute-many|insert|update`, `/health`(liveness), `/v1/check`(DB 연결확인). DML insert/update는 prefix 검증 없이 `execute_dml`로 위임(기존 rdb 동작 보존).
  - **응답:** pandas DataFrame을 서버에서 만들지 않고 columns+rows+dtypes JSON으로 내려보냄 → 클라이언트(rdb_manager)에서 DataFrame 복원.
  - **비밀번호 fail-fast:** `PG_PASSWORD` 미설정이면 기동 실패(`Settings` validator). 호스트/포트/DB/USER는 하드코딩 폴백 있음(host 192.7.9.45, port 5432, db kw_ai, user kiwoom).
- **PgBouncer 관계:** rdb-handler **서버는 5432 직결**이라 PgBouncer를 안 쓴다. 다만 옛 `rdb_handler/config.py`(라이브러리)와 `Kiwoom-Rag/packages` 잔재 README에는 여전히 "PgBouncer 192.7.9.45:6432 경유" 문구가 남아 있다 → **서버 경로에는 더 이상 해당 안 됨**. (인프라에 PgBouncer 컨테이너가 살아 있는지 여부는 `확인 필요`.)

---

## 4. 디렉터리 / 핵심 파일

### winus-manager (`Kiwoom-Manager/winus-manager/`)
```
pyproject.toml                 # name=kiwoom-winus-manager, deps: httpx/pydantic/pandas
README.md                      # 사용/잠금 정책
winus_manager/
├── __init__.py                # 동기 공개 API (모듈 함수 → SyncClient 위임)
├── aio.py                     # 비동기 공개 API (AsyncClient 위임)
├── _config.py                 # HANDLER_BASE_URL = http://192.7.9.45:8200 (단일 출처)
├── _sync_client.py            # SyncClient (httpx.Client, /v1/* 호출)
├── _client.py                 # AsyncClient (httpx.AsyncClient)
├── models.py                  # 응답 pydantic 모델(DataFrameData 등)
└── exceptions.py              # SDKError/HandlerError/HandlerConnectionError + 레거시 호환
tests/test_base_url_locked.py  # URL 잠금 검증
```

### rdb-manager (`Kiwoom-Manager/rdb-manager/`)
```
pyproject.toml                 # name=kiwoom-rdb-manager, deps: httpx/pydantic/pandas, pytest-asyncio
README.md                      # ⚠ 일부 스테일(라이브러리 시절 설명)
rdb_manager/
├── __init__.py                # 동기 공개 API
├── aio.py                     # 비동기 공개 API (configure 재호출 시 풀 누수 경고 로직 포함)
├── _config.py                 # HANDLER_BASE_URL = http://192.7.9.45:8700
├── _sync_client.py / _client.py
├── models.py / exceptions.py
tests/
├── test_base_url_locked.py    # URL 잠금(동기·비동기)
├── test_sync_client.py / test_async_client.py   # httpx.MockTransport로 핸들러 응답 모의
├── test_config_models.py
└── conftest.py
```

### rdb-handler (`Kiwoom-Handler/rdb-handler/`)
```
README.md                      # 저수준 라이브러리 안내(공개 API는 rdb-manager로 이전)
pyproject.toml
rdb_handler/                   # 저수준 연결 라이브러리
├── config.py                  # PostgresConfig (옛 라이브러리: PgBouncer 6432 잠금 — 서버는 안 씀)
├── connection.py / async_connection.py
└── exceptions.py
service/                       # ★ 매니저가 호출하는 8700 서버
├── Dockerfile                 # EXPOSE 8700, certs/ePrism.crt 복사
├── requirements.txt           # fastapi/uvicorn/psycopg/psycopg-pool/pandas/pydantic-settings
└── app/
    ├── main.py                # FastAPI 앱, lifespan에서 풀 open/close, request_id 미들웨어
    ├── config.py              # Settings: PG 직결 5432, 자체 풀, PG_PASSWORD fail-fast
    ├── core/pool.py           # AsyncConnectionPool (밸런서+백프레셔)
    ├── core/query.py          # SELECT/DML 실행 핵심
    ├── core/serialize.py      # 값 직렬화
    ├── core/errors.py         # RdbHandlerError/ConnectionPoolError/OverloadError
    ├── routers/query.py,dml.py,health.py
    └── models/requests.py,responses.py
tests/                         # 저수준 라이브러리 테스트(host/port 잠금, async)
service/tests/                 # 서버 테스트(test_config/test_routers/test_serialize)
```

> 참고용 winus-handler 서버(이 인수인계 범위 밖이지만 winus-manager의 짝): `Kiwoom-Handler/winus-handler/`. 문서 `README.md`/`WINUS.md`/`ASYNC.md`가 매우 상세하다.

---

## 5. 로컬 개발 / 테스트

### 매니저 설치(로컬, editable)
```bash
pip install -e Kiwoom-Manager/winus-manager
pip install -e Kiwoom-Manager/rdb-manager
```
> 매니저는 DB 드라이버 의존이 없어 별도 Oracle Client/psycopg 없이 설치된다.

### 매니저 테스트
```bash
# rdb-manager (pytest-asyncio, asyncio_mode=auto)
cd Kiwoom-Manager/rdb-manager && pytest         # tests/ : URL잠금 + MockTransport 단위테스트
# winus-manager
cd Kiwoom-Manager/winus-manager && pytest tests/test_base_url_locked.py
```
> 단위 테스트는 실제 핸들러/DB 없이 `httpx.MockTransport`로 핸들러 응답을 모의한다(라이브 DB 불필요). `dev` extra에 pytest 포함(`pip install -e ".[dev]"`).

### 핸들러 서버 로컬 실행 (Kiwoom-Handler)
```bash
cd Kiwoom-Handler/rdb-handler/service
# (uv 사용 시) uv sync; uv run uvicorn app.main:app --reload --port 8700
# PG_PASSWORD 미설정 시 기동 실패하므로 env 주입 필요
pytest tests   # service/tests : test_config/test_routers/test_serialize

# winus-handler 동일 패턴
cd Kiwoom-Handler/winus-handler/service
uv run uvicorn app.main:app --reload --port 8200
```

---

## 6. 배포 / 기동

- **빌드·배포 단위는 핸들러 서버**(매니저는 라이브러리라 pip 설치만 됨).
- **위치:** `Kiwoom-Handler` 레포 루트의 compose.
  - `docker-compose.yml`(빌드용): `winus-handler`(`8200:8200`), `rdb-handler`(`8700:8700`). build context는 레포 루트, dockerfile은 `*/service/Dockerfile`.
  - `docker-compose.prod.yml`(운영): GHCR 이미지 사용 — `ghcr.io/${IMAGE_OWNER:-kiwoomam}/kiwoom-rdb-handler:latest`, `...kiwoom-winus-handler:latest` 등.
- **rdb-handler env(compose):** `PG_HOST=192.7.9.45`, `PG_PORT=5432`, `PG_DATABASE=${PG_DATABASE:-kw_ai}`, `PG_USER=${PG_USER:-kiwoom}`, `PG_PASSWORD=${PG_PASSWORD}`.
- **현행 머지/배포 상태(검증됨):**
  - `Kiwoom-Handler` = `main`, rdb-handler/winus-handler 서버 소스 + compose 존재 → **서버화 머지됨**.
  - `Kiwoom-Manager` = `main`, rdb-manager HTTP SDK·winus-manager 머지됨(PR 머지 커밋 확인).
  - 즉 2026-06-10 시점엔 "로컬·미머지"였으나 **현재 시점(작성일 기준)에는 두 레포 모두 main에 반영**되어 있다. **운영 서버(192.7.9.45)에 실제로 최신 이미지가 떠 있는지는 `확인 필요`** (서버는 읽기전용 열람만, 배포는 사용자 담당 — §아래 운영 제약).
- **엔드포인트 192.7.9.45 잠금:** 매니저 URL은 `_config.py` 상수로 192.7.9.45:8200(winus)/8700(rdb)에 고정. 운영 IP/포트 변경 시 **`_config.py` 한 파일만** 고치고 재배포한다.
- **`[deploy]` 태그 규칙:** `Kiwoom-Rag` CI/CD는 커밋 메시지에 `[deploy]` 태그가 있어야 자동 빌드가 떨어진다(메모리 `reference_rag_deploy_tag`). **Kiwoom-Handler/Kiwoom-Manager에도 동일 규칙이 적용되는지는 `확인 필요`** (각 레포 `.github/workflows` 확인 권장).

### 운영 제약 (반드시 준수)
- 서버 **192.7.9.45**, 접속은 `ssh kiwoom`(WSL 포트 2222, 메모리 `reference_ssh_server`).
- **서버는 읽기전용 열람만.** 배포·기동·compose 편집은 **사용자가 수행**한다(메모리 `feedback_server_readonly_no_deploy`). 인수인계 담당자도 임의 배포 금지.

---

## 7. 환경변수 / 시크릿

### rdb-handler 서버 (PostgreSQL)
| 변수 | 기본/폴백 | 용도 |
|------|-----------|------|
| `PG_HOST` | `192.7.9.45` | PG 호스트(직결) |
| `PG_PORT` | `5432` | PG 포트(**PgBouncer 6432 아님**) |
| `PG_DATABASE` | `kw_ai` | DB명 |
| `PG_USER` | `kiwoom` | 접속 사용자 |
| `PG_PASSWORD` | (없음, 필수) | 비밀번호 — 미설정 시 기동 실패(fail-fast), 하드코딩 금지 |
| `PG_CONNECT_TIMEOUT` | `5` | 접속 타임아웃(초) |
| `PG_POOL_MIN`/`PG_POOL_MAX`/`PG_POOL_TIMEOUT` | `2`/`8`/`10.0` | 자체 풀 크기·대기 |

### winus-handler 서버 (Oracle)
| 변수 | 예/기본 | 용도 |
|------|---------|------|
| `ORACLE_HOST` | `192.7.x.x` | Oracle 호스트 |
| `ORACLE_PORT` | `1521` | 포트 |
| `ORACLE_SID` 또는 `ORACLE_SERVICE_NAME` | `WRI1` | DB 식별자(둘 중 하나) |
| `ORACLE_USER` / `ORACLE_PASSWORD` | - | 자격증명 |
| `USE_THICK_MODE` | `true` | Thick/Thin (운영 11.2는 Thick 필수) |
| `DB_POOL_MIN`/`DB_POOL_MAX` | `1`/`10` | 풀 |
| `MAX_CONCURRENT_REQUESTS`/`ACQUIRE_TIMEOUT`/`QUERY_TIMEOUT_MS`/`MAX_ROWS`/`MAX_QUERY_LENGTH` | 10/5.0/30000/10000/100000 | 과부하 방지 |

### 매니저(클라이언트) 측 env
- **매니저는 DB 자격증명을 받지 않는다.** 핸들러 URL이 코드에 고정이라 `WINUS_HANDLER_URL`/`RDB_HANDLER_URL`/`PG_HOST`/`PG_PORT` 같은 env는 **설정해도 무시**된다. `configure(timeout=...)`만 유효.

### 시크릿 / 인증서
- **certs:** 두 핸들러 Dockerfile 모두 `certs/ePrism.crt`를 복사해 `update-ca-certificates`(사내 프록시 SSL). 빌드 시 `certs/ePrism.crt`가 build context에 있어야 함.
- **GHCR/private repo 토큰:** Docker 빌드에서 private 의존성 설치 시 `GITHUB_TOKEN`/`x-access-token` 사용(rdb-handler README 예시). 멀티스테이지로 토큰 누출 방지.
- **.env CRLF 주의:** 서버 배포 시 `.env`가 **CRLF 줄바꿈이면 값 끝에 `\r`이 섞여** 접속 실패/이상 동작을 유발할 수 있음(메모리 `project_rdb_handler_library`의 "`.env` CRLF 주의"). LF로 정규화할 것. ⚠ 현행 레포 파일에서 이 주의를 직접 명시한 문구는 못 찾음 → **운영 메모리 기반, `확인 필요`.**

---

## 8. 자주 나는 장애와 대처

| 증상 | 원인 | 대처 |
|------|------|------|
| WINUS 로그인은 되는데 세션이 바로 끊김 / 트리거 차단 | logon trigger가 MODULE=`python*` 차단 | CMD를 `exec -a winus_handler ...`로 래핑(§3.1). 새 서비스도 동일 적용 |
| `DPY-3010 ... not supported ... in thin mode` | Oracle 11.2에 Thin으로 접속 | `USE_THICK_MODE=true` + Instant Client 설치(운영은 Docker 이미지에 19.21 포함) |
| Thick 초기화 실패 / `DPI`·라이브러리 못 찾음 | Instant Client 미설치/경로 불일치 | `ORACLE_CLIENT_PATH` 또는 `LD_LIBRARY_PATH` 확인. 컨테이너는 `/opt/oracle/instantclient_19_21` |
| 매니저가 엉뚱한 host로 붙으려 함 / env로 URL 못 바꿈 | URL이 코드에 잠겨 있음(의도된 동작) | `_config.py`의 `HANDLER_BASE_URL` 수정 후 재배포가 유일한 변경 경로 |
| rdb-handler 503 (OverloadError) | 자체 풀 max(8) 초과, `PG_POOL_TIMEOUT` 내 커넥션 못 얻음 | 부하/슬로우쿼리 점검, `PG_POOL_MAX` 상향 검토(PG `max_connections` 보호 고려) |
| rdb-handler 기동 즉시 종료 | `PG_PASSWORD` 미설정(fail-fast) | env 주입 |
| `connection refused` (rdb) | 192.7.9.45:5432 도달 불가 | PG 컨테이너 상태, `pg_isready -h 192.7.9.45 -p 5432`, 방화벽/VPN 확인. (구 문서의 6432는 서버 경로 아님) |
| `.env` 값 끝에 `\r` / 인증 실패 | `.env`가 CRLF | LF로 변환 |
| 소비처가 옛 핸들러 URL/포트로 붙음 | 엔드포인트 락 변경 후 소비처 미재배포 | 소비처(ETF/EM/pdfCollector 등) 재배포 필요(§9) |
| Oracle 형식 `:name`을 PG에, 또는 그 반대 | 바인드 변수 형식 혼동 | rdb=`%(name)s`, winus=`:name` |
| WINUS `SELECT *` 422 | WHERE 없는 `SELECT *`/`table.*` 차단(guard) | 컬럼 명시 또는 WHERE 추가 |

---

## 9. 미해결 / 진행 중

- **rdb-handler 서버화:** 2026-06-10 로컬 구현 → **현재 두 레포 main에 머지됨**(검증). 단 **운영 서버에 최신 이미지 실배포 여부는 `확인 필요`**(서버 읽기전용, 배포는 사용자).
- **winus → winus-manager 승격(엔드포인트 락/매니저화):** 메모리(`project_handler_endpoint_lock`, `project_etf_manager_migration_verified`)상 진행 중이었고, 현재 `Kiwoom-Manager`에 winus-manager가 main으로 존재한다. **모든 소비처가 winus-manager로 전환·재배포 완료됐는지는 `확인 필요`**(레포별로 옛 winus-handler 직접호출 잔재 점검 필요).
- **소비처 미정합 가능성:** 엔드포인트가 192.7.9.45로 잠긴 후에도 일부 소비처(ETF/EM/pdfCollector/ESG 등)는 **재배포가 안 됐을 수 있음**(메모리 `project_etf_index_post_handler_split` 등). 각 소비처 이미지가 최신 매니저를 물고 있는지 확인 필요.
- **`Kiwoom-Rag/packages/rdb-handler` 잔재 정리:** 작업 트리에 stale 디렉터리(.pyc/build만)가 남아 혼란을 줌. (Rag main엔 이미 없음 — 로컬 청소 대상.)
- **rdb-manager README 갱신:** §3.2 참고. 라이브러리 시절 설명을 HTTP SDK 기준으로 다시 써야 함.
- **PgBouncer 사용 여부:** rdb-handler 서버가 5432 직결로 바뀌어 PgBouncer를 안 씀. 인프라에 PgBouncer가 여전히 떠 있는지/다른 소비처가 쓰는지는 `확인 필요`.

---

## 10. 관련 레포 / 문서 링크

- **Kiwoom-Manager** (이 레포, 클라이언트 SDK 모음): `winus-manager/`, `rdb-manager/`, 그 외 file/llm/mdb/redis/vdb/api-manager.
- **Kiwoom-Handler** (핸들러 서버 모음, 권위 있는 현행 rdb-handler/winus-handler):
  - `Kiwoom-Handler/rdb-handler/` (서버 `service/`, README, Dockerfile, compose는 레포 루트 `docker-compose.yml`/`docker-compose.prod.yml`)
  - `Kiwoom-Handler/winus-handler/` — `README.md`, `WINUS.md`(개발 가이드·env·엔드포인트·과부하 보호), `ASYNC.md`(11.2/Thick/DPY-3010 상세)
- **설계/계획 문서(이 레포):**
  - `Kiwoom-Manager/docs/superpowers/specs/2026-06-11-rdb-manager-http-sdk-design.md`
  - `Kiwoom-Manager/docs/superpowers/plans/2026-06-11-rdb-manager-http-sdk.md`
- **스테일 잔재(참고만, 수정·신뢰 금지):** `Kiwoom-Rag/packages/rdb-handler/` (Rag main에서 제거됨, 로컬 .pyc/build만 남음).
- **소비처 레포:** `Kiwoom-ETF`, `Kiwoom-EM`, `Kiwoom-pdfCollector`, `Kiwoom-ESG`, `Kiwoom-Rag` 등 — 매니저를 import해 DB 접근.
- **운영 접속:** 서버 192.7.9.45, `ssh kiwoom`(포트 2222). 읽기전용 열람만, 배포는 사용자.
