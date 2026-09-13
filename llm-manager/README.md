# 1. 개요

---

Agent에서 LLM을 호출할 때 **중복 호출 없이 이전 응답을 재사용**하는 캐싱 레이어.
같은 질문 + 같은 파일이면 LLM을 다시 부르지 않고 MongoDB에서 즉시 반환한다.

**캐시 3계층**으로 낭비를 최소화한다:
- **LLM 응답 캐시 (MongoDB)**: 동일 입력 → 저장된 응답 반환
- **Files API URI 캐시 (Redis)**: 같은 파일 재업로드 방지 (47h)
- **Context Cache (Gemini)**: 반복 사용 파일의 입력 토큰 90% 절감 (opt-in)

파일 처리는 Google 공식 문서 기준으로 구현되어 있어, **사용자는 파일을 넘기는 방법만 알면 된다.**
크기·타입에 따른 inline / Files API 전환, PDF 50MB 초과 시 자동 분할은 내부에서 처리한다.

- **Quickstart**: `quickstarts/llm_manager/quickstart_sdk.ipynb` — 간단한 예시 실행 가능한 코드

# 2. 핵심 특징

---

| 항목 | 설명 |
| --- | --- |
| 패키지명 | `kiwoom-llm-manager` |
| 버전 | `0.3.0` |
| Python | **`>=3.11`** |
| 구성 | **순수 SDK manager** (별도 서버 없음). agent 프로세스 안에서 직접 실행 |
| 의존성 | `kiwoom-mdb-manager`, `kiwoom-redis-manager`, `kiwoom-file-manager`, `httpx`, `pydantic`, `pydantic-settings`, `filetype`, `PyMuPDF` (llm-handler 클라이언트 SDK는 흡수 — 외부 의존 아님) |
| 동작 방식 | SDK가 mdb·redis·file manager 와 llm-handler 를 **직접 조합 호출**. (구 0.2.0의 FastAPI Service(8101) 레이어는 제거됨 — 근거: [docs/23](docs/23.manager-sdk-refactoring-plan.md)) |

# 3. 디렉터리 구조

```
llm-manager/
├── llm_manager/                  # 단일 SDK manager 패키지
│   ├── __init__.py               # 공개 API (llm_request_async + 큐레이션한 handler primitive)
│   ├── aio.py                    # 비동기 공개 표면 (call/stream/embed/session — handler_client.aio 큐레이션)
│   ├── models.py                 # StoredFile, ResponseMeta, FileKeyingMeta, Expire* (+ 내부 FileInput)
│   ├── exceptions.py             # 공개 예외 (LLMManagerError 계층)
│   └── _internal/                # manager 로직 (외부 사용 X)
│       ├── handler_client/       # 흡수한 handler HTTP 클라이언트 (구 llm-handler/sdk)
│       │                         #   call/stream/embed/session, _client, models, exceptions, aio
│       ├── runner.py             # LLM 호출 (캐시 조회/저장 + standby dedup)
│       ├── keying.py             # 캐시 키 생성
│       ├── done_check.py         # 캐시 조회
│       ├── store.py              # MongoDB 캐시 저장/조회
│       ├── meta.py               # 저장용 메타 생성
│       ├── gemini_errors.py      # Gemini 404 / too-small 판별
│       ├── context_cache_lifecycle.py  # context cache expire
│       ├── stored_file_resolver.py     # stored_files → FileInput (lazy fetch)
│       ├── _config.py            # 하드코딩 handler URL + lazy 초기화
│       └── file_processing/
│           ├── resolver.py       # 파일 분류 (inline / Files API / context cache)
│           ├── uploader.py       # Files API 업로드 + context cache 생성
│           ├── cache_store.py    # URI 캐시 (Redis)
│           ├── dedup.py          # cache stampede dedup
│           ├── splitter.py       # PDF 분할
│           └── validators.py     # 파일 유효성 검사
├── tests/
├── quickstarts/
│   ├── llm_manager/      # 매니저 API 예제 (quickstart_sdk, verify_all)
│   └── handler_client/   # handler_client SDK 예제 (01–08, 내부)
└── pyproject.toml
```


# 4. 설정 및 준비사항

llm-manager 하나만 설치하면 종속 manager(`kiwoom-mdb-manager`·`kiwoom-redis-manager`·`kiwoom-file-manager`)가 **자동으로 함께 설치**된다. 의존성이 같은 GitHub **private** 모노레포의 하위 폴더를 가리키므로, repo 접근 인증이 한 번 필요하다. 환경에 따라 A·B·C 중 하나를 쓴다.

### A. 개발자 로컬 — `gh` 로그인 (권장)

본인 GitHub 계정으로 로그인해두면 별도 키·토큰이 필요 없다.

**1회 준비** (이미 `gh` 로 로그인돼 있으면 생략)

```bash
gh auth login
```

로그인 후 git 의 https 인증이 자동으로 `gh` 자격증명을 사용한다(`credential.helper = gh auth git-credential`).

**설치**

```bash
pip install "git+https://github.com/KiwoomAM/Kiwoom-Manager.git@main#subdirectory=llm-manager"
```

이 한 줄로 llm-manager + 종속 manager 3개 + 공개 패키지까지 한 번에 설치된다.

> 전제: 그 계정이 `KiwoomAM/Kiwoom-Manager` repo 접근 권한이 있어야 한다 (org 멤버 또는 collaborator).

### B. Docker / 무인 배포 — GitHub App 토큰

`gh` 로그인이 없는 빌드 환경에서는, 사내 공용 **GitHub App 개인키(`certs/kiwoom.pem`)** 로 단기 토큰(1시간)을 발급해 인증한다. 토큰을 `git config insteadOf` 로 주입하면 평범한 https 의존성 URL 이 자동으로 인증되므로 — **pyproject 는 수정할 필요가 없다.**

```dockerfile
# 사내 공용 GitHub App 키를 빌드 컨텍스트에 둔다 (certs/kiwoom.pem)
COPY certs/kiwoom.pem /tmp/kiwoom.pem
ARG GITHUB_APP_ID=2826312
ARG GITHUB_INSTALLATION_ID=108948292

RUN pip install --no-cache-dir PyJWT cryptography requests \
    && GITHUB_TOKEN=$(python -c "\
import jwt, time, requests; \
pem = open('/tmp/kiwoom.pem', 'rb').read(); \
now = int(time.time()); \
payload = {'iat': now - 60, 'exp': now + 600, 'iss': '${GITHUB_APP_ID}'}; \
token = jwt.encode(payload, pem, algorithm='RS256'); \
r = requests.post('https://api.github.com/app/installations/${GITHUB_INSTALLATION_ID}/access_tokens', \
    headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'}); \
print(r.json()['token'])") \
    && git config --global url."https://x-access-token:${GITHUB_TOKEN}@github.com/".insteadOf "https://github.com/" \
    && pip install --no-cache-dir "git+https://github.com/KiwoomAM/Kiwoom-Manager.git@main#subdirectory=llm-manager" \
    && rm -f /tmp/kiwoom.pem \
    && git config --global --unset-all url."https://x-access-token:${GITHUB_TOKEN}@github.com/".insteadOf
```

- 토큰·키가 이미지에 남지 않도록 **같은 `RUN` 레이어**에서 설치 후 `insteadOf` 해제 + pem 삭제까지 한다.
- `git` 이 있어야 한다(`pip install git+https://` 용). slim 이미지면 `apt-get install -y --no-install-recommends git` 를 먼저 추가.
- App ID·Installation ID 는 비밀이 아니므로 그대로 둬도 되고, `--build-arg` 로 덮어써도 된다. 비밀은 `kiwoom.pem` 뿐이다.

### C. 모노레포 로컬 개발 — editable

모노레포 안에서 개발할 때는 옆 폴더를 직접 참조(editable)한다:

```bash
pip install -e ./llm-manager
# 또는 종속 manager 까지 editable 로:  pip install -r llm-manager/requirements.txt
```

`pyproject.toml` 의 `[tool.uv.sources]` 로컬 경로가 우선 적용되어, 이때는 GitHub 가 아니라 옆 폴더에서 가져온다. 그래서 A·B 의 GitHub 의존성 선언과 충돌하지 않는다.

---

별도 서버를 띄울 필요가 없다. 첫 호출 시 SDK가 handler 들을 하드코딩된 사내 URL로 자동 초기화한다 (`LLM_REQUEST_URL` 같은 설정 불필요).

