# Caching

3계층 캐시로 중복 작업을 줄인다. 각 계층은 독립적으로 동작한다.

| 계층 | 저장소 | 막는 것 | 켜는 방법 |
|---|---|---|---|
| LLM 응답 캐시 | MongoDB (`agent_llm`) | 같은 입력으로 LLM 재호출 | 기본 동작 (`cache_mode=True`) |
| Files API URI 캐시 | Redis (TTL 47h) | 같은 파일을 Gemini Files API에 재업로드 | 파일이 **Files API 경로**로 처리될 때만 자동 ※ |
| Context Cache | Gemini 자체 (별도 비용) | 반복되는 큰 입력의 토큰 비용 | opt-in (`cached_content` 지정) |

> ※ 모든 파일이 Files API로 가는 건 아니다. **작은 파일은 inline 전송**되어 URI 캐시 대상이 아니다.
> 분기 기준: 비디오는 항상 Files API / 이미지·오디오 섞인 합계 ≤ 20MB → inline / PDF·텍스트만 합계 ≤ 50MB → inline / 그 외 Files API. 자세히는 [file-handling.md](file-handling.md).

이후 각 계층을 차례로 설명하고, 마지막에 캐시 키가 어떻게 만들어지는지 참고로 정리한다.

## LLM 응답 캐시 (MongoDB)

같은 입력으로 LLM을 다시 부르지 않도록 응답을 MongoDB에 저장하고 재사용한다.

### 전체 흐름

```
llm_input + files
  ↓
① 캐시 키 생성 (SHA-256 hex 64자) ─ 자세히는 §6 참고
  ↓
② 캐시 조회 (MongoDB `agent_llm` 컬렉션)
     HIT  → 저장된 응답 반환 (LLM 호출 없음)
     MISS ↓
③ 파일 처리 (inline 또는 Files API 업로드)
  ↓
④ LLM 호출
  ↓
⑤ 결과 저장 (MongoDB upsert)
  ↓
⑥ 반환
```

반환되는 `meta.source`로 어느 경로를 탔는지 확인할 수 있다 — `'cache'` (②에서 종료) 또는 `'live'` (③~⑥ 진행).

### `cache_mode` / `save_mode`

두 파라미터로 캐시 동작을 켜고 끈다. 4가지 조합이 가능하다.

| `cache_mode` | `save_mode` | 흐름 | 용도 |
| --- | --- | --- | --- |
| `True` (기본) | `True` (기본) | 캐시 조회 → HIT 반환 / MISS 시 LLM 호출 + 저장 | **일반 사용** — 캐시도 쓰고 신규 응답도 쌓음 |
| `True` | `False` | 캐시 조회 → HIT 반환 / MISS 시 LLM 호출만 (저장 X) | 기존 캐시는 활용하되 새 응답은 안 남기고 싶을 때 (실험) |
| `False` | `True` | 캐시 조회 안 함 → LLM 호출 → 저장 | **강제 갱신.** 같은 키의 기존 캐시는 **덮어쓰기**(upsert) |
| `False` | `False` | 키 생성도 안 함 → LLM 직접 호출 → 저장 X | 일회성/민감한 요청. 디버깅 |

**기본값**: 둘 다 `True`. 별도 지정 없이 호출하면 일반 사용 모드.

**두 플래그는 MDB 응답 캐시만 가른다.** `stored_files` 첨부는 네 조합 모두에서 동일하게 동작한다 —
업로드·inline 분류·Context Cache·404 자동 복구 전부 그대로다.

**`agent_llm` dedup 은 `save_mode=True` 일 때만 건다.** 대기자가 받아가는 것이 리더의 MDB 저장분이라,
저장하지 않는 조합(`True`/`False`·`False`/`False`)에서는 줄을 세우지 않고 각 요청이 곧장 LLM 을 부른다.

