"""Session 멀티턴 테스트 -- 이중 히스토리 패턴."""

from unittest.mock import patch

import httpx

import llm_manager._internal.handler_client


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=httpx.Request("POST", "http://test"),
    )


def test_session_multiturn():
    """session -- 두 번 호출 시 curated 이력 누적."""
    responses = [
        _mock_response(
            200,
            {"text": "첫 번째 응답", "model": "gemini-3-flash-preview", "finish_reason": "STOP", "request_id": "s1"},
        ),
        _mock_response(
            200,
            {"text": "두 번째 응답", "model": "gemini-3-flash-preview", "finish_reason": "STOP", "request_id": "s2"},
        ),
    ]
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.side_effect = responses
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session(system="한국어")
        r1 = s.call("안녕")
        assert r1.text == "첫 번째 응답"
        r2 = s.call("이어서 대화")
        assert r2.text == "두 번째 응답"
    second_call_body = instance.request.call_args_list[1].kwargs["json"]
    messages = second_call_body["messages"]
    assert len(messages) == 3  # user1, model1, user2
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "안녕"
    assert messages[1]["role"] == "model"
    assert messages[1]["content"] == "첫 번째 응답"
    assert messages[2]["content"] == "이어서 대화"


def test_session_invalid_response_excluded_from_curated():
    """빈 응답 -> curated에서 제외, comprehensive에는 포함."""
    responses = [
        _mock_response(
            200,
            {"text": "", "model": "gemini-3-flash-preview", "finish_reason": "STOP", "request_id": "s-invalid"},
        ),
        _mock_response(
            200,
            {"text": "정상 응답", "model": "gemini-3-flash-preview", "finish_reason": "STOP", "request_id": "s-valid"},
        ),
    ]
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.side_effect = responses
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        s.call("첫 번째")
        s.call("두 번째")
    second_call_body = instance.request.call_args_list[1].kwargs["json"]
    messages = second_call_body["messages"]
    assert len(messages) == 1  # 현재 메시지만 (무효 턴 제외)
    assert messages[0]["content"] == "두 번째"
    assert len(s.get_history()) == 2  # 두 번째 유효 턴만
    assert len(s.get_full_history()) == 4  # 전체 (무효 포함)


def test_session_safety_finish_reason_is_invalid():
    """finish_reason=SAFETY -> curated에서 제외."""
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(
            200,
            {
                "text": "부분 응답",
                "model": "gemini-3-flash-preview",
                "finish_reason": "SAFETY",
                "request_id": "s-safety",
            },
        )
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        s.call("test")
    assert len(s.get_history()) == 0  # curated에 없음 (SAFETY)
    assert len(s.get_full_history()) == 2  # comprehensive에 있음


def test_session_whitespace_response_is_invalid():
    """공백만 있는 응답도 무효 처리."""
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(
            200,
            {
                "text": "   ",
                "model": "gemini-3-flash-preview",
                "finish_reason": "STOP",
                "request_id": "s-ws",
            },
        )
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        s.call("test")
    assert len(s.get_history()) == 0
    assert len(s.get_full_history()) == 2


def test_session_system_instruction():
    """session -- system이 매 요청에 전달."""
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(
            200,
            {
                "text": "ok",
                "model": "gemini-3-flash-preview",
                "finish_reason": "STOP",
                "request_id": "s3",
            },
        )
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session(system="금융 분석가", model="gemini-3-pro-preview")
        s.call("분석해줘")
    body = instance.request.call_args.kwargs["json"]
    assert body["system_instruction"] == "금융 분석가"
    assert body["model"] == "gemini-3-pro-preview"


def test_session_get_history():
    """session.get_history() -- curated 이력 조회."""
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(
            200,
            {
                "text": "응답1",
                "model": "gemini-3-flash-preview",
                "finish_reason": "STOP",
                "request_id": "s4",
            },
        )
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        assert s.get_history() == []
        s.call("안녕")
        history = s.get_history()
        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "안녕"}
        assert history[1] == {"role": "model", "content": "응답1"}