## 환경변수 (선택 — 기본값 내장)

handler URL 기본값이 사내 주소이므로 보통 그대로 두면 된다. 다른 주소를 쓸 때만 환경변수로 override.

| 환경변수 | 기본값 | 설명 |
| --- | --- | --- |
| `LLM_HANDLER_URL` | `http://192.7.9.45:8100` | LLM Handler 서비스 |
| `MDB_HANDLER_URL` | `http://192.7.9.45:8300` | MDB Handler 서비스 |
| `REDIS_URL` | `redis://192.7.9.45:7617/0` | Redis |
| `FILE_HANDLER_URL` | `http://192.7.9.45:8600` | file-handler 서비스 |
| `MONGODB_DATABASE` | `kw_ai` | MongoDB 데이터베이스명 (`database=` 인자로도 지정 가능) |

## 데이터베이스 안내

| 환경 | `MONGODB_DATABASE` | 용도 |
| --- | --- | --- |
| 운영/에이전트 | `kw_ai` | 실제 에이전트 배포 시 사용 |
| 개발/테스트 | `kw_ai_dev` | Quickstart, 기능 테스트, 프롬프트 실험 |

개발 중 불필요한 데이터가 쌓이지 않도록 `save_mode=False` 또는 `database="kw_ai_dev"` 권장.


# 5. Quick Start

---

## 텍스트만

```python
from llm_manager import llm_request_async

response, meta = await llm_request_async(
    {"contents": "대한민국의 수도는?", "model": "gemini-2.5-flash"},
)
print(response.text)
print(meta.source)   # 'live' 또는 'cache'
```

## 파일 첨부 — `register_or_get` + `stored_files`

agent 가 file-manager SDK 의 `register_or_get` 으로 파일을 등록한 후, 받은 6 필드 dict 를 `StoredFile` 로 변환해 `stored_files=` 인자로 전달한다. llm-manager 는 `file_path` 만 받아 file-manager 에서 직접 fetch 한다.

### `StoredFile` 6 필드

| 필드 | 설명 |
| --- | --- |
| `file_name` | 파일명 (확장자 포함) |
| `file_size` | 파일 크기 (bytes) |
| `file_mtime` | 파일 수정 시각 (Unix timestamp, int) |
| `mime_type` | MIME 타입 (예: `application/pdf`) |
| `extension` | 확장자 (예: `.pdf`, 점 포함) |
| `file_path` | file-manager 가 부여한 UUID (`register_or_get` 결과) |

> **⚠️ file_name, file_size, file_mtime 은 실제 파일에서 추출한 값과 반드시 일치해야 한다.**
> 이 세 값은 캐시 키 생성에 직접 사용된다. 잘못된 값을 넣으면 캐시 미스가 반복 발생한다.

```python
from pathlib import Path
from file_manager import FileManagerHandler
from llm_manager import llm_request_async, StoredFile

fm = FileManagerHandler()

# 1. file-manager 등록 — build_metadata 로 5 필드 dict 생성 후 register_or_get
p = Path("report.pdf")
bytes_data = p.read_bytes()
metadata = fm.build_metadata(
    file_name=p.name,
    file_mtime=int(p.stat().st_mtime),
    file_size=p.stat().st_size,
)
sf_dict = await fm.register_or_get(bytes_data, metadata)
del bytes_data  # bytes release — agent 메모리 회수

# 2. llm-manager 호출 (file_path + 5 메타만 전송)
response, meta_resp = await llm_request_async(
    {"contents": "이 파일을 분석해줘", "model": "gemini-2.5-flash"},
    stored_files=[StoredFile(**sf_dict)],
)
print(response.text)
print(meta_resp.file_meta[0]["resolved_via"])  # "primary"
```

