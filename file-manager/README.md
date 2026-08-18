# File Manager (client)

> 파일 I/O + MongoDB 캐시·메타데이터 오케스트레이션 SDK 패키지. import name: `file_manager`.

file-handler(순수 파일 I/O, :8600)와 mdb-handler(MongoDB)를 직접 호출하여 파일 저장·캐시 조회·메타데이터 관리를 조합합니다.
파일명 + 수정시간 + 크기 조합으로 캐싱하며, 중복 업로드를 자동으로 방지합니다.
에이전트 개발자는 4개 메서드만 호출하면 저장소 경로·MongoDB 메타데이터·MIME 타입 추출을 모두 위임할 수 있습니다.

- **이 패키지** (`file_manager`): import해서 쓰는 오케스트레이션 SDK
- **파일 I/O 서버** (`../file-handler`): 띄우는 FastAPI 서버 (:8600) — [file-handler/CLAUDE.md](../file-handler/CLAUDE.md) 참고

## 요구사항

- Python >= 3.10
- httpx >= 0.27.0
- `file-handler` 서버 실행 중

## 설치

```bash
pip install -e packages/file-manager
```

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `FILE_HANDLER_URL` | `http://192.7.9.45:8600` | File Handler Service URL |
| `SERVICE_NAME` | (없음) | 파일을 등록하는 **호출 서비스** 식별자. `upload_file`이 저장하는 `file_cache` 메타에 기록된다. 미설정이면 `FMConfigError` |

**등록 경로(`register_or_get`·`register_from_*`·`upload_file`)는 `SERVICE_NAME`을 요구하고, 순수 조회(`check_cache`·`get_file`·`get_metadata`)는 없어도 동작한다.** `register_or_get`은 캐시 히트라 업로드를 타지 않는 경우에도 **진입부에서** 검사한다 — 업로드 안에만 가드가 있으면 히트가 나는 동안 설정 누락이 조용히 지나가고, 처음 보는 파일이 들어오는 순간 요청 도중에 터진다.

`SERVICE_NAME`은 file-manager가 아니라 **SDK를 import한 서비스 프로세스**의 환경변수다. ETF 컨테이너에서 부르면 `kiwoom-etf`, ESG에서 부르면 `kiwoom-esg`가 기록된다(각 서비스 `docker-compose.yml`의 `environment:`). 출처·강제 방식은 llm-manager의 `require_service_name`과 같다.

mdb-manager의 `X-Caller-Service`와 혼동하지 말 것 — 그쪽은 SDK 정체성이라 항상 `file-manager`로 고정 주입되고, `SERVICE_NAME`은 **등록 주체**를 가리킨다.

## 디렉터리 구조

```
packages/file-manager/          # 이 클라이언트 패키지
├── pyproject.toml
├── file_manager/
│   ├── __init__.py             # 공개 API (FileManagerHandler, 예외)
│   ├── _client.py              # httpx 기반 AsyncClient (내부용)
│   ├── handler.py              # FileManagerHandler (Singleton, async 진입점)
│   └── exceptions.py           # FMError, FMConnectionError, FMServiceError
└── tests/

packages/file-handler/          # 서버 — 별도 패키지
└── service/                    # FastAPI 서버 (:8600)
```

## 데이터 모델

SDK가 다루는 메타데이터는 세 단계로 늘어납니다.

| 단계 | 필드 수 | 필드 | 생성 주체 |
|------|---------|------|-----------|
| 입력 | 3 | `file_name`, `file_mtime`, `file_size` | 에이전트 |
| `build_metadata` 후 | 5 | + `mime_type`, `extension` | SDK (로컬 추출) |
| `upload_file` 후 | 6 | + `file_path` | file-handler (UUID 생성) |

- `file_name` + `file_mtime` + `file_size` 3개가 **캐시 키**
- `file_path`는 `UUID.확장자` 형식의 저장 경로 (원본 파일명과 별개)

---

## ⚠️ 반드시 확인 — Frontend에서 파일을 받는 경우

