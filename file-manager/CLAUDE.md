# File Manager (client)

파일 I/O + MongoDB 캐시·메타데이터를 **오케스트레이션하는 SDK**. file-handler(파일 저장/반환)와 mdb-handler(캐시 조회/메타데이터 기록)를 직접 호출하여 조합한다. 소비자(예: llm-request)가 `pip install` 후 `import file_manager`로 사용한다.

- **import해서 쓰는 SDK** → 이 패키지 (`file_manager`)
- **파일 I/O 서버** → `../file-handler` (FastAPI, :8600) — 순수 파일 저장/반환만 담당
- **MongoDB 서버** → mdb-handler (mdb-manager SDK 경유, caller="file-manager" 주입)

## 설치 / 사용
```bash
pip install -e packages/file-manager
```
```python
from file_manager import FileManagerHandler
fm = FileManagerHandler()            # 서버 주소 env: FILE_HANDLER_URL (기본 http://192.7.9.45:8600)
metadata = fm.build_metadata(file_name, file_mtime, file_size)
result = await fm.check_cache(metadata)
metadata["file_path"] = result["file_path"] if result["hit"] else await fm.upload_file(data, metadata)
file_bytes, meta = await fm.get_file(metadata["file_path"])
```

## 메서드
`build_metadata`(동기, 로컬 mime/extension 추출) · `check_cache`(mdb-handler 직접 조회) · `upload_file`(file-handler 저장 + mdb 메타데이터 기록) · `get_file`(mdb 메타 조회 + file-handler 바이너리 반환) · `register_or_get`.

> `register_or_get`은 같은 `(file_name, file_mtime, file_size)` 동시 호출을 **Redis standby(SETNX)** 로 **프로세스·워커 넘어** 직렬화한다 — 첫 요청만 업로드, 나머지는 대기 후 캐시 재사용. Redis 미연결/타임아웃 시 직접 업로드로 degrade(가용성 우선, 드물게 중복 가능). 의존: `kiwoom-redis-manager`(redis-handler).

파이프라인 명세는 [`../file-handler/file-manager-pipeline.md`](../file-handler/file-manager-pipeline.md) 참고.

## 예외
`FMError` · `FMConnectionError` · `FMServiceError` (from `file_manager`).
