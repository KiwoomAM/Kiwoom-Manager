# MDB Manager

MongoDB HTTP 클라이언트 패키지. MDB Handler Service(`packages/mdb-handler`)에 HTTP로
요청을 보내는 SDK다. 외부 에이전트가 GitHub에서 import해 쓰는 것을 전제로 한다
(서버화/docker-compose 대상이 아니라 라이브러리).

> 사용자 가이드·예시·API 표는 @README.md 참조. 이 문서는 코드 수정 시 알아야 할 것만 담는다.

## 편집 범위

- **이 패키지(`packages/mdb-manager/`)만 수정한다.** vdb-manager·redis-manager는 같은 팀
  영역이지만 그 외 `*-manager` / `*-handler`는 **다른 담당자 영역 — 절대 수정 금지.**
- 서버측 ACL(컬렉션별 허용 caller)은 `packages/mdb-handler/app/core/access_control.py`에
  있다. caller 정책 변경은 mdb-manager가 아니라 그쪽 소관.

## Architecture

```
[Agent 코드]  →  mdb_manager (httpx)  →  MDB Handler Service  →  MongoDB
```

- **sync**: `MongoDBManager` — Singleton (`__new__` 캐시, `reset()`으로 초기화)
- **async**: `AsyncMongoDBManager` — 비-Singleton, 명시적 `aclose()` lifecycle
- 두 진입점 모두 내부 `_client.py`/`_async_client.py`(httpx 래퍼)에 위임. 경로 빌더는
  `_paths.py` 공용. envelope 전용 진입점은 `get_agent_data()`/`get_agent_output()`.

## Conventions

- sync/async 두 파일은 의도적으로 평행 구조다. 한쪽(`_client.py`)을 고치면 다른 쪽
  (`_async_client.py`)도 같이 맞춘다 (`_default_headers` 등 중복은 의도된 것).
- 환경변수는 `os.environ`에서 직접 읽는다. **이 패키지는 `.env`를 자동 로드하지 않는다** —
  호출 애플리케이션이 `load_dotenv()`를 먼저 실행할 책임.

## Gotchas

- **SERVICE_NAME guard (envelope 진입 시점)**: `SERVICE_NAME`이 없어도
  `MongoDBManager()` / `AsyncMongoDBManager()` 생성과 blocks read/write는 정상 동작한다.
  caller 식별이 필수인 **envelope 진입점**(`get_agent_data()` / `get_agent_output()`)에서만,
  env `SERVICE_NAME`도 `with_caller()` caller도 없으면 `MDBConfigError`를 던진다. 구현:
  각 핸들러 파일의 `_require_caller()` + 클라이언트 `caller_identity` 프로퍼티. (과거에는 client
  생성 시점에 hard-fail했으나, read-only blocks 소비자 마찰과 load_dotenv import-order
  footgun을 없애기 위해 가드를 envelope 진입점으로 좁혔다.)
- **import/생성 시점 부작용 없음**: 모듈 import도, 핸들러 생성도 SERVICE_NAME을 요구하지
  않는다. 따라서 모듈-레벨 싱글톤(`mdb = MongoDBManager()`)을 `load_dotenv()` 전에 만들어도
  안전하다. SERVICE_NAME은 envelope를 실제로 호출하기 전까지만 떠 있으면 된다.
- **envelope-heavy 소비자의 부팅 검증(권장)**: 가드가 envelope 진입 시점으로 옮겨졌으므로,
  항상 envelope를 쓰는 장수 프로세스(FastAPI lifespan 등)에서 SERVICE_NAME 누락을 부팅 때
  빨리 발견하려면, lifespan에서 `os.environ.get("SERVICE_NAME")`를 assert하거나 envelope
  진입을 한 번 호출(예: `get_agent_data()`)해 검증하라. (강제 아님 — 선택적 fail-fast.)
- **Singleton(sync) poisoning 방지**: 생성 실패 시 `MongoDBManager.__init__`이 `_instance`를
  비운다. 그래서 SERVICE_NAME 누락으로 실패한 뒤 env를 채우고 다시 생성하면 정상 동작.
  (SERVICE_NAME은 더 이상 생성 실패 요인이 아니지만, 다른 생성 오류에 대한 일반 방어로 유지.)
- **접근 키 (Authorization)**: `_default_headers(auth_key)` 가 X-Caller-Service 와 함께
  `Authorization: Bearer <키>` 를 싣는다. 주지 않으면(None) `_config._ADMIN_KEY`(핸들러가 admin 으로 판정하는 키)다 —
  SDK 를 거친 호출이 모두 admin 이 되게 하는 장치다(레포 루트 CONTEXT.md 「auth_key」, Kiwoom-Handler
  `docs/adr/0001`). 바꾸는 자리는 두 매니저 생성자의 `auth_key` 뿐이고, 동기 싱글톤은 처음 생성한 값이 이긴다.
  핸들러가 거절하면 401·403 이 전용 예외 없이 `MDBHandlerError.status_code` 로 올라온다.
- **HANDLER URL은 고정 기본값**: `MDB_HANDLER_URL` 미설정 시 `http://192.7.9.45:8300`.
  사용자는 보통 설정하지 않는다. env override는 테스트/특수 환경용 escape hatch.
- **AsyncMongoDBManager는 Singleton 아님**: 명시적 `aclose()` 필요 (`async with` 또는 FastAPI
  lifespan). `aclose()` 후 호출 시 `RuntimeError`.
- **단일 SDK (과거 mdb-handler/sdk 승격)**: 이 패키지는 과거 `mdb-handler/sdk`를 승격(promote)한
  것으로, 이제 MDB Service의 **유일한 클라이언트 SDK**다. `mdb-handler/sdk`는 더 이상 존재하지
  않으며 "두 SDK 중복" 문제는 해소됐다. (mdb-manager에만 있는 SERVICE_NAME 가드는 의도된 동작.)
- **caller override (`with_caller`)**: 기본 caller는 env `SERVICE_NAME`. `mdb.with_caller(c)`는
  싱글톤을 변경하지 않는 불변 bound view를 반환하고, caller를 **per-request 헤더**로만 적용한다
  (`_CallerBoundClient`). 그래서 bound 호출이 기본 호출로 새지 않는다. 임베디드 라이브러리가
  호스트와 다른 정체성으로 owned 컬렉션에 접근할 때 사용. 별도 `service_name` 메서드 인자는
  없다 — inline `with_caller(c).method()`가 per-call 역할을 한다.

## Environment Variables

| 변수 | 기본값 | 설정 필요? |
|---|---|---|
| `SERVICE_NAME` | (없음) | envelope(`agent_data`/`agent_output`) 접근·owned 컬렉션 write의 caller 식별용. blocks read만 한다면 불필요. envelope 진입 시 누락이면 `MDBConfigError`. `with_caller()`로 대체 가능 |
| `MDB_HANDLER_URL` | `http://192.7.9.45:8300` | 불필요 (고정 기본값). 테스트/특수 환경만 override |

## Commands

```bash
# 로컬 (python3 + pytest 가용 시)
cd packages/mdb-manager && python3 -m pytest tests/ -v

# Docker 경로는 @README.md / mdb-handler/CLAUDE.md 참조
```
