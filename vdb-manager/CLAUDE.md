# VDB Manager

Vector DB HTTP 클라이언트 패키지. VDB Handler Service(`packages/vdb-handler`)에 HTTP로
요청을 보내는 SDK다. Dense/Sparse 임베딩, Hybrid 검색, ColBERT 리랭킹, 컬렉션 CRUD를
서버측에 위임한다. 외부 에이전트가 GitHub에서 import해 쓰는 것을 전제로 한다.

> 사용자 가이드·예시는 @README.md 참조. 이 문서는 코드 수정 시 알아야 할 것만 담는다.
> 낱말이 무엇을 가리키는지는 @CONTEXT.md, 되돌리기 비싼 결정은 `docs/adr/` 에 있다.

## 편집 범위

- **이 패키지(레포 최상위 `vdb-manager/`)만 수정한다.** mdb-manager·redis-manager는 같은 팀
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
- **payload 쓰기는 존재를 판정하지 않는다**: 서버가 `has_id` 조건으로 보내 없는 id 를
  고르지 않는다. 예전에는 Qdrant 가 404 를 내면서 있는 포인트의 변경은 이미 끝내 놓아,
  호출자가 아무 일도 없었다고 읽으면서 실제로는 일부가 바뀌어 있었다. 존재 판정을 쓰기에
  다시 얹지 말 것 — 그 일은 `get_points`·`count_points` 가 한다.
- **payload 쓰기 응답 필드는 `requested`**: `count` 가 아니다. 셀 수 없는 값에 센 값처럼
  읽히는 이름을 붙이지 않는다. `/count` 응답의 `count` 와 `/upsert_points` 의 `count` 는
  그대로이며 서로 다른 값이다.
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
- **`with_payload`는 항목 고르기, `payload_filter`는 포인트 고르기다**: 앞은 고른 포인트에서
  어떤 payload 항목을 받을지(SELECT)이고 뒤는 어떤 포인트를 고를지(WHERE)다. 이름이 비슷해
  뒤바뀌기 쉬운데 축이 달라 조용히 다른 결과가 나온다. 용어 정의는 @CONTEXT.md.
- **모르는 payload 항목명은 막지 않는다 — 막을 대상이 없다**: Qdrant payload 는 포인트마다
  스키마가 없어 "항목명이 틀렸다"와 "그 키를 가진 포인트가 없다"가 같은 상태다. SDK 도 서버도
  판정하지 않으며 빈 payload 가 그대로 온다. 빈 결과가 정상인지는 컬렉션의 도메인을 아는
  호출자만 안다 (`KiwoomAM/Kiwoom-Handler` 의 ADR 0004). SDK 가 대신 판정하려 들지 말 것.
- **빈 목록은 막고 항목명 형식은 판정하지 않는다**: `with_payload=[]` 는 왕복 전 `ValueError`
  이지만 `["  "]` 는 그대로 보낸다. `_reject_if_empty` 가 보는 것은 언제나 "컨테이너가
  비었는가" 이고, 원소 단위 검사는 형식 판정이라 서버 규칙을 흉내 내기 시작하는 자리다.
- **순회의 끝은 `next_offset is None` 이다**: `0` 은 실재하는 포인트 id 라 falsy 로 판정하면
  첫 페이지에서 멈춘다. `iter_points` 가 이 판정을 삼키므로 전수 순회는 그것을 쓴다.
- **게이트웨이 응답은 봉투째 돌려준다**: `get_points`·`scroll_points` 는 서버 응답을 그대로
  준다. 알맹이만 꺼내면 서버가 그 봉투에 새 정보를 실을 때 SDK 가 조용히 가린다. 검색 3종이
  아직 알맹이만 주는 것은 소비자와 함께 움직여야 해서이며 통일은 #118 이다 (`docs/adr/0001`).
- **바로가기 시그니처는 테스트가 소스에서 세어 와 대조한다**: `tests/test_shortcut_signatures.py`
  가 `VDBManager` 의 위임 관계를 ast 로 읽어 원본과 인자를 비교한다. 새 바로가기를 만들면
  등록하지 않아도 검사 대상이 되고, 인자를 `**kwargs` 로 뭉개면 실패한다 — 그렇게 하면 IDE
  자동완성과 타입 검사가 사라지고 오타가 SDK 안쪽에서 터진다 (#114).
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