**`False`/`False` 로 파일을 반복해서 보낼 때의 비용.** 이 조합만 **응답 캐시 조회까지 없어**, 같은
파일로 같은 질문을 반복하면 매번 파일을 다시 받아 오고 입력 토큰도 매번 다시 문다. 파일 단위
dedup 은 Files API 경로에만 있고 inline 경로에는 없다(작은 파일은 대개 inline 이다).
**반복 호출이 예상되면 `cache_mode=True` 를 켜야 아껴진다** — `save_mode` 만 켜면(`False`/`True`)
조회를 안 하므로 순차 반복에서는 아무것도 아껴지지 않는다(위 표 3행).

**`False`/`False` 도 흔적을 남길 수 있다.** 파일 합계가 inline 한도를 넘거나
`timings={"context_cache_ttl": ...}` 를 지정하면 파일이 Files API 로 올라가고(48시간 잔존),
ttl 을 켰다면 **과금되는 Context Cache** 까지 생성된다. "아무것도 남기지 않는 일회성 호출" 로
쓰려면 파일 첨부 없이, ttl 없이 불러야 한다.

상세는 `docs/35`.

### MongoDB 저장 구조

| 설정 | 값 |
| --- | --- |
| 기본 database | `kw_ai` (`MONGODB_DATABASE` env 또는 호출 시 `database=` 파라미터로 변경 가능) |
| 기본 collection | `agent_llm` |

> 테스트/검증 시 `kw_ai_dev`를 사용한다 (`database="kw_ai_dev"`). 프로덕션 `kw_ai`에 직접 쓰지 않는다.

#### 문서 스키마

`_id`는 SHA-256 캐시 키(64자 hex). 같은 키로 재저장 시 upsert(전체 교체).

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `_id` | `str` | SHA-256 캐시 키 |
| `canonical_json` | `str` | 캐시 키 계산에 쓰인 정규화 JSON (디버깅용) |
| `result` | `dict` | `LLMResponse.model_dump()` |
| `metadata` | `dict` | `{"service_name": ..., "model": ...}` (`llm_input`에서 추출) |
| `created_at` | `str` | 저장 시각 (UTC ISO) |
| `requested_at` | `str` | LLM 요청 시각 (UTC ISO, LLM 호출 시에만 기록) |
| `expires_at` | `str` | `mdb_ttl` 설정 시에만 기록 (자동 삭제는 동작 안 함 — §2.4 참고) |
| `file_meta` | `list[dict]` | 파일 포함 요청 시. `FileInput` meta 리스트 (data 제외, file-manager가 부여한 `file_path` 포함) |

### TTL 주의

> **⚠️ `mdb_ttl` / `ttl_seconds`는 필드만 기록할 뿐, 자동 삭제는 동작하지 않는다.**
>
> 두 파라미터를 지정하면 문서의 `expires_at` 필드에 만료 시각이 기록되지만, MongoDB TTL 인덱스가 설정되어 있지 않아 만료된 문서가 자동으로 삭제되지 않는다. **현재 모든 문서는 영구 저장된다.**

