# File Manager SDK

> Kiwoom File Manager SDK — HTTP 클라이언트 기반 파일 저장/캐시/반환 라이브러리

File Manager Service(REST API)를 통해 파일을 저장·조회하는 Python SDK입니다.
파일명 + 수정시간 + 크기 조합으로 캐싱하며, 중복 업로드를 자동으로 방지합니다.
에이전트 개발자는 SDK의 4개 메서드만 호출하면 저장소 경로·MongoDB 메타데이터·MIME 타입 추출을 모두 위임할 수 있습니다.

## 요구사항

- Python >= 3.10
- httpx >= 0.27.0
- File Manager Service 실행 중

## 설치

```bash
pip install -e packages/file-manager/sdk
```

## 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `FILE_MANAGER_URL` | `http://localhost:8600` | File Manager Service URL |

## 디렉터리 구조

```
file_manager/
├── __init__.py            # 공개 API (FileManagerHandler, 예외)
├── _client.py             # httpx 기반 AsyncClient (내부용)
├── handler.py             # FileManagerHandler (Singleton, async 진입점)
└── exceptions.py          # FMError, FMConnectionError, FMServiceError
```

## 데이터 모델

SDK가 다루는 메타데이터는 세 단계로 늘어납니다.

| 단계 | 필드 수 | 필드 | 생성 주체 |
|------|---------|------|-----------|
| 입력 | 3 | `file_name`, `file_mtime`, `file_size` | 에이전트 |
| `build_metadata` 후 | 5 | + `mime_type`, `extension` | SDK (로컬 추출) |
| `upload_file` 후 | 6 | + `file_path` | FM Service (UUID 생성) |

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
FileManagerHandler()   # 인자 없음. 환경변수 FILE_MANAGER_URL로 대상 지정
```

#### 메타데이터

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `build_metadata(file_name, file_mtime, file_size)` | `dict` (5필드) | 로컬에서 `mime_type`/`extension` 추출해 메타데이터 구성 (동기) |

#### 캐시 / 업로드

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `await check_cache(metadata)` | `dict` | `{"hit": bool, "file_path": str \| None}` — 캐시 키 3개(`file_name`/`file_mtime`/`file_size`)로 MongoDB 조회 |
| `await upload_file(file_data, metadata)` | `str` | 파일 저장 + MongoDB 레코드 생성. FM Service가 생성한 `file_path` 반환 |

#### 조회

| 메서드 | 반환값 | 설명 |
|--------|--------|------|
| `await get_file(file_path)` | `tuple[bytes, dict]` | `(파일 바이너리, 6필드 메타데이터)` — 메타는 서버의 MongoDB에서 가져온 권위 버전 |

#### 생명주기

| 메서드 | 설명 |
|--------|------|
| `await close()` | HTTP 클라이언트 종료 |
| `reset()` (classmethod) | Singleton 인스턴스 초기화 (테스트용) |

### 예외

```
FMError                     # 기본 에러 (message, status_code)
├── FMConnectionError       # FM Service 연결 실패 (status_code=0)
└── FMServiceError          # FM Service 에러 응답 (HTTP 4xx/5xx)
```

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

## 주의사항

- **비동기 SDK**: `build_metadata`를 제외한 모든 메서드가 async입니다. 동기 환경에서는 `asyncio.run(...)`이나 thread pool로 감싸야 합니다.
- **Singleton**: `FileManagerHandler()`는 첫 호출 시 생성되며 이후 동일 인스턴스를 반환합니다. HTTP 커넥션 풀 공유를 위해 의도된 설계. 테스트에서 재생성이 필요하면 `FileManagerHandler.reset()` 후 다시 인스턴스화하세요.
- **`file_mtime`**: Frontend 업로드 경로에서는 반드시 명시 전달 필요 (상단 ⚠️ 섹션 참고). 서버 파일시스템은 `os.path.getmtime`으로 자동.
- **`mime_type`/`extension`는 SDK가 결정**: FM Service는 SDK가 전달한 값을 그대로 저장합니다. 재추출 없음.
- **`check_cache`/`upload_file` 반환값은 `file_path`만**: 나머지 5필드는 로컬 `metadata` dict에 이미 있으므로, 에이전트가 병합해서 6필드 완전체를 구성합니다.
- **연결 종료**: 장기 실행 프로세스에서는 종료 시 `await fm.close()` 호출 권장.
- **SDK는 HTTP만, File Server I/O는 Service가 담당**: 파일 저장소 접근(로컬 FS, 향후 S3 등)은 FM Service와 `file_handler` 패키지가 처리합니다. SDK 사용자는 저장소 형태를 알 필요가 없습니다.