> **⚠️ Frontend 업로드 경로:** 위 예시는 서버 파일시스템에서 읽는 케이스. 브라우저 → 에이전트 backend 로 파일 업로드받는 경우 `file_mtime` 을 **frontend 가 명시 전달**해야 한다 (HTTP multipart 는 자동 전달 안 함). 빠뜨리면 같은 파일이 매 업로드마다 새 레코드로 쌓여 캐시 무의미. 상세는 [file-manager README](../file-manager/README.md#반드시-확인--frontend에서-파일을-받는-경우) 참고.

## 같은 파일 재호출

`register_or_get` 결과 dict 를 자기 DB 에 저장해두고 재호출 시 그대로 사용. file-manager 등록 / bytes 처리 모두 skip.

```python
# 첫 호출 시 sf_dict 자기 DB 에 저장
db.evaluations.insert_one({"file_meta": sf_dict, ...})

# 재호출 — bytes 처리 자체가 없음
saved = db.evaluations.find_one({...})["file_meta"]
ref = StoredFile(**saved)
response, _ = await llm_request_async(
    {"contents": "다른 질문", "model": "gemini-2.5-flash"},
    stored_files=[ref],
)
```

> ⚠️ **응답 `meta.file_meta` 를 저장한 경우 — `StoredFile(**...)` 시 422**
>
> 응답 `meta.file_meta[i]` 에는 server-fill `resolved_via` 가 박혀서 내려옴. agent 가 응답 dict 를 자기 DB 에 저장했다가 재호출 시 그대로 `StoredFile(**dict)` 로 넣으면 `extra="forbid"` 정책상 ValidationError. 6 필드만 픽킹 필요:
>
> ```python
> saved = db.evaluations.find_one({...})["meta_file_meta"]
> ref = StoredFile(**{k: saved[k] for k in StoredFile.model_fields})
> ```
>
> `register_or_get` 결과 (`sf_dict`) 를 저장했다면 6 필드만 있으므로 `StoredFile(**saved)` 로 바로 사용 가능 (위 예시).

### `resolved_via` 값

응답 `meta.file_meta[i].resolved_via` 가 fetch 경로를 알려준다 (디버깅 용도).

| 값 | 의미 |
| --- | --- |
| `"primary"` | `file_path` 1차 fetch 성공 |
| `"fallback"` | `file_path` stale → `check_cache` 로 새 path 발견 후 fetch. 호출자가 자기 DB 의 `file_path` 를 새 값으로 갱신 권장 |

> ⚠️ `resolved_via` 필드 도입 이전에 MDB 에 저장된 옛 doc 의 cache hit 응답에는 이 키가 없을 수 있다. 직접 인덱싱 (`fm["resolved_via"]`) 대신 `fm.get("resolved_via")` 사용 권장.

### 에러 처리 — `FileResolveError`

`get_file(file_path)` 1차 404 + `check_cache(hashing 3)` miss → `FileResolveError` raise (error_type='file_resolve'). caller 는 이를 catch 후 `register_or_get` 으로 재등록하여 재호출.

```python
from llm_manager import llm_request_async, StoredFile, FileResolveError

try:
    response, _ = await llm_request_async(llm_input, stored_files=refs)
except FileResolveError:
    # file-manager 에 정말로 없음 → 파일 재등록
    sf_dict = await fm.register_or_get(p.read_bytes(), fm.build_metadata(...))
    response, _ = await llm_request_async(
        llm_input, stored_files=[StoredFile(**sf_dict)],
    )
```


# 6. SDK

---

## 공개 API

| 함수 | 반환 타입 | 설명 |
| --- | --- | --- |
| `llm_request_async` | `tuple[LLMResponse, ResponseMeta]` | 비동기 단건 LLM 호출 |
| `llm_request` | `tuple[LLMResponse, ResponseMeta]` | 동기 단건 (`asyncio.run` 래퍼) |
| `llm_request_stream_async` | `tuple[AsyncIterator[dict], dict]` | 비동기 스트리밍 — `(stream, meta)` 튜플 |
| `llm_request_stream` | `tuple[Iterator[dict], dict]` | 동기 스트리밍 — `(stream, meta)` 튜플 |
| `done_check` | `DoneCheckResult` | 캐시 존재 여부만 확인 (LLM 호출 없음) |
| `fetch_by_cache_key` | `tuple[LLMResponse, ResponseMeta]` | 캐시 키로 직접 조회 |
| `file_preprocessing_async` / `file_preprocessing` | `dict[str, str] \| list[list[str]]` | 파일 전처리만 (Files API 업로드 + opt-in Context Cache 생성, LLM 호출 없음) |
| `delete_context_cache_async` / `delete_context_cache` | `ExpireResult` | Context Cache 를 **무조건 5초 뒤** 만료 (시간 인자 없음) |
| `update_context_cache_async` / `update_context_cache` | `ExpireResult` | Context Cache 수명을 `ttl`(1~3600초)로 **연장** |
| `close` | `None` (async) | 하위호환 no-op (httpx 클라이언트 제거됨). 호출해도 안전 |

## llm_request — 단건/스트리밍 LLM 호출

주력 함수. `llm_request_async`(비동기) / `llm_request`(동기 래퍼)는 단건 호출, `llm_request_stream_async` / `llm_request_stream`은 스트리밍이다. 모두 `(llm_input, *, stored_files, cache_mode, save_mode, database, timings, debug)` 시그니처를 공유하며 `(result, meta)` 를 반환한다. 아래는 입력·플래그·반환의 상세.

> ⚠️ **스트리밍은 반환된 스트림을 끝까지 소비하거나 닫아야 한다** (`aclose()` / sync 는 `close()`).
> 그 스트림이 dedup standby 리더 토큰을 쥐고 있어서, 받아 놓고 순회하지 않으면 같은 입력의 다른
> 요청이 최대 `standby_ttl`(기본 600초)만큼 대기한다. 대기자는 리더가 죽었는지 일하는 중인지
> 구분할 수 없어 TTL 을 다 기다린다. 상세는 `docs/36`.
>
> sync 판(`llm_request_stream`)은 **이미 실행 중인 이벤트 루프 안에서는 부를 수 없다** — async
> 컨텍스트에서는 `llm_request_stream_async` 를 쓴다.

### llm_input 구조

`llm_request_async`의 첫 번째 인자. **handler 생성 호출(`call()`)의 파라미터 대부분과 동일하며, 파일 관련만 `contents`에서 빼고 별도의 `stored_files=[StoredFile]` 인자로 분리**한 점이 차이다. `llm_input`에는 텍스트 프롬프트와 생성 파라미터만 넣고, 파일은 file-manager `register_or_get` 으로 등록 후 받은 6 필드를 `StoredFile` 로 구성해 `stored_files=` 로 넘기면 내부에서 file-manager 에서 bytes 를 fetch 해 inline / Files API 분류·업로드·캐싱을 자동 처리한다.

```python
response, meta = await llm_request_async(
    {
        "contents": "질문",             # 필수. str 또는 [str | Part | FileRef] 혼합 리스트
        "model": "gemini-2.5-flash",
        "system": "시스템 프롬프트",    # 선택
        "temperature": 0,               # 선택
        "max_tokens": 200,              # 선택
        "task_name": "fund_comment",    # 선택 (meta.task_name 에 기록). 한 서비스 내 호출(task) 구분 라벨
        "tool_config": "auto",          # 선택 ("auto" | "any" | "none")
        "media_resolution": "high",     # 선택 ("low" | "medium" | "high")
    },
    stored_files=[...],    # 선택. list[StoredFile] — file-manager register_or_get 결과
    cache_mode=True,       # 기본 True
    save_mode=True,        # 기본 True
    timings={"mdb_ttl": 3600},  # 선택. Timings TypedDict
    debug=False,           # 기본 False. True면 meta.route_reason 포함
    database=None,         # 기본 None → env MONGODB_DATABASE 또는 kw_ai
)
```

### handler `call()` 전체 파라미터

`llm_input` dict의 키로 사용할 수 있는 전체 목록.

```python
def call(
    contents: ContentsType,              # 필수. str | list[str | FileRef | Part]
    *,
    system: str | None = None,           # 시스템 프롬프트
    model: str | None = None,            # 모델명 (기본: gemini-2.5-flash)
    temperature: float | None = None,    # 0.0(결정적) ~ 2.0(창의적)
    max_tokens: int | None = None,       # 최대 출력 토큰 수
    seed: int | None = None,             # 재현성 시드값
    output: str | None = None,           # "json" | "enum" | "text"
    schema: type | dict | None = None,   # Pydantic / Enum / dict
    thinking: str | None = None,         # "minimal" | "low" | "medium" | "high"
    tools: list[dict] | None = None,     # Function calling / Built-in 도구 정의
    tool_config: str | None = None,      # "auto" | "any" | "none"
    history: list[dict] | None = None,   # 이전 대화 이력
    cached_content: str | None = None,   # Context Cache 이름
    task_name: str | None = None,        # 호출(task) 단위 라벨 (meta.task_name, 로깅용)
    media_resolution: str | None = None, # "low" | "medium" | "high"
) -> LLMResponse
```

스트리밍은 `stream()`으로 동일 파라미터 사용. async 버전은 `llm_manager.aio.stream()`.

#### `tools` 형식 — 평평한 dict 목록

**Gemini 원문서처럼 `function_declarations` 로 감싸면 안 된다** — handler 가 `HandlerInternalError`(500) 를 낸다.

```python
llm_input = {
    "contents": "삼성전자(005930) 현재가 알려줘.",
    "model": "gemini-3-flash-preview",
    "tools": [                                   # ✅ 평평한 dict 목록
        {
            "name": "get_stock_price",
            "description": "종목의 현재가를 조회한다",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string", "description": "종목코드"}},
                "required": ["ticker"],
            },
        }
    ],
    "tool_config": "any",                        # "auto" | "any" | "none" | "validated"
}

# ❌ tools: [{"function_declarations": [{...}]}]   → 500
```

내장(server-side) 도구는 이름만 있는 dict 로 켠다.

```python
"tools": [{"google_search": {}}]                 # url_context·code_execution 도 같은 꼴
```

도구를 쓰면 `response.text` 가 **비어 있을 수 있고**(`None`), `response.function_calls` 에 호출이 담긴다. **둘은 한 턴에 함께 올 수 있으니 독립적으로 확인할 것** — `if response.text is None:` 안에서 호출을 처리하면 텍스트가 같이 온 턴에서 놓친다. 스트리밍은 아래 [스트리밍 이벤트](#스트리밍-이벤트) 참고.

### cache_mode / save_mode

조합별 흐름은 [8. Runner — 캐시 조회 및 LLM 호출 흐름](#8-runner) 참고.

### Timings TypedDict

시간 관련 설정을 하나의 TypedDict로 묶어서 전달한다. 일부 키만 지정하면 나머지는 기본값으로 채워진다.

| 키 | 기본값 | 역할 | 조정 기준 |
| --- | --- | --- | --- |
| `http_timeout` | `600.0` | SDK → Service HTTP 응답 대기 시간 (초) | 느린 모델 사용 시 늘릴 것. 기본 600초로 대부분 충분 |
| `mdb_ttl` | `None` | MDB 캐시 수명 (초). None = 영구 저장 | **현재 미동작 — 설정해도 만료 처리 안 됨. 항상 영구 저장** |
| `context_cache_ttl` | `None` | Gemini Context Cache 수명 (초) | **`None` 또는 `0` 이면 Context Cache 비활성**. 양수(`>0`) 값만 생성 트리거. 반복 참조 파일에 Context Cache 를 쓰려면 양수 값 지정 필요 (예: `3600`) |
| `standby_ttl` | `600` | Cache stampede dedup 대기 시간 (초) | 동일 요청이 동시에 몰릴 때 중복 LLM 호출 방지(**`save_mode=True` 일 때만 — docs/35**). LLM 응답 시간보다 길게 설정 |

```python
response, meta = await llm_request_async(
    {"contents": "...", "model": "gemini-3-flash-preview"},
    timings={
        "context_cache_ttl": 3600,  # Context Cache 사용 시 필수
    },
)
```

### debug

`debug=True` 설정 시 `meta.route_reason`에 해당 요청이 어떤 경로로 처리됐는지가 기록된다(기본 `False`). 값의 의미는 다음과 같다.

```
"cache_hit"              # 캐시 히트
"cache_miss"             # 캐시 미스
"cache_error_fallback"   # 캐시 조회 오류 → LLM fallback
"cache_deserialize_error" # 캐시 역직렬화 실패 → LLM 재호출
"standby_wait_hit"       # Dedup standby 대기 중 다른 요청이 완료
"cache_off_save_off"     # cache_mode=False, save_mode=False
"cache_off_save_on"      # cache_mode=False, save_mode=True
```

### 반환 타입

### LLMResponse

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `text` | `str \| None` | 모델 응답 텍스트 (Tool calling 시 None일 수 있음) |
| `model` | `str` | 사용된 모델명 |
| `usage` | `TokenUsage \| None` | 토큰 사용량 |
| `finish_reason` | `str \| None` | 종료 사유 (`"STOP"`, `"MAX_TOKENS"`, `"SAFETY"` 등) |
| `function_calls` | `list[FunctionCall] \| None` | 모델이 요청한 client-side 함수 호출 |
| `tool_calls` | `list[ToolCallInfo] \| None` | Gemini server-side tool 호출 흔적 (google_search 등) |
| `tool_responses` | `list[ToolResponseInfo] \| None` | 그 tool 의 응답 |
| `code_blocks` | `list[CodeBlock] \| None` | code_execution 이 생성한 코드 |
| `code_results` | `list[CodeResult] \| None` | 그 코드의 실행 결과 |
| `grounding` | `Grounding \| None` | google_search 인용 출처 |
| `url_context` | `UrlContextMetadata \| None` | url_context 도구가 읽은 URL |
| `assistant_message` | `Content \| None` | 그 턴의 완성 발화 (role + Part 목록). 다음 턴 이력에 그대로 echo 가능 |
| `request_id` | `str` | 요청 식별자 |

### 스트리밍 이벤트

`llm_request_stream_async` / `llm_request_stream` 은 `LLMResponse` 가 아니라 **이벤트 dict** 를 흘린다. 항상 세 키를 갖는다.

```python
{"type": "chunk", "text": ..., "finish_reason": ...}
```

아래 키는 **그 청크에 값이 있을 때만** 붙으므로 쓰기 전에 있는지 확인해야 한다. 값은 모두 `model_dump()` 된 dict 라 그대로 `json.dumps` 로 떨어진다.

| 키 | 설명 |
| --- | --- |
| `usage` | 토큰 사용량 |
| `function_calls` | 모델이 요청한 client-side 함수 호출 |
| `tool_calls` · `tool_responses` | server-side tool 호출·응답 |
| `code_blocks` · `code_results` | code_execution 코드·결과 |
| `grounding` | google_search 인용 출처 |
| `url_context` | url_context 도구가 읽은 URL |

**리스트 5종(`function_calls`·`tool_calls`·`tool_responses`·`code_blocks`·`code_results`)은 소비자가 이어붙여 누적해야 한다.** 청크마다 자기 몫만 실려 오기 때문이다 — 병렬 함수 호출 N 개는 N 개 청크에 1 개씩 나뉘어 온다. 덮어쓰기로 구현하면 마지막 청크 것만 남아 **앞의 호출이 조용히 사라진다.**

```python
stream, meta = await llm_request_stream_async(llm_input)

calls = []
async for ev in stream:
    if ev.get("text"):
        print(ev["text"], end="")
    calls += ev.get("function_calls", [])      # ← 덮어쓰지 말고 누적
```

> **캐시 히트면 스트림 전체가 이벤트 1 개다.** 저장해 둔 응답을 청크 하나로 되돌리므로 `text`(전문)와 도구 필드가 **같은 이벤트에** 실린다. 라이브에서는 도구 필드가 text 없는 청크로 따로 오는 것이 보통이라, `if ev.get("text"): ... else: 도구` 로 짜면 캐시 히트마다 함수 호출을 놓친다. 두 필드를 독립적으로 볼 것.

> **캐시 히트에서 두 부류의 결과가 갈린다.** 리스트 5 종은 저장 경로가 이어붙여 두므로 캐시 히트 이벤트 하나가 라이브를 누적한 것과 같은 집합이 된다. 반면 `grounding`·`url_context` 는 저장 경로가 **마지막 non-None 으로 덮어써서**, 여러 번 나뉘어 온 턴이면 캐시 히트에는 마지막 것만 남는다. 즉 같은 입력이라도 캐시 히트 여부에 따라 인용 출처 집합이 달라질 수 있다(별건 — 이슈 #99 범위 밖).

### ResponseMeta

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `source` | `str` | `'live'` (LLM 직접 호출) 또는 `'cache'` |
| `cache_hit` | `bool` | 캐시 히트 여부 |
| `cache_key` | `str \| None` | SHA-256 캐시 키 (64자) |
| `metadata` | `dict` | `{"service_name": ..., "task_name": ..., "model": ...}` — `service_name`=env `SERVICE_NAME`(실행 주체), `task_name`=`llm_input`의 호출 라벨 |
| `collection` | `str \| None` | MongoDB 컬렉션명 |
| `route_reason` | `str \| None` | `debug=True`일 때만 포함 |
| `file_meta` | `list[dict] \| None` | `stored_files` 인자로 호출 시 파일별 메타(내부 `FileInput` 을 `data`(bytes)만 빼고 dump)가 담긴 list. 6 식별 필드(`file_name`·`file_size`·`file_mtime`·`mime_type`·`extension`·`file_path`) + `resolved_via`·`page_count`·`image_tokens`·`pdf_part_ranges`. stored_files 없는 호출이면 `None` (응답 dict 에는 키 자체 부재). 호출자가 자기 DB 에 저장 후 재호출 등에 활용 가능. `pdf_part_ranges`: 50MB/1000p 초과로 분할 업로드된 PDF 의 파트별 원본 페이지 범위 `[[1,500],[501,1000],…]`(1-based·끝 포함·업로드 순서), 비분할·비PDF면 `None` — 분할 파트는 각자 p.1 부터 재시작하므로 모델이 인용한 (파트, 로컬쪽)을 원본 절대쪽으로 환산할 때 쓴다 |
| `context_cache_id` | `str \| None` | Context Cache 생성/HIT 시 노출되는 식별자 (= hash_key). `delete_context_cache_async` / `update_context_cache_async` 호출에 사용. live 호출일 때만 노출되며, MDB cache HIT 응답에는 키 자체 부재. **영구 식별자가 아니다** — Gemini 측 TTL 이 만료되면 자동 파괴되고, 같은 입력으로 재호출 시 새 cache (새 id) 가 생성된다 |

#### `file_meta` 활용 예시

```python
response, meta = await llm_request_async(llm_input, stored_files=[StoredFile(...)])

if meta.file_meta:
    for fm in meta.file_meta:
        # fm = {"file_name", "file_size", "file_mtime", "mime_type", "extension", "file_path"}
        save_to_db(fm["file_path"])
```

> 응답 shape — stored_files 가 있을 때만 `meta.file_meta` 키가 들어가고 (MongoDB doc 의 `file_meta` 동작과 거울), 없으면 키 자체 부재. 옛 SDK 사용 시 `ResponseMeta` 가 모르는 키는 Pydantic v2 기본 `extra="ignore"` 로 폐기 — 자연 호환.

## done_check

MDB 캐시 존재 여부만 확인한다. LLM을 호출하지 않는다.
캐시 키 생성 규칙은 [8. Runner — 캐시 키 생성](#8-runner) 참고.

```python
from llm_manager import done_check, FileKeyingMeta

dc = await done_check(
    {"contents": "대한민국의 수도는?", "model": "gemini-2.5-flash"},
    files=[FileKeyingMeta(file_name="report.pdf", file_size=1024, file_mtime=1700000000)],
)

print(dc["cache_key"])    # SHA-256 캐시 키
print(dc["cache_hit"])    # True / False
print(dc["cached"])       # 캐시 문서 dict 또는 None
```

## fetch_by_cache_key

`done_check`으로 확보한 `cache_key`를 사용해 LLM 재호출 없이 캐시 결과를 조회한다.

```python
from llm_manager import fetch_by_cache_key

response, meta = await fetch_by_cache_key(cache_key)
print(response.text)
print(meta.source)   # "cache"
```

# 7. File Handler

---

파일이 `llm_request_async`에 전달되면 내부(`_internal/file_processing`)에서 아래 순서로 처리된다.

```
StoredFile 리스트 (file_path + 5 메타)
  ↓
① 유효성 검사 (validators) — placeholder FileInput(data=b"") 으로 검증
  ↓
② 파일 분류 (resolver.classify_files)
     inline_pool / files_api_pool 분리
  ↓
③ 처리 방식 결정 (resolver.resolve_files) — bytes 가 필요한 시점에 file-manager 에서 lazy fetch
     inline       → fetch → bytes 그대로 전달 (standby 없음, 동시 N 요청 시 N 회 fetch)
     files_api    → URI 캐시 확인 → 없으면 standby leader 1 명만 fetch + 업로드
     context_cache_ttl 지정 시 → Context Cache 생성
```

## 유효성 검사

진입 즉시 fail-fast로 차단. 아래 두 조건 중 하나라도 위반하면 에러 반환.

**지원 MIME 타입 화이트리스트**

| 분류 | MIME 타입 |
| --- | --- |
| 이미지 | `image/png`, `image/jpeg`, `image/webp`, `image/heic`, `image/heif` |
| 오디오 | `audio/wav`, `audio/mp3`, `audio/aiff`, `audio/aac`, `audio/ogg`, `audio/flac` |
| 문서 | `application/pdf`, `text/plain`, `text/html`, `text/csv`, `text/markdown`, `text/xml`, `application/json`, `application/xml` |
| 비디오 | `video/mp4`, `video/mpeg`, `video/mov`, `video/avi`, `video/x-flv`, `video/webm`, `video/3gpp` |

**크기 제한**: 단일 파일 **2GB** 초과 시 거부 (inline·Files API 어느 경로로도 처리 불가)

## 파일 분류 전략

| 조건 | 처리 방식 |
| --- | --- |
| 비디오 | 항상 **Files API** (inline 불가) |
| inline 풀 합계 ≤ 20MB (이미지·오디오 포함 시) | **inline** |
| inline 풀 합계 ≤ 50MB (PDF·텍스트만) | **inline** |
| 위 한도 초과 | **Files API** |

이미지나 오디오가 하나라도 섞이면 inline 합계 한도가 20MB로 낮아진다. 작은 파일부터 한도 내에 채우고 초과분은 Files API로 넘긴다.

> PDF가 Files API 경로이고 **50MB 초과**이면 자동 분할 후 업로드 (→ PDF 자동 분할 참고)

## Files API URI 캐시 (Redis)

같은 파일을 매번 다시 업로드하지 않도록 URI를 Redis에 캐싱한다.

- **키**: `file_cache:files_api:{checksum}` (checksum = SHA-256(file_name + mtime + size))
- **TTL**: Gemini가 반환한 만료 시각 기준으로 동적 계산 (1시간 버퍼 차감). 계산 실패 시 **47시간** 폴백 (Gemini Files API 보존 기간 48시간보다 1시간 짧게 설정)
- 업로드 전 Redis 조회 → HIT이면 업로드 생략

## PDF 자동 분할

PDF가 50MB를 초과하면 자동으로 분할 후 병렬 업로드한다.

1. 페이지별 예상 크기 계산 (이미지 xref + 텍스트 콘텐츠)
2. 50MB 이하가 되도록 페이지 그룹핑
3. 그룹별 저장 → 크기 초과 시 재귀 재분할 (최대 3단계)
4. 인접 파트를 45MB 상한 내에서 병합

## Context Cache (opt-in)

`timings={"context_cache_ttl": 3600}` 을 지정할 때만 활성화된다. **지정하지 않으면 Context Cache는 생성되지 않는다.**

같은 **파일 조합 + 모델 + 시스템 프롬프트 + tools + tool_config** 가 반복될 때 입력 토큰을 최대 90% 절감한다.

- **캐시 키**: SHA-256(file_checksums + model + system_instruction + tools + tool_config)
- **Redis TTL**: 기본 1시간

> **Context Cache는 시간 단위로 비용이 발생한다.** 필요 이상으로 긴 TTL은 불필요한 비용을 유발하므로 실제 사용 패턴에 맞게 설정할 것.

> **캐시 키 재료(5개) 중 하나라도 달라지면 캐시 미스가 발생한다.** Context Cache는 `file_checksums` + `model` + `system_instruction` + `tools` + `tool_config` 를 하나의 키로 묶으므로, 이 중 하나라도 바뀌면 새로운 캐시가 생성된다. 특히 `tools` / `tool_config` 는 cache 생성 시 Gemini 쪽에도 함께 저장되므로, 같은 파일로도 tools 가 다르면 별도 cache 가 만들어진다 (오염된 cache HIT 방지).

> **파일별 라벨은 Context Cache 콘텐츠에만 붙는다 (출처 귀속용).** 캐시에 담기는 콘텐츠 앞에 파일 헤더 `===== [문서 N] 파일명 =====`(분할 파일은 `(총 N개 파트로 분할 업로드됨)`)를 삽입해, 소비자가 응답의 출처를 파일별로 귀속할 수 있게 한다(docs/29). 단 **캐시 경로를 타지 않는 응답**(`context_cache_ttl` 미지정, too-small fail-soft 로 URI 폴백 등)에는 라벨이 없다 — 라벨을 출처 귀속에 쓰는 소비자는 "라벨 부재 = 캐시 미적용" 상황을 견디도록 짜야 한다.

### Context Cache 수명 관리 — `delete_context_cache` (만료) / `update_context_cache` (연장)

`context_cache_ttl` 을 길게 잡아두고, agent task 가 끝났을 때 즉시 만료시킬 수 있다. storage 비용은 새 TTL 기준으로 분 단위 prorate 청구되므로, "넉넉히 잡고 끝나면 줄이기" 패턴이 비용 효율적이다. 반대로 더 오래 살려야 하면 `update_context_cache` 로 TTL 을 연장한다.

두 함수 모두 **`context_cache_id`(= hash_key)** 를 받는다 — `meta.context_cache_id` 또는 `file_preprocessing`(context cache 모드) 반환의 `context_cache_id`. (Gemini 의 `cached_content_name`(`cachedContents/...`)은 받지 않는다 — 넣으면 `ValueError`. Redis 키 매칭이 안 돼 조용한 no-op 이 되기 때문.)

```python
from llm_manager import llm_request_async, delete_context_cache_async, StoredFile

cache_ids: set[str] = set()
for q in questions:
    response, meta = await llm_request_async(
        {"contents": q, "model": "gemini-2.5-flash"},
        stored_files=[StoredFile(**sf_dict)],
        timings={"context_cache_ttl": 3600},
    )
    if meta.context_cache_id:
        cache_ids.add(meta.context_cache_id)

# task 끝나면 일괄 만료 — 무조건 5초 뒤 (sync 래퍼 delete_context_cache 도 있음)
result = await delete_context_cache_async(list(cache_ids))
print(result.expired)   # ["..."]
print(result.failed)    # []
```

**`delete_context_cache(cache_ids)`** — **무조건 5초 뒤** 만료. 시간 인자 없음(5초는 in-flight 요청 보호용 고정 grace). Redis/Gemini 부재는 `expired` 로 idempotent 성공 처리.

**`update_context_cache(cache_ids, *, ttl)`** — 수명을 `ttl`(1~3600초)로 **연장**. delete 와 반대로, 연장 대상이 없으면(Redis 부재·Gemini 404) 해당 id 는 `failed` 로 분류된다.

```python
from llm_manager import update_context_cache_async

res = await update_context_cache_async(cache_id, ttl=3600)   # 1시간 더 살림
print(res.expired)   # 연장 성공한 id
print(res.failed)    # 부재/404 등 실패한 id
```

> ⚠️ `meta.context_cache_id` 는 **live 호출일 때만** 응답에 들어간다. MDB cache HIT 응답에는 키 자체 부재. 첫 호출이 cache HIT 이면 그 task 의 cache_id 는 받지 못하지만, Gemini 측 cache 는 자연 만료되므로 큰 문제 없음.

> ⚠️ `context_cache_id` 는 **영구 식별자가 아니다.** Gemini 측 TTL 이 만료되면 cache 가 자동 파괴되고, 같은 입력으로 다시 호출하면 새 cache (새 id) 가 생성된다. agent 가 옛 id 를 DB 에 저장해두고 나중에 `delete_context_cache_async` 로 호출해도 이미 만료된 cache 라면 `expired` idempotent 처리되니 안전 — 다만 옛 id 가 살아있다고 가정한 동작은 만들지 말 것.

> ⚠️ **expire 가 필요한 워크플로는 live 호출 직후의 `meta.context_cache_id` 를 보관할 것.** MDB cache HIT 응답에서는 노출되지 않으므로, 같은 입력으로 재호출하면 ID 를 받을 수 없어 그 시점에 expire 가 불가능하다 (옛 cache 가 살아있어도 호출자 측에서 ID 모르는 상태).

### 파일 전처리만 — `file_preprocessing`

LLM 호출 없이 **파일 처리(Files API 업로드 + opt-in Context Cache 생성)만** 미리 돌리고 싶을 때 쓴다. `llm_request` 의 LLM 캐시 조회·LLM 호출 조립은 빼고, stored_files 를 받아 업로드 핸들만 돌려준다 (pre-warming).

- 입력: `llm_input`(`model`/`system`/`tools`/`tool_config` 만 사용 — `contents` 는 안 봄), `stored_files`, `timings`(`context_cache_ttl` opt-in).
- **classify 를 건너뛰고 전 파일을 Files API 로 강제 업로드**한다 (작은 파일도 URI 보장).

```python
from llm_manager import file_preprocessing_async, StoredFile

# files_api 모드 (context_cache_ttl 미지정) → 파일별 (n, ?) URI 리스트
uris = await file_preprocessing_async(
    {"model": "gemini-2.5-flash"},
    stored_files=[StoredFile(**sf_a), StoredFile(**sf_b)],
)
# uris == [["files/aaa"], ["files/bbb1", "files/bbb2"]]   # b 는 분할되어 2개

# context cache 모드 (context_cache_ttl 지정) → dict 두 키
res = await file_preprocessing_async(
    {"model": "gemini-2.5-flash", "system": "..."},
    stored_files=[StoredFile(**sf_a)],
    timings={"context_cache_ttl": 1800},
)
# res == {"context_cache_id": "<hash_key>",          # update/delete_context_cache 에 사용
#         "cached_content_name": "cachedContents/.."} # Gemini 직접 호출용
# (토큰 미달 too-small 이면 캐시 skip → (n, ?) URI 리스트로 폴백)
```

| 모드 | 조건 | 반환 |
| --- | --- | --- |
| files_api-only | `context_cache_ttl` 미지정 | `(n, ?)` 파일별 URI 리스트 |
| context cache | `context_cache_ttl` 양수 + 생성 성공 | `{"context_cache_id", "cached_content_name"}` |
| too-small 폴백 | context cache 모드인데 토큰 미달 | `(n, ?)` URI 리스트 |

> 이후 같은 `stored_files` 로 `llm_request` 를 호출하면 매니저가 Redis 에서 업로드/캐시를 자동 재사용하므로, `file_preprocessing` 반환값을 직접 들고 다닐 필요는 없다 — 단 Context Cache 를 수동 만료/연장하려면 `context_cache_id` 를 보관한다.

## Cache Stampede Dedup

동일 파일을 동시에 여러 요청이 업로드하려 할 때 중복 업로드를 방지한다.

- Redis SETNX로 선점 (`file_cache:files_api_standby:{checksum}`)
- 선점 실패한 요청은 exponential backoff으로 대기 (0.5초 → 1 → 2 → 4 → 최대 10초)
- 대기 중 캐시 HIT 확인 → HIT 시 즉시 반환
- `standby_ttl` 초과 시 대기 포기 후 직접 업로드 (중복 허용, 회복력 우선)


# 8. Runner

---

LLM 요청의 전체 실행 흐름. 캐시 키 생성 → 캐시 조회 → LLM 호출 → 저장 순으로 진행된다.

```
llm_input + files
  ↓
① 캐시 키 생성 (keying)
     file_checksums 주입 → subset 추출 → 정규화 → SHA-256
  ↓
② 캐시 조회 (done_check → MongoDB)
     HIT  → 즉시 반환
     MISS ↓
③ 파일 처리 (file_processing)
  ↓
④ LLM 호출 (handler_client.aio.call)
  ↓
⑤ 결과 저장 (store → MongoDB)
  ↓
⑥ 반환
```

## 캐시 키 생성

### 1. 파일 checksum 주입

파일이 있으면 각 파일의 `(file_name, file_size, file_mtime)` 3개 필드로 SHA-256 checksum을 계산한 후 `file_checksums` 키로 `llm_input`에 주입한다. 이후 캐시 키는 텍스트 입력 + 파일 조합 전체를 반영한다.

> 파일이 있으면 `cached_content`는 volatile로 처리되어 캐시 키에서 제외된다.

### 2. subset 추출

아래 16개 필드만 캐시 키 계산에 사용된다. 목록에 없는 필드는 무시된다. (호출 라벨 `task_name` 은 키에서 제외 — 출력에 영향 없는 라벨이라 metadata 로만 저장. 반면 `service_name`=실행 서비스는 키에 포함.)

| 필드 | 설명 |
|------|------|
| `contents` | 필수. 질문/프롬프트 |
| `system` | 시스템 프롬프트 |
| `history` | 이전 대화 이력 |
| `model` | 모델명 |
| `max_tokens` | 최대 출력 토큰 수 |
| `temperature` | 온도 |
| `cached_content` | Context Cache 이름 (파일 있으면 제외) |
| `service_name` | 실행 서비스 식별자 (env SERVICE_NAME) |
| `seed` | 재현성 시드값 |
| `output` | 출력 형식 |
| `schema` | 출력 스키마 |
| `thinking` | 사고 수준 |
| `tools` | 도구 정의 |
| `tool_config` | 도구 설정 |
| `media_resolution` | 미디어 해상도 |
| `file_checksums` | 파일 checksum 리스트 (파일 있을 때만) |

### 3. subset 전처리

`build_llm_input_subset()`에서 위 필드를 추출. 값이 `None`이거나 키가 없으면 `""`로 치환.

```python
llm_input = {"contents": "안녕", "temperature": None}

# 결과 subset:
{
    "contents": "안녕",   # 값 있음 → 그대로
    "temperature": "",    # None → "" 치환
    "model": "",          # 키 없음 → "" 치환
    ...                   # 나머지 필드도 모두 "" 포함
}
```

### 4. 정규화 (canonicalize)

`canonicalize_llm_input()`에서 각 필드 값을 재귀적으로 정규화한다.

| 타입 | 처리 방식 |
|------|-----------|
| `dict` | 키 문자열 변환, `None` → `""`, volatile 키 제거 |
| `list` / `tuple` | 각 원소 재귀 정규화. **순서 유지** (contents·history는 순서가 의미 있음) |
| `float` | 소수점 4자리 반올림 (`0.30000001` → `0.3`) |
| `Decimal` | `normalize()` 후 문자열 변환 (`Decimal("1.200")` → `"1.2"`) |
| `datetime` / `date` | `isoformat()` 변환 |
| `bytes` / `bytearray` | hex 문자열 변환 (`b'\xff\x00'` → `"ff00"`) |
| `str` / `int` / `bool` / `None` | 그대로 사용 |

**volatile 키**: 요청마다 달라지는 메타데이터. 포함하면 같은 내용의 요청도 다른 캐시 키가 된다.

| volatile 키 | 성격 |
|-------------|------|
| `timestamp`, `ts`, `created_at`, `updated_at` | 시간 정보 |
| `request_id`, `trace_id` | 요청 추적용 고유 ID |

### 5. JSON 직렬화 → SHA-256

- dict 키 알파벳 정렬
- 구분자 공백 제거 (값 내부 공백은 유지)
- UTF-8 인코딩 후 SHA-256 → **64자리 hex 캐시 키**

```python
# 값 안의 공백은 유지되므로 아래 둘은 다른 캐시 키:
"안녕하세요"   → 해시 A
"안녕하세요 "  → 해시 B  (뒤에 공백 하나)
```

## 캐시 조회 및 LLM 호출 흐름

`cache_mode` / `save_mode` 조합에 따라 경로가 달라진다.

| cache_mode | save_mode | 흐름 | agent_llm standby |
| --- | --- | --- | --- |
| `False` | `False` | 키 생성 없음 → LLM 직접 호출 → 저장 없음 | 안 씀 |
| `False` | `True` | 키 생성 → LLM 호출 → MDB 저장 | 씀 |
| `True` | `True` | 키 생성 → MDB 조회 → HIT 반환 / MISS LLM 호출 + 저장 | 씀 |
| `True` | `False` | 키 생성 → MDB 조회 → HIT 반환 / MISS LLM 호출만 | 안 씀 |

**두 플래그는 MDB 응답 캐시만 가른다.** `stored_files` 첨부는 네 조합 모두에서 동일하게 동작한다 —
업로드·inline 분류·Context Cache·404 자동 복구 전부 그대로다.

**standby 는 `save_mode=True` 일 때만 건다.** 대기자가 기다린 대가로 받는 것은 리더가 MDB 에 저장한
결과이므로, 저장하지 않는 조합에서 줄을 세우면 대기자가 빈손으로 깨어나 자기가 다시 LLM 을 부른다
(LLM 호출 수는 그대로고 지연만 쌓인다). 상세는 `docs/35`.

## MDB 저장 구조

상세 스키마는 [10. MDB 저장 구조](#10-mdb-저장-구조) 참고.


# 9. (구) Service API — 제거됨

---

> ⚠️ **0.3.0 에서 FastAPI 서버(port 8101)와 아래 HTTP 엔드포인트는 전부 제거됐다.**
> 이제 SDK 공개 함수(§6)가 같은 프로세스에서 manager 로직을 직접 호출한다. HTTP 요청 경로는 없다.
> 아래 요청/응답 표는 **각 공개 함수가 받고 돌려주는 데이터 형태의 참고**로만 남겨둔다
> (`/v1/request` ≈ `llm_request_async`, `/v1/done-check` ≈ `done_check`, `/v1/fetch` ≈ `fetch_by_cache_key`).

## (구) POST /v1/request

LLM 호출 + 캐시 조회/저장. SDK `llm_request_async`의 백엔드.

**요청 바디**

| 필드 | 타입 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `llm_input` | `dict` | — | llm-handler에 전달할 입력 |
| `stored_files` | `list[StoredFile] \| null` | `null` | file-manager 등록 후 받은 6 필드 리스트. 내부에서 `file_path` 로 bytes 를 lazy fetch |
| `cache_mode` | `bool` | `true` | MDB 캐시 조회 여부 |
| `save_mode` | `bool` | `true` | MDB 저장 여부 |
| `ttl_seconds` | `int \| null` | `null` | MDB 캐시 수명 (현재 미동작) |
| `database` | `str \| null` | `null` | MDB database 이름 (기본 `kw_ai`) |
| `context_cache_ttl` | `int \| null` | `null` | Gemini Context Cache 수명 (초). `null` = 생성 안 함 |
| `standby_ttl_seconds` | `int` | `600` | Cache stampede dedup 대기 timeout (초) |
| `debug` | `bool` | `false` | `true`면 응답 `meta.route_reason`에 처리 경로 포함 |

**StoredFile 구조** (6 필드)

| 필드 | 설명 |
| --- | --- |
| `file_path` | file-manager 가 부여한 저장 경로 (`register_or_get` 결과) |
| `file_name` | 파일 이름 |
| `file_size` | 파일 크기 (bytes) |
| `file_mtime` | 수정 시각 (unix timestamp) |
| `mime_type` | MIME 타입 |
| `extension` | 확장자 |

**응답**

```json
{
  "result": { /* LLMResponse.model_dump() */ },
  "meta": {
    "source": "live | cache",
    "cache_hit": true,
    "cache_key": "sha256...",
    "collection": "agent_llm",
    "route_reason": "...",
    "metadata": {}
  }
}
```

## POST /v1/request/stream

스트리밍 LLM 요청. SSE(Server-Sent Events) 형식으로 응답. 요청 바디는 `/v1/request`와 동일.

**응답 (SSE)**

```
data: {"type": "chunk", "text": "...", "finish_reason": null}
data: {"type": "chunk", "text": "...", "finish_reason": "STOP", "usage": {...}}
data: [DONE]
```

## POST /v1/done-check

MDB에 해당 입력의 응답이 이미 저장되어 있는지 확인. LLM 호출 없음.

**요청 바디**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `llm_input` | `dict` | 캐시 키 계산 대상 입력 |
| `files` | `list[FileKeyingMeta] \| null` | 파일 메타 (file_name, file_size, file_mtime). bytes 불필요 |
| `database` | `str \| null` | MDB database |

**응답**

```json
{
  "cache_key": "sha256...",
  "canonical_json": "{...}",
  "cached": { /* 저장된 문서 또는 null */ },
  "cache_hit": true,
  "collection": "agent_llm"
}
```

## POST /v1/fetch

캐시 키를 직접 지정해 MDB에서 결과 조회.

**요청 바디**

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `cache_key` | `str` | 조회할 SHA-256 캐시 키 |
| `database` | `str \| null` | MDB database |

**응답**: `/v1/request`와 동일한 `result` + `meta` 구조.


# 10. MDB 저장 구조

---

## Database / Collection

| 설정 | 값 |
| --- | --- |
| 기본 database | `kw_ai` (`MONGODB_DATABASE` env 또는 `database` 파라미터로 변경) |
| 기본 collection | `agent_llm` |

테스트/검증 시 `kw_ai_dev`를 사용한다 (`database="kw_ai_dev"`).

## 문서 스키마

`_id`는 SHA-256 캐시 키(64자 hex). 같은 키로 재저장 시 upsert(전체 교체).

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `_id` | `str` | SHA-256 캐시 키 |
| `canonical_json` | `str` | 캐시 키 계산에 쓰인 정규화 JSON |
| `result` | `dict` | `LLMResponse.model_dump()` |
| `metadata` | `dict` | `{"service_name": ..., "task_name": ..., "model": ...}` — `service_name`=env `SERVICE_NAME`(실행 주체), `task_name`·`model`=`llm_input`에서 추출 |
| `created_at` | `str` | 저장 시각 (UTC ISO) |
| `requested_at` | `str` | LLM 요청 시각 (UTC ISO, LLM 호출 시에만) |
| `expires_at` | `str` | `mdb_ttl` 설정 시에만. **현재 필드만 기록, 자동 삭제 미동작** |
| `file_meta` | `list[dict]` | `stored_files` 포함 요청 시. 각 `StoredFile` 의 6 필드(`file_path` 포함) 리스트 |

## TTL 현황

`mdb_ttl` / `ttl_seconds` 지정 시 `expires_at` 필드가 기록된다.
MongoDB TTL 인덱스가 없어 자동 삭제는 동작하지 않으며, 현재 모든 문서는 영구 저장 상태다.


# 11. 에러 처리

---

## 공개 예외 계층

서버가 제거되어 manager 로직의 예외가 호출자에게 **직접** 전파된다. 모두 `LLMManagerError` 를 상속하며 `.error_type` / `.status_code` 속성을 갖는다 (속성은 분기/로깅용으로 유지).

| 예외 | `error_type` | `status_code` | 발생 조건 |
| --- | --- | --- | --- |
| `ValidationError` | `validation` | 422 | 입력 검증 실패 |
| `UnsupportedFileTypeError` | `unsupported_file_type` | 422 | Gemini 지원 외 MIME 타입 |
| `FileTooLargeError` | `file_too_large` | 422 | 개별 파일 2GB 초과 |
| `FileUploadError` | `file` | 502 | Files API 업로드 실패 |
| `FileResolveError` | `file_resolve` | 502 | stored_files resolve 실패 (file-manager에서 파일 못 찾음) |
| `CacheError` | `cache` | 502 | MDB / Redis 캐시 조회·저장 실패 |
| `CacheKeyNotFoundError` | `not_found` | 404 | `fetch_by_cache_key` 대상 캐시 문서 없음 |
| `LLMManagerError` | `handler` | 500 | 베이스 / 그 외 fallback |

LLM 호출 실패는 `HandlerError` / `HandlerConnectionError` 가 그대로 전파된다 (흡수한 handler_client 가 re-export).

```python
from llm_manager import (
    llm_request_async, CacheError, FileResolveError,
    LLMManagerError, HandlerError, HandlerConnectionError,
)

try:
    response, meta = await llm_request_async(llm_input)
except FileResolveError:
    ...                       # 파일 재등록 후 재호출
except (HandlerError, HandlerConnectionError) as e:
    ...                       # LLM 호출 자체 실패
except LLMManagerError as e:
    print(e.error_type, e.status_code, e.message)
```

> **오류 발생 시**: `[route]` / `[dedup]` 로그 패턴, `route_reason` 값(`debug=True`) 으로 처리 경로를 추적할 수 있다.

## LLM 오류는 MDB에 저장되지 않음

LLM 호출 중 예외가 발생하면 저장 단계로 진입하지 않아 캐시 문서가 생성되지 않는다.


# 12. 마이그레이션

---

## v0.2.0 → v0.3.0 (서버 제거 → 순수 SDK)

| 항목 | v0.2.0 | v0.3.0 |
| --- | --- | --- |
| 설치 경로 | `packages/llm-manager/sdk` | **`packages/llm-manager`** (루트가 곧 패키지) |
| Service 실행 | FastAPI(8101) 필수 | **불필요 — 서버 제거** |
| 환경변수 | `LLM_REQUEST_URL` | 불필요 (handler URL 하드코딩, 필요 시 개별 override) |
| 예외 | `ServiceError` / `ServiceConnectionError` | 구체 예외(`FileResolveError`·`CacheError` 등) + `HandlerError` 직접 전파 ([11. 에러 처리](#11-에러-처리)) |
| `close()` | httpx 풀 정리 | no-op (그대로 둬도 안전) |
| **공개 API 시그니처·호출** | — | **불변** (`llm_request_async`, `stored_files`, `meta.*` 그대로) |

> 핵심: **호출 코드는 거의 그대로**다. 바뀌는 건 (1) 설치 경로, (2) 서버 안 띄움, (3) `except ServiceError` 쓰던 곳을 구체 예외로 교체. `ServiceError` 를 import 하던 코드만 수정 필요.

# 13. 주의사항

---

### 스트림은 이터레이터를 끝까지 소비해야 저장됨

`llm_request_stream` / `llm_request_stream_async`는 모든 청크를 순회한 뒤 MDB에 저장한다.
중간에 `break`하거나 이터레이터를 버리면 저장이 실행되지 않는다. 두 함수는 `(stream, meta)` 튜플을 반환한다 — meta 는 스트림 시작 전 바로 확인 가능 (`cache_hit`, `cache_key` 등).

```python
stream, meta = await llm_request_stream_async(...)
print(meta["cache_hit"])            # 스트림 시작 전 확인 가능
async for chunk in stream:
    if some_condition:
        break  # ⚠️ 저장 안 됨
```

---

### `ttl_seconds`는 `expires_at` 필드만 기록 — 자동 삭제는 MongoDB TTL 인덱스 필요

`ttl_seconds` 지정 시 문서에 `expires_at` 필드만 기록된다.
자동 삭제를 원하면 MDB 측에 TTL 인덱스(`expires_at` 대상)가 설정되어 있어야 한다.

---

### 파일명도 캐시 키에 영향

파일의 캐시 키는 `file_name + file_mtime + file_size` 체크섬 기반이다.
세 값 중 하나라도 다르면 다른 체크섬 → 다른 캐시 키가 된다.
같은 내용의 파일이라도 다른 이름으로 다시 보내면 cache miss.

---

### 브라우저 업로드 파일은 mtime 추출에 주의

로컬 경로가 없는 경우(프론트엔드에서 업로드되는 에이전트 등) Python `Path.stat().st_mtime`을 쓸 수 없으므로, **프론트엔드에서 `File.lastModified` 값을 백엔드로 함께 전달**해야 한다. JavaScript `File` 객체의 `lastModified`는 밀리초 단위이므로 초 단위로 변환해서 넘긴다.

```js
// frontend (JS)
const mtime = Math.floor(file.lastModified / 1000);
// 업로드 폼 / JSON 등에 file_mtime 필드로 같이 전달
```

서버 수신 시점의 현재 시각(`time.time()` 등)을 `file_mtime`으로 대신 쓰면, 동일 파일을 다시 업로드할 때마다 `file_mtime`이 달라져 **cache miss가 반복**된다.

---

### 동시 요청 시 `standby_ttl_seconds` 조정

같은 입력의 동시 요청은 Redis standby 메커니즘으로 중복 LLM 호출을 방지한다.
**단 `save_mode=True` 일 때만이다** — 대기자가 받아가는 것이 리더의 MDB 저장분이라, 저장하지 않는
조합(`save_mode=False`)에서는 standby 를 걸지 않고 각 요청이 곧장 LLM 을 부른다 (`docs/35`).

- 첫 요청이 `standby_ttl_seconds` 내에 완료되면 후속 요청은 자동으로 캐시 결과를 사용
- 시간 내 완료되지 않으면 후속 요청이 별도 LLM 호출 시작 (두 응답이 다를 수 있음)
- 작업 소요 시간에 맞게 조정 권장: 짧은 작업 120~300초 / 대용량 + 무거운 모델 900~1200초

---

### 개발/테스트 시 `kw_ai_dev` 사용

```python
# 저장 없이 LLM만 호출
response, meta = await llm_request_async(
    {"contents": "테스트", "model": "gemini-2.5-flash"},
    cache_mode=False,
    save_mode=False,
)

# 또는 dev DB에 저장
response, meta = await llm_request_async(..., database="kw_ai_dev")
```

---

### `close()` 는 이제 no-op

0.2.0 까지는 SDK 가 httpx `AsyncClient` 풀을 소유해 `close()` 로 정리해야 했다. 서버 제거(0.3.0)로 SDK 가 소유한 커넥션이 없어져 **`close()` 는 하위호환 no-op** 이다. 기존 코드의 `await close()` 호출은 그대로 둬도 안전(무동작)하다. handler 호출의 커넥션은 각 handler SDK 가 자체 관리한다.


# 14. 향후 계획

---

## Files API 20GB 한도 관리

Gemini Files API는 계정당 20GB 저장 한도가 있다.
현재는 한도 초과 시 별도 처리 없이 업로드 오류로 전파된다.
`llm-handler`에 `delete_file()` SDK가 노출되면 한도 초과 시 오래된 파일 자동 삭제 로직을 구현할 예정.

## 에이전트별 균등 호출 조절

여러 에이전트가 동시에 실행될 때 특정 에이전트가 LLM 호출을 독점하지 않도록
에이전트 단위의 quota / rate limit을 두는 방식을 검토 중이다.
호출량 제어는 공용 서버인 `llm-handler` 레이어의 책임이며, 거기서 동시 호출 수 제한을 두는 방향이다.





# 15. handler 호출 primitive (공개 표면)

---

handler 클라이언트는 `llm_manager._internal.handler_client` 로 **내부화**되어 있고, `llm_manager` 는 그중 **생성 primitive 만 큐레이션해 노출**한다. 캐시/파일 생명주기 ops(`create_cache`/`upload_file`/`configure`/...)는 **매니저의 결정 역할**이라 공개하지 않는다 — raw 노출 시 매니저의 Redis 추적·keying·grace 만료와 desync 되기 때문. (캐시 생성은 `llm_request_async(timings={"context_cache_ttl": ...})`, 만료/연장은 `delete_context_cache(...)` / `update_context_cache(..., ttl=...)` 같은 매니저 API 로.) handler URL 주입 등 설정은 첫 호출 시 `_internal/_config.py` 가 자동(lazy) 처리한다.

## sync — flat import

```python
from llm_manager import call, stream, embed, session, Part, LLMResponse

response = call("안녕하세요")
print(response.text)

for chunk in stream("질문", model="gemini-2.5-flash"):
    print(chunk.text or "", end="", flush=True)

result = embed("텍스트", model="text-embedding-004")
```

## async — `llm_manager.aio`

```python
from llm_manager.aio import call, stream, embed   # 또는: from llm_manager import aio; await aio.call(...)

response = await call("안녕하세요")

async for chunk in stream("질문", model="gemini-2.5-flash"):
    print(chunk.text or "", end="", flush=True)
```

## 공개 목록

| 분류 | 이름 |
| --- | --- |
| 생성 primitive | `call`, `stream`, `embed`, `session` |
| 클래스 | `Session` (sync) / `AsyncSession` (`llm_manager.aio`) |
| 모델 | `Part`, `LLMResponse`, `StreamChunk`, `FunctionCall`, `CacheRef`, `FileRef`, `TokenUsage`, `ContentsType`, `EmbedResponse`, `EmbeddingStatistics`, `EmbeddingValue` |
| 예외 | `HandlerError`, `HandlerConnectionError` |

> **비공개 (매니저 결정 역할)**: `create_cache`/`update_cache`/`get_cache`/`list_caches`/`upload_file`/`delete_file`/`get_file`/`list_files`/`configure`. 직접 raw 핸들러 제어가 필요하면 매니저 API 를 쓰거나 별도 논의.
