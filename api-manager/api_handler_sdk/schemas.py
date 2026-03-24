# -*- coding: utf-8 -*-
"""
외부 API 도구 스키마 정의

API Handler가 제공하는 도구 목록과 파라미터 스키마를 정의합니다.
소비하는 쪽(챗봇, 에이전트 등)이 LLM에게 도구 목록을 제공할 때 사용합니다.
"""

from typing import List, Dict, Any

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "serper_search",
        "description": "최신 뉴스/웹 정보를 검색하여 요약하고 출처를 제시합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "q":        {"type": "string",  "description": "검색 키워드/문장"},
                "location": {"type": "string",  "description": "지역", "default": "Seoul, South Korea"},
                "gl":       {"type": "string",  "description": "Geo code", "default": "kr"},
                "hl":       {"type": "string",  "description": "Language", "default": "ko"},
                "num":      {"type": "integer", "description": "결과 개수", "default": 10},
            },
            "required": ["q"]
        }
    },
    {
        "name": "finnhub_price",
        "description": "특정 종목의 현재 주가와 당일 시세를 조회합니다. 기간별 과거 데이터는 제공하지 않습니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "예: 005930.KS, AAPL, NVDA"},
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "fred_series",
        "description": "금리/물가/CPI/GDP 등 거시 지표 시계열을 조회합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "series_id":  {"type": "string", "description": "예: FEDFUNDS, CPIAUCSL, GDPC1"},
                "start_date": {"type": "string", "description": "YYYY-MM-DD. 미지정 시 최근 90일 전부터 조회"},
                "end_date":   {"type": "string", "description": "YYYY-MM-DD. 미지정 시 오늘 날짜까지 조회"},
            },
            "required": ["series_id"]
        }
    },
    {
        "name": "openex_latest",
        "description": "USD 기준 환율(원/엔/유로 등)을 조회합니다.",
        "parameters": {
            "type": "object",
            "properties": {
                "base":    {"type": "string", "description": "기준통화", "default": "USD"},
                "symbols": {"type": "array",  "items": {"type": "string"}, "description": "상대통화 목록", "default": ["KRW", "JPY", "EUR"]},
            },
            "required": ["base"]
        }
    },
    {
        "name": "dart_list",
        "description": (
            "국내 기업 공시 목록을 조회합니다. "
            "기업명(한글) 또는 corp_code(8자리 숫자)로 조회 가능. "
            "지원 기업: 삼성전자, 현대차, SK하이닉스, 네이버, 카카오, "
            "LG에너지솔루션, 삼성바이오로직스, 삼성SDI, 기아, 포스코홀딩스, "
            "LG전자, 현대모비스, KB금융, 신한지주, 하나금융지주, 키움증권, "
            "키움투자자산운용, 셀트리온, 삼성물산 등"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "company_name": {"type": "string",  "description": "기업명(한글) 또는 corp_code(8자리 숫자)"},
                "bgn_de":       {"type": "string",  "description": "시작일 YYYYMMDD"},
                "end_de":       {"type": "string",  "description": "종료일 YYYYMMDD"},
                "page_no":      {"type": "integer", "description": "페이지 번호", "default": 1},
                "page_count":   {"type": "integer", "description": "페이지당 개수", "default": 10},
            },
            "required": ["company_name", "bgn_de", "end_de"]
        }
    },
]