def test_session_clear():
    """session.clear() -- 양쪽 이력 모두 초기화."""
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.return_value = _mock_response(
            200,
            {
                "text": "응답",
                "model": "gemini-3-flash-preview",
                "finish_reason": "STOP",
                "request_id": "s5",
            },
        )
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        s.call("안녕")
        assert len(s.get_history()) == 2
        assert len(s.get_full_history()) == 2
        s.clear()
        assert s.get_history() == []
        assert s.get_full_history() == []


def test_session_recovery_after_invalid():
    """무효 턴 후 유효 턴 -> curated가 정상 복구."""
    responses = [
        _mock_response(
            200,
            {"text": "정상1", "model": "gemini-3-flash-preview", "finish_reason": "STOP", "request_id": "r1"},
        ),
        _mock_response(
            200,
            {"text": "", "model": "gemini-3-flash-preview", "finish_reason": "STOP", "request_id": "r2"},
        ),
        _mock_response(
            200,
            {"text": "정상3", "model": "gemini-3-flash-preview", "finish_reason": "STOP", "request_id": "r3"},
        ),
    ]
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.side_effect = responses
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        s.call("첫 번째")
        s.call("두 번째")
        s.call("세 번째")
    third_call_body = instance.request.call_args_list[2].kwargs["json"]
    messages = third_call_body["messages"]
    assert len(messages) == 3  # user1, model1, user3
    assert messages[0]["content"] == "첫 번째"
    assert messages[1]["content"] == "정상1"
    assert messages[2]["content"] == "세 번째"
    assert len(s.get_history()) == 4  # 유효 턴 2개 = 4 메시지
    assert len(s.get_full_history()) == 6  # 전체 턴 3개 = 6 메시지


def test_session_round_trip_appends_assistant_message():
    """Session 이 LLMResponse.assistant_message 를 model_msg 로 통째 echo 하는지 검증."""
    assistant_message = {
        "role": "model",
        "content": [
            {
                "type": "function_call",
                "function_call": {
                    "name": "get_stock_price",
                    "args": {"ticker": "005930"},
                    "id": "fc_abc",
                    "thought_signature": "dHM=",
                },
            }
        ],
    }
    responses = [
        _mock_response(
            200,
            {
                "model": "gemini-3-flash-preview",
                "finish_reason": "STOP",
                "request_id": "s1",
                "function_calls": [{"name": "get_stock_price", "args": {"ticker": "005930"}, "id": "fc_abc"}],
                "assistant_message": assistant_message,
            },
        ),
        _mock_response(
            200,
            {"text": "87,500원", "model": "gemini-3-flash-preview", "finish_reason": "STOP", "request_id": "s2"},
        ),
    ]
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.side_effect = responses
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        s.call("삼성전자 주가 알려줘")
        s.call("계속")
    second_body = instance.request.call_args_list[1].kwargs["json"]
    messages = second_body["messages"]
    # user1, model1 (echo of assistant_message), user2
    assert messages[1]["role"] == "model"
    assert isinstance(messages[1]["content"], list)
    assert messages[1]["content"][0]["type"] == "function_call"
    assert messages[1]["content"][0]["function_call"]["thought_signature"] == "dHM="


