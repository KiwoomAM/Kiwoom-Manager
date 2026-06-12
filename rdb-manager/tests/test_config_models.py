"""config 상수 잠금 값과 응답 모델 파싱 검증."""

from rdb_manager._config import HANDLER_BASE_URL
from rdb_manager.models import (
    DataFrameData,
    DictRowsData,
    DmlResult,
    OneRowData,
    RowsData,
)


def test_handler_base_url_is_8700():
    assert HANDLER_BASE_URL == "http://192.7.9.45:8700"


def test_dataframe_data_parses():
    data = DataFrameData.model_validate(
        {"columns": ["a"], "rows": [[1]], "dtypes": {"a": "int64"}, "request_id": "r"}
    )
    assert data.columns == ["a"]
    assert data.rows == [[1]]
    assert data.dtypes == {"a": "int64"}


def test_one_row_data_allows_null():
    data = OneRowData.model_validate({"row": None, "request_id": "r"})
    assert data.row is None


def test_dml_result_parses():
    assert DmlResult.model_validate({"rowcount": 3, "request_id": "r"}).rowcount == 3


def test_rows_and_dict_rows_parse():
    assert RowsData.model_validate({"rows": [[1]], "request_id": "r"}).rows == [[1]]
    assert DictRowsData.model_validate({"rows": [{"a": 1}], "request_id": "r"}).rows == [{"a": 1}]
