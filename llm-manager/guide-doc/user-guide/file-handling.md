# File Handling

`llm_request_async`에 `files=`로 전달된 `FileInput` 리스트가 어떻게 LLM으로 전달되는지 다룬다.

## 처리 흐름

Service 내부에서 4단계로 처리된다.

```
FileInput 리스트
  ↓
① 유효성 검사
   MIME 화이트리스트 / 단일 파일 2GB 한도 — 위반 시 fail-fast (§3)
  ↓
② file-manager에 파일 등록
   Service가 외부 file-manager 서비스로 bytes 전송, file_path 부여
  ↓
③ 파일 분류
   각 파일의 크기·타입 보고 inline_pool / files_api_pool로 분리 (§4)
  ↓
④ 처리 방식 결정
   inline    → bytes를 그대로 LLM 페이로드에 포함
   files_api → URI 캐시 확인 → 없으면 업로드 → URI 사용 (캐시 자세히는 caching.md §3)
   context_cache_ttl 지정 시 → Context Cache 생성 (caching.md §4)
```

이후 섹션에서 각 단계의 규칙을 자세히 다룬다.

## FileInput 입력 규칙

`FileInput`은 6개 필드로 구성된다. 모든 필드 필수.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `file_name` | `str` | 파일명 (확장자 포함) |
| `file_size` | `int` | 파일 크기 (bytes) |
| `file_mtime` | `int` | 파일 수정 시각 (Unix timestamp, 초 단위) |
| `mime_type` | `str` | MIME 타입 (예: `application/pdf`) |
| `extension` | `str` | 확장자 (점 없이, 예: `pdf`) |
| `data` | `bytes` | 파일 바이트 |

### 캐시 키에 영향을 주는 필드

`file_name`, `file_size`, `file_mtime` **세 필드의 SHA-256 체크섬**이 캐시 키 + Files API URI 캐시 키의 일부가 된다.
**세 값 중 하나라도** 실제 파일과 다르거나 호출마다 변하면 **캐시 미스가 반복**된다 — `file_name`도 포함이므로 같은 내용의 파일이라도 이름이 다르면 다른 체크섬이다.

> **⚠️ `file_name` / `file_size` / `file_mtime`은 실제 파일에서 추출한 값과 반드시 일치해야 한다.**
> 자세한 캐시 키 계산은 [caching.md §6](caching.md#참고-캐시-키-상세) 참고.

### 브라우저 업로드 파일은 `file_mtime` 추출에 주의

로컬 경로가 없는 경우(프론트엔드에서 업로드되는 에이전트 등) Python `Path.stat().st_mtime`을 쓸 수 없다. **프론트엔드에서 `File.lastModified` 값을 백엔드로 함께 전달**해야 한다. JavaScript `File.lastModified`는 밀리초 단위이므로 초 단위로 변환:

```js
// frontend (JS)
const mtime = Math.floor(file.lastModified / 1000);
// 업로드 폼 / JSON에 file_mtime 필드로 같이 전달
```

서버 수신 시점의 현재 시각(`time.time()` 등)을 `file_mtime`으로 대신 쓰면, 동일 파일을 다시 업로드할 때마다 `file_mtime`이 달라져 **cache miss가 반복**된다.

## 유효성 검사

처리 흐름 ①. 진입 즉시 fail-fast로 차단 — 아래 두 조건 중 하나라도 위반하면 에러 반환.

### 지원 MIME 타입 화이트리스트

| 분류 | MIME 타입 |
| --- | --- |
| 이미지 | `image/png`, `image/jpeg`, `image/webp`, `image/heic`, `image/heif` |
| 오디오 | `audio/wav`, `audio/mp3`, `audio/aiff`, `audio/aac`, `audio/ogg`, `audio/flac` |
| 문서 | `application/pdf`, `text/plain`, `text/html`, `text/csv`, `text/markdown`, `text/xml`, `application/json`, `application/xml` |
| 비디오 | `video/mp4`, `video/mpeg`, `video/mov`, `video/avi`, `video/x-flv`, `video/webm`, `video/3gpp` |

목록 외 MIME 타입은 거부된다.

### 단일 파일 크기 한도

**2GB** 초과 시 거부 — inline / Files API 어느 경로로도 처리 불가.

## inline vs Files API — 분기 기준

처리 흐름 ③④. 각 파일을 inline_pool / files_api_pool로 나누고, pool별로 처리한다.

### 분기 기준

| 조건 | 처리 방식 |
| --- | --- |
| 비디오 | 항상 **Files API** (inline 불가) |
| 인라인 풀 합계 ≤ 20MB (이미지·오디오 포함 시) | **inline** |
| 인라인 풀 합계 ≤ 50MB (PDF·텍스트만) | **inline** |
| 위 한도 초과 | **Files API** |

이미지나 오디오가 하나라도 섞이면 inline 합계 한도가 20MB로 낮아진다. 작은 파일부터 한도 내에 채우고 초과분은 Files API로 넘긴다.

> PDF가 Files API 경로이고 50MB 초과이면 자동 분할 후 업로드한다 ([§5](#pdf-자동-분할-50mb-초과) 참고).

### 각 경로의 동작

**inline 경로**
- Service가 bytes를 그대로 LLM 페이로드에 포함
- Redis URI 캐시 미적용 (Files API 경로 아니므로)

**Files API 경로**
- Service가 file-manager가 보관한 파일을 Gemini Files API에 업로드
- 업로드 전 Redis URI 캐시 조회 → HIT이면 업로드 생략 ([caching.md §3](caching.md#files-api-uri-캐시-redis))
- Gemini가 발급한 URI를 LLM 페이로드에 포함

## PDF 자동 분할 (50MB 초과)

Files API 경로의 PDF가 50MB를 초과하면 자동으로 분할 후 병렬 업로드한다 (Gemini Files API의 단일 파일 한도 대응).

| 단계 | 내용 |
|---|---|
| 1 | 페이지별 예상 크기 계산 (이미지 xref + 텍스트 콘텐츠 기준) |
| 2 | 50MB 이하가 되도록 페이지 그룹핑 |
| 3 | 그룹별 저장 → 그룹이 여전히 크기 초과면 **재귀 재분할** (최대 3단계) |
| 4 | 인접 파트를 45MB 상한 내에서 병합 — 너무 작은 파트가 많아지지 않도록 |

분할된 각 파트는 병렬 업로드되며, 일반적으로 LLM이 받는 파트들은 원본 PDF의 연속된 페이지 범위다. 사용자가 분할 동작을 켜고/끄거나 임계값을 바꾸는 옵션은 현재 없다.

## (참고) Files API URI 캐시 / Context Cache

파일 처리 자체는 위 §1~§5에서 끝나고, 캐시는 별도 챕터에서 다룬다.

- **Files API URI 캐시 (Redis)**: 같은 파일을 다시 보냈을 때 업로드 생략 — [caching.md §3](caching.md#files-api-uri-캐시-redis)
- **Context Cache (Gemini)**: 반복되는 큰 입력의 토큰 비용 절감 — [caching.md §4](caching.md#context-cache-gemini)

두 캐시 모두 **Files API 경로일 때만** 의미가 있다 (inline 경로는 해당 없음).