**`file_mtime`을 Frontend에서 명시적으로 넘겨줘야 합니다.**
HTTP multipart는 파일 수정시간을 자동 전달하지 않습니다.
빠뜨리면 **같은 파일이 매 업로드마다 새 레코드로 쌓여 캐시가 무의미해집니다.**

```js
// Frontend (브라우저)
const formData = new FormData();
formData.append("file", file);
formData.append("file_name", file.name);
formData.append("file_mtime", Math.floor(file.lastModified / 1000));  // ms → s 변환 필수
formData.append("file_size", file.size);
// 에이전트 Backend로 전송
```

```python
# 에이전트 Backend (FastAPI 수신 예시)
@app.post("/upload")
async def upload(
    file: UploadFile,
    file_name: str = Form(...),
    file_mtime: int = Form(...),   # ← Frontend가 보낸 값을 그대로 받음
    file_size: int = Form(...),
):
    data = await file.read()
    metadata = fm.build_metadata(file_name, file_mtime, file_size)
    # ... check_cache / upload_file ...
```

> 💡 서버 파일시스템에서 읽는 경로(배치/크롤러 등)라면 `os.path.getmtime(path)`으로 자동 획득하면 됩니다. 이 경고는 **브라우저 업로드** 경로에만 해당됩니다.

---

## Quick Start

```python
import asyncio
from file_manager import FileManagerHandler


async def main():
    fm = FileManagerHandler()

    file_name = "report.xlsx"
    file_mtime = 1713000000        # epoch seconds
    file_size = len(file_data)     # bytes

    # 1) 5필드 메타데이터 구성 (로컬 계산, HTTP 없음)
    metadata = fm.build_metadata(file_name, file_mtime, file_size)

    # 2) 캐시 체크
    result = await fm.check_cache(metadata)

    # 3) 히트 → file_path 재사용 / 미스 → 업로드
    if result["hit"]:
        metadata["file_path"] = result["file_path"]
    else:
        metadata["file_path"] = await fm.upload_file(file_data, metadata)

    # 4) 필요할 때 파일 + 6필드 메타데이터 꺼내기
    file_bytes, stored_metadata = await fm.get_file(metadata["file_path"])

    await fm.close()


asyncio.run(main())
```

## API Reference

### FileManagerHandler

Singleton 패턴의 async 진입점입니다. `build_metadata`만 동기, 나머지는 async.

```python
FileManagerHandler()   # 인자 없음. 환경변수 FILE_HANDLER_URL로 대상 지정
```

#### 메타데이터

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `build_metadata(file_name, file_mtime, file_size)` | `dict` (5필드) | 로컬에서 `mime_type`/`extension` 추출해 메타데이터 구성 (동기) |

#### 캐시 / 업로드

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `await check_cache(metadata)` | `dict` | `{"hit": bool, "file_path": str \| None}` — 캐시 키 3개(`file_name`/`file_mtime`/`file_size`)로 mdb-handler 직접 조회 |
| `await upload_file(file_data, metadata)` | `str` | file-handler에 파일 저장 + mdb-handler에 MongoDB 레코드 생성. file-handler가 생성한 `file_path`를 반환 |
| `await register_or_get(bytes_data, metadata)` | `dict` (6필드) | `check_cache` + `upload_file` 합성 — miss 면 upload, hit 이면 skip. metadata + `{file_path}` 반환 |

#### 편의 메서드 (이슈 #81)

`build_metadata`+`register_or_get` 조합과 임시파일·mtime 처리를 소비처가 매번 재구현하던 보일러플레이트를 흡수합니다.

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `await register_from_bytes(data, *, file_name, file_mtime)` | `dict` (6필드) | `build_metadata`+`register_or_get`을 한 줄로. `file_size`는 `len(data)`로 내부 계산 |
| `await register_from_path(path)` | `dict` (6필드) | 경로에서 `file_name`·`file_mtime` 도출 후 등록. 바이트 읽기는 `asyncio.to_thread`로 off-loop. 경로 없으면 `FileNotFoundError` 전파 |
| `async with materialize_to_tempfile(data, *, suffix="")` | `Path` (context manager) | 바이트를 임시파일로 떨궈 `Path`를 yield, 블록 종료 시(정상·예외 모두) `unlink` |
| `normalize_mtime(value)` (staticmethod) | `int` | int·float·숫자 문자열(소수점 포함)·ISO 8601을 int epoch seconds로 통일. 10^12 이상은 밀리초로 보고 초 환산, 실패 시 0 |