def test_session_send_tool_results():
    """send_tool_results: tool 메시지 추가 + 다음 응답 받기."""
    responses = [
        _mock_response(
            200,
            {
                "model": "gemini-3-flash-preview",
                "finish_reason": "STOP",
                "request_id": "s1",
                "function_calls": [{"name": "get_stock_price", "args": {"ticker": "005930"}, "id": "fc_abc"}],
                "assistant_message": {
                    "role": "model",
                    "content": [
                        {
                            "type": "function_call",
                            "function_call": {
                                "name": "get_stock_price",
                                "args": {"ticker": "005930"},
                                "id": "fc_abc",
                            },
                        }
                    ],
                },
            },
        ),
        _mock_response(
            200,
            {"text": "87,500원", "model": "gemini-3-flash-preview", "finish_reason": "STOP", "request_id": "s2"},
        ),
    ]
    tools = [
        {
            "name": "get_stock_price",
            "description": "주가 조회",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        }
    ]
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.side_effect = responses
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        r1 = s.call("주가 알려줘", tools=tools)
        assert r1.function_calls is not None
        # Turn 2 — tools 같은 정의 다시 전달 (안전망 통과)
        r2 = s.send_tool_results(
            [("fc_abc", "get_stock_price", {"price_krw": 87500})],
            tools=tools,
        )
        assert r2.text == "87,500원"

    second_body = instance.request.call_args_list[1].kwargs["json"]
    messages = second_body["messages"]
    # user1, model1, tool 추가됨
    assert messages[-1]["role"] == "tool"
    tool_part = messages[-1]["content"][0]
    assert tool_part["type"] == "function_response"
    assert tool_part["function_response"]["name"] == "get_stock_price"
    assert tool_part["function_response"]["id"] == "fc_abc"
    assert tool_part["function_response"]["response"] == {"price_krw": 87500}
    # tools 가 Turn 2 wire 에 포함됐는지
    assert second_body["tools"] == tools


