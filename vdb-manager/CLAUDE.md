# VDB Manager

Vector DB HTTP 클라이언트 패키지. VDB Handler Service(`packages/vdb-handler`)에 HTTP로
요청을 보내는 SDK다. Dense/Sparse 임베딩, Hybrid 검색, ColBERT 리랭킹, 컬렉션 CRUD를
서버측에 위임한다. 외부 에이전트가 GitHub에서 import해 쓰는 것을 전제로 한다.

> 사용자 가이드·예시는 @README.md 참조. 이 문서는 코드 수정 시 알아야 할 것만 담는다.

## 편집 범위

- **이 패키지(`packages/vdb-manager/`)만 수정한다.** mdb-manager·redis-manager는 같은 팀
  영역이지만 그 외 `*-manager` / `*-handler`는 **다른 담당자 영역 — 절대 수정 금지.**
- 임베딩 모델 로드·SPLADE/ColBERT 추론·Qdrant 직접 접근은 **서버측(`packages/vdb-handler`)**
  소관이다. 이 패키지는 HTTP 클라이언트일 뿐 모델을 직접 로드하지 않는다.

## Architecture

```
[Agent 코드]  →  vdb_manager (httpx, sync)  →  VDB Handler Service  →  Qdrant / SPLADE / ColBERT
```

- `VDBManager` — Singleton 통합 진입점. 하위 클라이언트를 속성으로 노출:
  - `.dense` (DenseClient) — Gemini Dense 임베딩/검색
  - `.sparse` (SparseClient) — SPLADE sparse 임베딩/검색
  - `.hybrid` (HybridClient) — Dense+Sparse RRF 하이브리드 검색
  - `.collection` (CollectionClient) — 컬렉션 생성/upsert/조회 CRUD
  - `.reranker` (RerankerClient) — ColBERT/Gemini 리랭킹
- 내부 `_client.py`의 `SyncClient`(httpx.Client)에 위임. `get`·`post`·`patch`·`put`·
  `delete` 다섯을 노출하며 각각 얇은 래퍼 + 오류 번역만 한다.

## Conventions

- **sync only.** async 인터페이스가 없다 (mdb-manager와 다름). async 컨텍스트에서 호출하면
  이벤트 루프를 블록하므로, 필요 시 `asyncio.to_thread` 등으로 감싼다.
- 환경변수는 `os.environ`에서 직접 읽는다. **이 패키지는 `.env`를 자동 로드하지 않는다** —
  호출 애플리케이션이 `load_dotenv()`를 먼저 실행할 책임.

## Gotchas

- **sync only → 이벤트 루프 블록 주의**: FastAPI/LangGraph 등 async 환경에서 직접 호출 시
  루프가 막힌다. 별도 스레드로 오프로딩 권장.
- **HANDLER URL은 고정 기본값**: `VDB_HANDLER_URL` 미설정 시 `http://192.7.9.45:8500`.
  사용자는 보통 설정하지 않는다. env override는 테스트/특수 환경용 escape hatch.
- **SERVICE_NAME 불필요**: mdb-manager와 달리 vdb-manager는 caller 식별(write ACL)을 쓰지
  않는다. 설정할 env가 `VDB_HANDLER_URL` 하나뿐이고 그것도 기본값이 있어, 일반 사용에서는
  **아무것도 설정하지 않아도 된다.**
- **서버 기동이 느리다**: VDB Handler Service는 SPLADE/ColBERT 모델 로드로 기동이 ~120s
  걸린다. 클라이언트 타임아웃/헬스체크 시 고려.
- **payload 병합과 교체는 HTTP 메서드로만 구분된다**: `set_payload`(PATCH)와 `overwrite_payload`
  (PUT)는 경로와 body 모양이 같다. 테스트 더블이 메서드를 기록하지 않으면 둘이 뒤바뀌어도
  통과하므로, `tests/conftest.py`의 `FakeClient`는 메서드를 반드시 캡처한다.
- **`clear_payload`는 `delete_payload_keys`의 인자 생략형이 아니다**: 서버가 경로를 나눠 둔
  이유가 "인자를 빠뜨렸다고 전체가 지워지는 일"을 막기 위해서다. 한쪽으로 합치지 말 것.
- **SDK는 빈 것만 막고 형식은 판정하지 않는다**: `_reject_if_empty`가 보는 것은 빈 `ids`·
  `payload`·`keys`·`field`·`schema`뿐이다. u64 폭이나 UUID 모양까지 흉내 내면 서버가 규칙을 넓힐 때
  SDK만 옛 규칙으로 정당한 요청을 막는다. 서버가 판정하지 **않는** 사실(슬래시 한계)만 SDK가
  경고로 전한다.
- **index 조회는 컬렉션 정보와 별개다**: Qdrant에 index 전용 조회 경로가 없어 서버가
  `get_collection()`에서 투영하지만 그것은 서버의 구현 세부다. `list_payload_indexes`를
  `info()`에 합치면 한 메서드가 두 엔드포인트를 치는 얕은 합성이 된다.
- **슬래시가 든 필드명의 index 는 삭제 불가**: 벤더 클라이언트가 필드명을 URL 경로에
  percent-encoding 없이 넣는다. `create_payload_index`의 경고는 `KiwoomAM/Kiwoom-Handler#74`가
  고쳐지면 함께 걷어낼 임시 조치다.
- **이 레포의 `#74`는 다른 이슈다**: 이슈 번호를 적을 때 레포를 함께 쓴다. `Kiwoom-Manager#74`는
  llm-manager 건이라, `#74`로만 적으면 GitHub이 엉뚱한 이슈로 링크를 건다.
- **Singleton**: `VDBManager()`는 항상 같은 인스턴스. 재초기화는 `VDBManager.reset()` 또는 프로세스 재시작.

## Environment Variables

| 변수 | 기본값 | 설정 필요? |
|---|---|---|
| `VDB_HANDLER_URL` | `http://192.7.9.45:8500` | 불필요 (고정 기본값). 테스트/특수 환경만 override |

## Commands

- 테스트는 `tests/` 에 있고 `uv run --with pytest pytest` 로 돌린다 (dev 의존성이 선언돼
  있지 않아 `--with pytest` 가 필요하다). Service 를 띄우지 않고 돌아간다 — 손수 만든
  `FakeClient` 를 서브클라이언트에 주입해 **어느 경로로 어떤 HTTP 메서드와 body 가 갔는지**를
  검증하는 것이 이 패키지의 관례다. 공용 더블과 `make_collection` fixture 는
  `tests/conftest.py` 에 있다 — `tests/test_collection_points.py` 에도 `patch`·`put` 을
  모르는 오래된 사본이 있으며, 통합은 클라이언트 경계를 Protocol 로 세우는 #108 소관이다.
  실제 연동 확인이 필요하면 Service 를 띄우고 `VDBManager().health_check()`.
- 클라이언트 동작을 바꾸면 회귀 방지를 위해 최소 단위 테스트 추가를 우선 고려한다.
