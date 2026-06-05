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

- **sync**: `MongoDBHandler` — Singleton (`__new__` 캐시, `reset()`으로 초기화)
- **async**: `AsyncMongoDBHandler` — 비-Singleton, 명시적 `aclose()` lifecycle
- 두 진입점 모두 내부 `_client.py`/`_async_client.py`(httpx 래퍼)에 위임. 경로 빌더는
  `_paths.py` 공용. envelope 전용 진입점은 `get_agent_data()`/`get_agent_output()`.

## Conventions

- sync/async 두 파일은 의도적으로 평행 구조다. 한쪽(`_client.py`)을 고치면 다른 쪽
  (`_async_client.py`)도 같이 맞춘다 (`_default_headers` 등 중복은 의도된 것).
- 환경변수는 `os.environ`에서 직접 읽는다. **이 패키지는 `.env`를 자동 로드하지 않는다** —
  호출 애플리케이션이 `load_dotenv()`를 먼저 실행할 책임.

## Gotchas

- **SERVICE_NAME hard-fail (생성 시점)**: `SERVICE_NAME` env가 없으면 `MongoDBHandler()` /
  `AsyncMongoDBHandler()` (정확히는 내부 client 생성) 시점에 즉시 `MDBConfigError`를 던진다.
  서버측 400을 첫 요청까지 기다리지 않는 fail-fast다. 구현: `_client.py`/`_async_client.py`의
  `_default_headers()`. read-only 소비자도 SERVICE_NAME이 필요하다 (없으면 아무 값이라도 지정).
- **import 시점 부작용 없음, 생성 시점에 터짐**: 모듈 import 자체는 안전하지만, 핸들러를
  생성하는 순간 SERVICE_NAME 가드가 작동한다. 따라서 `load_dotenv()`는 **핸들러 생성보다
  먼저** 실행돼야 한다. 에이전트가 모듈 로드 시점에 핸들러를 만든다면(예: 모듈-레벨 싱글톤),
  그 import 전에 env가 떠 있어야 한다.
- **Singleton(sync) poisoning 방지**: 생성 실패 시 `MongoDBHandler.__init__`이 `_instance`를
  비운다. 그래서 SERVICE_NAME 누락으로 실패한 뒤 env를 채우고 다시 생성하면 정상 동작.
- **HANDLER URL은 고정 기본값**: `MDB_HANDLER_URL` 미설정 시 `http://192.7.9.45:8300`.
  사용자는 보통 설정하지 않는다. env override는 테스트/특수 환경용 escape hatch.
- **AsyncMongoDBHandler는 Singleton 아님**: 명시적 `aclose()` 필요 (`async with` 또는 FastAPI
  lifespan). `aclose()` 후 호출 시 `RuntimeError`.
- **mdb-handler/sdk와의 관계**: 이 패키지는 `packages/mdb-handler/sdk`와 같은 계보의 클라이언트지만
  **독립적으로 관리되며 자동 동기화되지 않는다.** 예: SERVICE_NAME 가드는 mdb-manager에만 있다.
  한쪽 변경이 다른쪽에 자동 반영되지 않으니 의도한 쪽만 고친다.

## Environment Variables

| 변수 | 기본값 | 설정 필요? |
|---|---|---|
| `SERVICE_NAME` | (없음) | **필수** — write/envelope 작업의 caller 식별. 누락 시 생성 시점 `MDBConfigError` |
| `MDB_HANDLER_URL` | `http://192.7.9.45:8300` | 불필요 (고정 기본값). 테스트/특수 환경만 override |

## Commands

```bash
# 로컬 (python3 + pytest 가용 시)
cd packages/mdb-manager && python3 -m pytest tests/ -v

# Docker 경로는 @README.md / mdb-handler/CLAUDE.md 참조
```
