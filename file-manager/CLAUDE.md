# File Manager (client)

`file-handler` 서버용 **HTTP 클라이언트**. 소비자(예: llm-request)가 `pip install` 후 `import file_manager`로 사용한다.

- **import해서 쓰는 클라이언트** → 이 패키지 (`file_manager`)
- **띄우는 서버** → `../file-handler` (FastAPI, :8600)

## 설치 / 사용
```bash
pip install -e packages/file-manager
```
```python
from file_manager import FileManagerHandler
fm = FileManagerHandler()            # 서버 주소 env: FILE_MANAGER_URL (기본 http://localhost:8600)
metadata = fm.build_metadata(file_name, file_mtime, file_size)
result = await fm.check_cache(metadata)
metadata["file_path"] = result["file_path"] if result["hit"] else await fm.upload_file(data, metadata)
file_bytes, meta = await fm.get_file(metadata["file_path"])
```

## 메서드
`build_metadata`(동기, 로컬 mime/extension 추출) · `check_cache` · `upload_file` · `get_file` · `register_or_get`.
서버 엔드포인트/책임 상세는 [`../file-handler/CLAUDE.md`](../file-handler/CLAUDE.md) 참고.

## 예외
`FMError` · `FMConnectionError` · `FMServiceError` (from `file_manager`).
