"""SDK 응답 모델."""

from typing import Any

from pydantic import BaseModel


class DataFrameData(BaseModel):
    """query_to_dataframe 서버 응답."""

    columns: list[str]
    rows: list[list[Any]]
    dtypes: dict[str, str]
    request_id: str


class RowsData(BaseModel):
    """fetch_all 서버 응답."""

    rows: list[list[Any]]
    request_id: str


class DictRowsData(BaseModel):
    """fetch_all_dict 서버 응답."""

    rows: list[dict[str, Any]]
    request_id: str


class OneRowData(BaseModel):
    """fetch_one 서버 응답."""

    row: list[Any] | None
    request_id: str


class DmlResult(BaseModel):
    """DML 실행 결과."""

    rowcount: int
    request_id: str