#### 조회

`get_file`은 바이트까지, `get_metadata`는 메타만 가져옵니다. **메타만 필요하면 후자를 쓰세요** — 파일 전체를 받았다가 버리지 않습니다 (이슈 #97).

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `await get_file(file_path)` | `tuple[bytes, dict]` | `(파일 바이너리, 6필드 메타데이터)` — 메타는 서버의 MongoDB에서 가져온 권위 버전. 저장 문서에 그 밖의 필드(`service_name` 등)가 있어도 반환에 싣지 않는다 |
| `await get_metadata(file_path)` | `dict` (6필드) | 메타데이터만 조회 — **파일 바이트는 받지 않는다**(mdb-handler만 호출). 반환은 `get_file`의 두 번째 요소와 동일 |

> 💡 없는 `file_path`면 두 메서드 모두 `MetadataNotFoundError`입니다. 다만 `get_metadata`는 file-handler를 부르지 않으므로 **메타는 있는데 바이트가 사라진 파일**을 감지하지 못하고 정상 dict를 돌려줍니다 — 그 경우 `get_file`이 내는 `FMServiceError`(404)가 여기선 나지 않습니다.

#### 생명주기

| 메서드 | 설명 |
|--------|------|
| `await close()` | HTTP 클라이언트 종료 |
| `reset()` (classmethod) | Singleton 인스턴스 초기화 (테스트용) |

### 예외

```
FMError                     # 기본 에러 (message, status_code)
├── FMConnectionError       # FM Service 연결 실패 (status_code=0)
├── FMServiceError          # FM Service 에러 응답 (HTTP 4xx/5xx)
├── FMConfigError           # 설정 누락 (예: 업로드 시 SERVICE_NAME 부재)
└── MetadataNotFoundError   # file_path 에 해당하는 메타가 없음 (get_file·get_metadata)
```

`FMConfigError`는 재시도로 복구되지 않는 배포 설정 실수다 — 잡아서 폴백하지 말고 터뜨려 고치는 쪽이 맞다.

```python
from file_manager import FMConnectionError, FMServiceError

try:
    result = await fm.check_cache(metadata)
except FMConnectionError:
    # 네트워크 이슈: 재시도 또는 폴백
    ...
except FMServiceError as e:
    # 서비스 응답 에러: e.status_code로 분기 가능
    if e.status_code == 404:
        ...
    else:
        raise
```

## 사용 패턴

### 에이전트 백엔드 (FastAPI) 통합

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from file_manager import FileManagerHandler


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.fm = FileManagerHandler()
    try:
        yield
    finally:
        await app.state.fm.close()


app = FastAPI(lifespan=lifespan)


@app.post("/documents")
async def ingest(file_name: str, file_mtime: int, file_data: bytes):
    fm = app.state.fm
    metadata = fm.build_metadata(file_name, file_mtime, len(file_data))
    result = await fm.check_cache(metadata)
    if result["hit"]:
        metadata["file_path"] = result["file_path"]
    else:
        metadata["file_path"] = await fm.upload_file(file_data, metadata)
    return metadata
```

### 파일이 필요한 시점에만 회수

업로드/캐시 단계에서는 바이너리를 전달받지 않고 `file_path`만 주고받다가, 실제로 파일 내용이 필요해질 때 `get_file`로 꺼냅니다. 네트워크 왕복 비용을 최소화하기 위함.

```python
# 에이전트 파이프라인 전반부 — file_path만 전달
metadata["file_path"] = result["file_path"] or await fm.upload_file(...)

# ...여러 단계 처리...

# 후반부에서 실제 바이너리 필요 시
file_bytes, stored_metadata = await fm.get_file(metadata["file_path"])
```

### `register_or_get` — `check_cache` + `upload_file` 합성

`check_cache → miss 면 upload_file` 패턴을 한 줄로 처리하는 헬퍼. 결과 6 필드 dict 를 그대로 외부 SDK 의 Pydantic 모델에 `**` unpack 해서 사용 가능 (변환 없음).

```python
metadata = fm.build_metadata(file_name="report.pdf", file_mtime=..., file_size=...)
result = await fm.register_or_get(file_data, metadata)
# {"file_name", "file_size", "file_mtime", "mime_type", "extension", "file_path"} (6 필드)
```

같은 파일에 여러 작업이 필요할 때 `result` 를 자기 DB 에 저장 후 재사용하면 bytes 재전송 / 재업로드 없이 `file_path` 로만 작업 가능.

### 편의 메서드 — before / after (이슈 #81)

각 서비스 repo에 흩어져 재구현되던 패턴을 SDK 메서드로 흡수합니다.

**바이트 등록** — `build_metadata`+`register_or_get` 2줄을 한 줄로:

```python
# before
metadata = fm.build_metadata(file_name, file_mtime, len(data))
result = await fm.register_or_get(data, metadata)

# after
result = await fm.register_from_bytes(data, file_name=file_name, file_mtime=file_mtime)
```

**경로 등록** — `stat()`·`int(mtime)`·off-loop 읽기 조합을 한 줄로:

```python
# before
stat = path.stat()
metadata = fm.build_metadata(path.name, int(stat.st_mtime), stat.st_size)
data = await asyncio.to_thread(path.read_bytes)
result = await fm.register_or_get(data, metadata)

# after
result = await fm.register_from_path(path)
```

**임시파일화** — 경로를 요구하는 하류 추출기(pandas·PyMuPDF 등)에 넘길 때:

```python
# before
with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
    tf.write(pdf_bytes)
    tmp_path = Path(tf.name)
try:
    result = extractor(tmp_path)
finally:
    tmp_path.unlink(missing_ok=True)

# after
async with fm.materialize_to_tempfile(pdf_bytes, suffix=".pdf") as tmp_path:
    result = extractor(tmp_path)        # 종료 시 자동 정리 (예외 경로 포함)
```

**mtime 정규화** — 브라우저발 ISO 문자열·밀리초 epoch을 int 초로:

```python
# before: 소비처마다 변환 로직 재구현 (MN의 _to_int_mtime 등)
# after
file_mtime = FileManagerHandler.normalize_mtime(raw_mtime)   # "2026-01-01T09:00:00" / 1700000000000 → int 초
result = await fm.register_from_bytes(data, file_name=name, file_mtime=file_mtime)
```

## 주의사항

- **비동기 SDK**: `build_metadata`를 제외한 모든 메서드가 async입니다. 동기 환경에서는 `asyncio.run(...)`이나 thread pool로 감싸야 합니다.
- **Singleton**: `FileManagerHandler()`는 첫 호출 시 생성되며 이후 동일 인스턴스를 반환합니다. HTTP 커넥션 풀 공유를 위해 의도된 설계. 테스트에서 재생성이 필요하면 `FileManagerHandler.reset()` 후 다시 인스턴스화하세요.
- **`file_mtime`**: Frontend 업로드 경로에서는 반드시 명시 전달 필요 (상단 ⚠️ 섹션 참고). 서버 파일시스템은 `os.path.getmtime`으로 자동.
- **`mime_type`/`extension`는 SDK가 결정**: mdb-handler에 저장 시 SDK가 전달한 값을 그대로 기록합니다. 재추출 없음.
- **`check_cache`/`upload_file` 반환값은 `file_path`만**: 나머지 5필드는 로컬 `metadata` dict에 이미 있으므로, 에이전트가 병합해서 6필드 완전체를 구성합니다.
- **연결 종료**: 장기 실행 프로세스에서는 종료 시 `await fm.close()` 호출 권장.
- **파일 저장소 접근은 file-handler가 담당**: 파일 저장소 접근(로컬 FS, 향후 S3 등)은 `file-handler` 서버가 처리합니다. SDK 사용자는 저장소 형태를 알 필요가 없습니다. MongoDB 연동은 mdb-handler를 통해 SDK가 직접 처리합니다.