캐시 무효화는 [§2.2](#cache_mode--save_mode)에서 다룬 `cache_mode=False`로 우회한다 (조회 안 함). 도큐먼트를 직접 지우는 API는 제공하지 않는다.

## Files API URI 캐시 (Redis)

같은 파일을 매번 Files API에 다시 업로드하지 않도록, Files API가 발급한 URI를 Redis에 캐싱한다.
파일이 Files API 경로로 처리될 때만 동작한다 (inline 경로는 해당 없음 — §1 각주 참고).

| 항목 | 값 |
| --- | --- |
| Redis 키 | `file_cache:files_api:{checksum}` |
| `checksum` 계산 | SHA-256(`file_name` + `file_mtime` + `file_size`) |
| TTL (정상) | Gemini가 반환한 만료 시각 기준 동적 계산 (1시간 버퍼 차감) |
| TTL (폴백) | **47시간** — Gemini Files API 보존 기간 48시간보다 1시간 짧게 |
| 동작 시점 | Files API 업로드 직전 — Redis HIT이면 업로드 생략 |

> **사용자가 직접 할 일은 없다.** 같은 파일을 다시 보내면 자동으로 캐시된 URI를 재사용한다.
> 단, `FileInput`의 `file_name` / `file_mtime` / `file_size` 중 하나라도 다르면 다른 checksum이 되어 캐시 미스 — Quick Start의 ⚠️ 박스와 같은 이유.

## Context Cache (Gemini)

Gemini가 자체 제공하는 캐시 기능. 같은 큰 입력(파일 + 시스템 프롬프트 + tools 등)이 반복될 때 입력 토큰을 **최대 90% 절감**한다.
**opt-in** — 명시적으로 켰을 때만 동작한다.

### 켜는 방법

`llm_request_async()` 호출 시 `timings={"context_cache_ttl": <초>}`을 지정하면 그 시간 동안 Gemini Context Cache를 유지한다. 지정하지 않으면 생성되지 않는다.

### 캐시 키

| 항목 | 값 |
| --- | --- |
| 캐시 키 | SHA-256(`file_checksums` + `model` + `system_instruction` + `tools` + `tool_config`) |
| Redis TTL | 기본 1시간 (개별 호출 시 `context_cache_ttl`로 덮어쓰기) |

> **5개 재료 중 하나라도 달라지면 캐시 미스.** 특히 `tools` / `tool_config`는 Gemini 쪽에도 함께 저장되므로, 같은 파일이라도 `tools`가 다르면 별도 캐시가 만들어진다 (오염된 HIT 방지).

### 비용 — 켤 가치가 있나

Context Cache는 **시간 단위 보관 비용**이 발생한다. 무조건 켜면 손해.

켤 가치가 있는 경우:
- **입력 토큰이 클 때** (예: 큰 PDF 첨부) — 절감 폭이 큼
- **TTL 동안 호출 빈도가 높을 때** (예: 같은 문서로 여러 질문) — 회수 빠름

켜면 안 되는 경우:
- 단발성 호출
- 입력이 작은 경우

> **⚠️ TTL을 무작정 길게 잡지 말 것.** 사용 패턴에 맞춰 (예: 한 세션 내 반복이면 30분~1시간, 일별 배치면 더 짧게).

## `fetch_by_cache_key` — 캐시 키로 결과 직접 조회

`done_check`으로 캐시 존재 여부만 확인하고 나중에 결과를 가져올 때 사용한다.
**`llm_input`을 다시 구성하지 않고 `cache_key`만으로 조회**한다 — 키 재계산(파일 checksum, canonicalize, SHA-256) 비용을 한 번 더 치르지 않는다.

### 호출 형태

```python
from llm_manager import done_check, fetch_by_cache_key, FileKeyingMeta

dc = await done_check(
    {"contents": "이 보고서 요약", "model": "gemini-2.5-flash"},
    files=[FileKeyingMeta(file_name="report.pdf", file_size=1024, file_mtime=1700000000)],
)

if dc["cache_hit"]:
    response, meta = await fetch_by_cache_key(dc["cache_key"])
    print(response.text)
    print(meta.source)   # "cache"
```

### 파라미터

| 파라미터 | 타입 | 설명 |
| --- | --- | --- |
| `cache_key` | `str` | `done_check`이 반환한 SHA-256 캐시 키 (64자 hex) |
| `database` | `str \| None` | DB 명. `None`이면 기본값(`kw_ai`) |

### 반환

`(LLMResponse, ResponseMeta)` 튜플. `meta.source == "cache"`, `meta.cache_hit == True`.

### 예외

| 상황 | 예외 |
| --- | --- |
| Service 연결/타임아웃 | `ServiceConnectionError` |
| 그 외 (캐시 미존재, 역직렬화 실패 등) | `ServiceError` |

자세히는 [error-handling.md](error-handling.md).

### 사용 예시

편집본을 따로 저장하면서 원본 LLM 응답과 연결해두고 싶을 때 — 편집본 측에 `cache_key`(64자)만 함께 보관해두면, 필요할 때 원본을 다시 가져올 수 있다.

## (참고) 캐시 키 상세

LLM 응답 캐시(§2)와 Context Cache(§4)의 키는 다음 5단계로 생성된다.
일반 사용자는 이 섹션을 볼 일이 없다 — 디버깅(왜 캐시가 안 맞지?)할 때만 필요.

### 5단계 흐름

```
llm_input + files
   ↓
1. 파일 checksum 주입
   ↓
2. subset 추출 (16개 필드만)
   ↓
3. 전처리 (None / 누락 → "")
   ↓
4. 정규화 (canonicalize)
   ↓
5. JSON 직렬화 → SHA-256 → 64자 hex
```

### 1. 파일 checksum 주입

파일이 있으면 각 파일의 `(file_name, file_size, file_mtime)` 3개 필드로 SHA-256 checksum을 계산해 `file_checksums` 키로 `llm_input`에 주입한다. 이후 캐시 키는 텍스트 입력 + 파일 조합 전체를 반영한다.

> 파일이 있으면 `cached_content`는 volatile로 처리되어 캐시 키에서 제외된다.

### 2. subset 추출

아래 16개 필드만 캐시 키 계산에 사용. 목록에 없는 필드는 무시.

| 필드 | 설명 |
|------|------|
| `contents` | 필수. 질문/프롬프트 |
| `system` | 시스템 프롬프트 |
| `history` | 이전 대화 이력 |
| `model` | 모델명 |
| `max_tokens` | 최대 출력 토큰 수 |
| `temperature` | 온도 |
| `cached_content` | Context Cache 이름 (파일 있으면 제외) |
| `service_name` | 서비스 식별자 |
| `seed` | 재현성 시드값 |
| `output` | 출력 형식 |
| `schema` | 출력 스키마 |
| `thinking` | 사고 수준 |
| `tools` | 도구 정의 |
| `tool_config` | 도구 설정 |
| `media_resolution` | 미디어 해상도 |
| `file_checksums` | 파일 checksum 리스트 (파일 있을 때만) |

### 3. 전처리

`build_llm_input_subset()`이 위 필드를 추출. 값이 `None`이거나 키가 없으면 `""`로 치환.

```python
llm_input = {"contents": "안녕", "temperature": None}

# 결과 subset:
{
    "contents": "안녕",   # 값 있음 → 그대로
    "temperature": "",    # None → "" 치환
    "model": "",          # 키 없음 → "" 치환
    ...                   # 나머지 13개 필드도 모두 "" 포함
}
```

### 4. 정규화 (canonicalize)

`canonicalize_llm_input()`이 각 필드 값을 재귀적으로 정규화.

| 타입 | 처리 |
|------|------|
| `dict` | 키 문자열 변환, `None` → `""`, volatile 키 제거 |
| `list` / `tuple` | 각 원소 재귀 정규화. **순서 유지** (contents·history는 순서가 의미 있음) |
| `float` | 소수점 4자리 반올림 (`0.30000001` → `0.3`) |
| `Decimal` | `normalize()` 후 문자열 변환 (`Decimal("1.200")` → `"1.2"`) |
| `datetime` / `date` | `isoformat()` 변환 |
| `bytes` / `bytearray` | hex 문자열 변환 (`b'\xff\x00'` → `"ff00"`) |
| `str` / `int` / `bool` / `None` | 그대로 사용 |

**volatile 키** — 요청마다 달라지는 메타데이터. 포함하면 같은 내용의 요청도 다른 캐시 키가 된다.

| volatile 키 | 성격 |
|-------------|------|
| `timestamp`, `ts`, `created_at`, `updated_at` | 시간 정보 |
| `request_id`, `trace_id` | 요청 추적용 고유 ID |

### 5. JSON 직렬화 → SHA-256

- dict 키 알파벳 정렬
- 구분자 공백 제거 (값 내부 공백은 유지)
- UTF-8 인코딩 후 SHA-256 → 64자리 hex 캐시 키

```python
# 값 안의 공백은 유지되므로 아래 둘은 다른 캐시 키:
"안녕하세요"   → 해시 A
"안녕하세요 "  → 해시 B  (뒤에 공백 하나)
```