def test_session_send_tool_results_without_tools_raises():
    """직전 model turn 에 function_call 있는데 tools 없이 send_tool_results 호출 → ValueError."""
    import pytest

    responses = [
        _mock_response(
            200,
            {
                "model": "gemini-3-flash-preview",
                "finish_reason": "STOP",
                "request_id": "s1",
                "function_calls": [{"name": "get_stock_price", "args": {"ticker": "005930"}, "id": "fc_abc"}],
                "assistant_message": {
                    "role": "model",
                    "content": [
                        {
                            "type": "function_call",
                            "function_call": {
                                "name": "get_stock_price",
                                "args": {"ticker": "005930"},
                                "id": "fc_abc",
                            },
                        }
                    ],
                },
            },
        ),
    ]
    tools = [
        {
            "name": "get_stock_price",
            "description": "주가 조회",
            "parameters": {"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
        }
    ]
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.side_effect = responses
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        s.call("주가 알려줘", tools=tools)
        with pytest.raises(ValueError, match="`tools=` 가 필요합니다"):
            s.send_tool_results([("fc_abc", "get_stock_price", {"price_krw": 87500})])  # tools 누락


# =============================================================================
# stream → 다음 turn 으로 assistant_message (ts) 보존
# =============================================================================


class _MockStreamResponse:
    """SyncClient.stream() 에서 iter_lines 흉내 — done event payload 포함."""

    status_code = 200

    def __init__(self, sse_lines: list[str]) -> None:
        self._lines = sse_lines

    def iter_lines(self):
        yield from self._lines

    def iter_bytes(self):
        return iter([])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def test_session_stream_records_assistant_message_with_ts():
    """Session.stream() 후 _curated_history 의 model_msg 가 ts 포함된 parts list 로 echo."""
    import json as _json

    done = {
        "event": "done",
        "request_id": "r-1",
        "assistant_message": {
            "role": "model",
            "content": [
                {
                    "type": "function_call",
                    "function_call": {
                        "name": "get_stock_price",
                        "args": {"t": "005930"},
                        "id": "fc_x",
                        "thought_signature": "TS_FC",
                    },
                }
            ],
        },
    }
    fc_chunk = _json.dumps(
        {
            "function_calls": [
                {"name": "get_stock_price", "args": {"t": "005930"}, "id": "fc_x", "thought_signature": "TS_FC"}
            ]
        }
    )
    sse_lines = [
        f"data: {fc_chunk}",
        "",
        f"data: {_json.dumps(done, ensure_ascii=False)}",
        "",
    ]

    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.stream.return_value = _MockStreamResponse(sse_lines)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        list(s.stream("주가 알려줘"))

        history = s.get_history()

    assert len(history) == 2  # user + model
    model_msg = history[1]
    assert model_msg["role"] == "model"
    # text fallback (str) 이 아닌 parts list 로 echo
    assert isinstance(model_msg["content"], list)
    assert model_msg["content"][0]["type"] == "function_call"
    assert model_msg["content"][0]["function_call"]["thought_signature"] == "TS_FC"


def test_session_stream_then_send_tool_results_wires_ts():
    """stream → send_tool_results round-trip: Turn 2 wire 에 Turn 1 의 fc + ts 가 보존."""
    import json as _json

    done = {
        "event": "done",
        "request_id": "r-1",
        "assistant_message": {
            "role": "model",
            "content": [
                {
                    "type": "function_call",
                    "function_call": {
                        "name": "get_weather",
                        "args": {"city": "Seoul"},
                        "id": "fc_w",
                        "thought_signature": "TS_W",
                    },
                }
            ],
        },
    }
    fc_chunk = _json.dumps(
        {
            "function_calls": [
                {"name": "get_weather", "args": {"city": "Seoul"}, "id": "fc_w", "thought_signature": "TS_W"}
            ]
        }
    )
    stream_sse = [
        f"data: {fc_chunk}",
        "",
        f"data: {_json.dumps(done, ensure_ascii=False)}",
        "",
    ]
    turn2_response = _mock_response(
        200,
        {"text": "맑음", "model": "gemini-3-flash-preview", "finish_reason": "STOP", "request_id": "s2"},
    )
    tools = [
        {
            "name": "get_weather",
            "description": "날씨",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        }
    ]

    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.stream.return_value = _MockStreamResponse(stream_sse)
        instance.request.return_value = turn2_response
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        list(s.stream("서울 날씨", tools=tools))
        r2 = s.send_tool_results([("fc_w", "get_weather", {"weather": "sunny"})], tools=tools)
        assert r2.text == "맑음"

    turn2_body = instance.request.call_args.kwargs["json"]
    messages = turn2_body["messages"]
    # user, model (fc+ts), tool
    assert len(messages) == 3
    model_msg = messages[1]
    assert model_msg["role"] == "model"
    assert model_msg["content"][0]["function_call"]["thought_signature"] == "TS_W"


def test_session_stream_backwards_compat_no_assistant_message_in_done():
    """기존 handler (done event 에 assistant_message 없음) → text fallback 유지 (regression 0)."""
    import json as _json

    done = {"event": "done", "request_id": "r-old"}
    sse_lines = [
        'data: {"text": "안녕"}',
        "",
        'data: {"finish_reason": "STOP"}',
        "",
        f"data: {_json.dumps(done)}",
        "",
    ]

    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.stream.return_value = _MockStreamResponse(sse_lines)
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        list(s.stream("hi"))

        history = s.get_history()

    model_msg = history[1]
    # 기존 handler 시나리오: text fallback 으로 str 저장
    assert model_msg["content"] == "안녕"


# ── send_tool_results history rollback 회귀 ──


def _turn1_response_with_fc() -> httpx.Response:
    """Turn 1: function_call 포함 model 응답 — send_tool_results 사전 상태 세팅용."""
    return _mock_response(
        200,
        {
            "model": "gemini-3-flash-preview",
            "finish_reason": "STOP",
            "request_id": "s1",
            "function_calls": [{"name": "get_stock_price", "args": {"ticker": "005930"}, "id": "fc_abc"}],
            "assistant_message": {
                "role": "model",
                "content": [
                    {
                        "type": "function_call",
                        "function_call": {
                            "name": "get_stock_price",
                            "args": {"ticker": "005930"},
                            "id": "fc_abc",
                        },
                    }
                ],
            },
        },
    )


def _tools_def() -> list[dict]:
    return [
        {
            "name": "get_stock_price",
            "description": "주가 조회",
            "parameters": {
                "type": "object",
                "properties": {"ticker": {"type": "string"}},
                "required": ["ticker"],
            },
        }
    ]


def test_session_send_tool_results_rollback_on_handler_error():
    """client.call 이 HandlerError 던지면 send_tool_results 가 append 한 tool_msg 가 rollback 돼야 한다.

    rollback 안 하면 orphan tool message (model 응답 없이 tool 메시지로 끝남) 가 history 에 남아
    다음 호출에서 Gemini 빈응답 / 400 으로 연쇄 실패.
    """
    import pytest

    from llm_manager._internal.handler_client.exceptions import HandlerError

    tools = _tools_def()
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        # Turn 1 정상, Turn 2 (send_tool_results) 에서 502 provider error
        instance.request.side_effect = [
            _turn1_response_with_fc(),
            _mock_response(502, {"error": "Gemini 빈 응답", "error_type": "provider"}),
        ]
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        s.call("주가 알려줘", tools=tools)  # Turn 1 — history 에 user + model_msg(fc) 누적

        history_before = s.get_history()
        full_before = s.get_full_history()

        with pytest.raises(HandlerError) as exc_info:
            s.send_tool_results([("fc_abc", "get_stock_price", {"price_krw": 87500})], tools=tools)
        assert exc_info.value.status_code == 502

        # rollback 검증 — tool_msg 가 양쪽 history 에서 모두 제거됐어야 함
        assert s.get_history() == history_before, "curated_history 가 rollback 안 됨 — orphan tool_msg 남음"
        assert s.get_full_history() == full_before, "comprehensive_history 가 rollback 안 됨"


def test_session_send_tool_results_retry_after_rollback_succeeds():
    """1차 호출이 실패 → rollback → 같은 results 로 재시도 시 깨끗한 history 로 정상 완료."""
    tools = _tools_def()
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        instance.request.side_effect = [
            _turn1_response_with_fc(),
            # 첫 send_tool_results: 502 실패
            _mock_response(502, {"error": "Gemini 빈 응답", "error_type": "provider"}),
            # 재시도: 성공
            _mock_response(
                200,
                {"text": "87,500원", "model": "gemini-3-flash-preview", "finish_reason": "STOP", "request_id": "s2"},
            ),
        ]
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        s.call("주가 알려줘", tools=tools)

        # 1차 — 실패
        try:
            s.send_tool_results([("fc_abc", "get_stock_price", {"price_krw": 87500})], tools=tools)
        except Exception:
            pass

        # 2차 — 같은 results 재시도. rollback 덕에 tool_msg 가 중복되지 않아야 함
        r2 = s.send_tool_results([("fc_abc", "get_stock_price", {"price_krw": 87500})], tools=tools)
        assert r2.text == "87,500원"

    # 재시도 호출의 wire body 검증 — tool message 가 정확히 1개여야 (중복 없음)
    retry_body = instance.request.call_args_list[2].kwargs["json"]
    tool_messages = [m for m in retry_body["messages"] if m["role"] == "tool"]
    assert len(tool_messages) == 1, f"재시도 시 tool message 중복 — got {len(tool_messages)} (rollback 미동작 의심)"


def test_session_send_tool_results_rollback_on_keyboard_interrupt():
    """KeyboardInterrupt 같은 BaseException 에서도 rollback 동작 (Exception 만 잡으면 누락됨)."""
    import pytest

    tools = _tools_def()
    with patch("llm_manager._internal.handler_client._sync_client.httpx.Client") as mock_client_cls:
        instance = mock_client_cls.return_value
        # side_effect 리스트에 예외 인스턴스를 두면 해당 호출 시점에 raise — 함수 객체를 두면 안 됨
        instance.request.side_effect = [_turn1_response_with_fc(), KeyboardInterrupt()]
        llm_manager._internal.handler_client.configure(base_url="http://test:8100")
        s = llm_manager._internal.handler_client.session()
        s.call("주가 알려줘", tools=tools)

        history_before = s.get_history()
        with pytest.raises(KeyboardInterrupt):
            s.send_tool_results([("fc_abc", "get_stock_price", {"price_krw": 87500})], tools=tools)
        assert s.get_history() == history_before
