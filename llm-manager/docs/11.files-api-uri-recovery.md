# Files API URI 복구 — sha256_hash 기반 (백로그)

> **Status**: 보류 (Backlog). 현재 구현 계획 없음. 필요 시 재검토.

## 아이디어

Gemini Files API가 반환하는 `sha256_hash`(content hash)와 `list_files()`를
활용하여, Redis files_api 캐시가 유실/만료됐을 때 재업로드 없이 URI를 복구.

### 동작 시나리오

```
t=0h    업로드 → Gemini URI 받음, Redis 저장 (TTL 47h), sha256 포함
t=47h+  Redis 만료. Gemini 파일은 아직 48h 안이라 살아있음
t=~     요청 도착 → Redis MISS → (현재: 재업로드 / 제안: list_files + sha256 매칭 → URI 복구)
t=48h   Gemini도 만료 → 복구 불가, 재업로드 필수
```

**유효 구간**: Redis 만료(47h) ~ Gemini 만료(48h) = **1시간 오버랩**

## 효과

- **절감**: 해당 1h 창에서 발생한 요청의 재업로드 회피
- **대상**: >20MB Files API 경로 파일 (inline 파일은 Redis 캐싱 안 함)
- **빈도**: 단일 파일 기준 1/47 ≈ 약 2% 요청

## 구현 요건

### 1. llm-handler SDK에 `list_files()` 노출 (미제공)

```python
# llm_handler / llm_handler.aio
def list_files(page_size: int = 100, page_token: str | None = None) -> tuple[list[FileRef], str | None]: ...
```

- Gemini `client.files.list()` 래핑
- 페이지네이션 필수 (운영 환경에서 수백~수천 개 누적 가능)

### 2. llm-handler SDK의 `FileRef`에 `sha256_hash` 필드 추가

현재 `FileRef` 필드: `name, uri, mime_type, state`
추가 필요: `sha256_hash: str | None`

### 3. llm-request 측 변경

**cache_store.py**
- `save_files_api` doc에 `sha256_hash` 필드 추가 저장
- 복구 로직 신규 함수: `recover_uris_by_sha256(sha256: str) -> list[str] | None`
  - `list_files()` 페이지 순회 + sha256 매칭

**resolver.py `_upload_and_cache_file`** (Redis MISS 분기 확장)
- 현재: Redis MISS → 바로 재업로드
- 신규: Redis MISS → (로컬 bytes에서 sha256 계산) → `recover_uris_by_sha256` → HIT면 URI 재사용 + Redis 재저장 / MISS면 재업로드

### 4. 로컬 bytes에서 sha256 계산

현재 llm-request는 file-manager meta 기반으로만 식별. 복구를 위해선 업로드 직전 **content sha256**도 계산해야 함 (file-manager에서 받은 bytes를 hashlib.sha256으로).

## 한계 / 부작용

- **복잡도 증가**: 2가지 식별 체계 (meta checksum + content sha256) 공존
- **list_files 오버헤드**: 페이지 많을 시 복구 1회가 수 초 소요 가능 (재업로드 대비 이득 애매해질 수 있음)
- **메모리 sha256 계산**: >20MB 파일 해싱 비용
- **좁은 유효 구간**: 47~48h의 1시간 창만 활용 가능

## 대안 — TTL overlap 확대

**훨씬 간단한 해법**: Redis TTL을 더 낮춰 overlap을 키우는 것.

- 현재: Redis 47h → Gemini 48h → overlap 1h
- 변경: Redis 45h → Gemini 48h → overlap 3h → 재업로드 창구 3배
- 구현 방법: `cache_store.FILES_API_TTL = 162000  # 45h` 또는 `compute_files_api_ttl`의 buffer를 1h→3h로

sha256 복구 대비 **코드 변경 1줄 / 복잡도 0 / 효과 3배**. 이 방향이 훨씬 경제적.

## 판단

| 항목 | sha256 복구 | TTL overlap 확대 |
|---|---|---|
| 구현 범위 | llm-handler + llm-request 양쪽 | llm-request 1줄 |
| 복잡도 | 높음 | 매우 낮음 |
| 1h 구간 절감 | 가능 | 동일 |
| n시간 구간 확장 | 가능 (limit: 48h) | 가능 (limit: 48h) |

**결론**: 현 시점 sha256 복구는 과도. TTL overlap 확대가 실질적. 단, Redis 메모리 eviction으로 TTL 만료 전 키가 증발하는 상황이 잦아지면 sha256 복구 재검토 가치 있음.

## 재검토 트리거

- Redis eviction 빈번 → 관측 지표 필요
- Gemini TTL이 대폭 확대됨 (예: 72h+) → overlap 확대만으로 충분해짐
- `list_files` 성능 개선 (페이지 토큰 검색 지원 등) → sha256 매칭 비용 감소
