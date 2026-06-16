"""Session -- SDK 로컬 멀티턴 이력 관리 (이중 히스토리 패턴).

Gemini Chat 모듈과 동일한 curated/comprehensive 분리 전략:
- _curated_history: 유효한 턴만 포함 -> API 호출 시 사용
- _comprehensive_history: 전체 기록 -> 디버깅/로깅용
"""

from collections.abc import Iterator
from typing import Any

from ._common import ContentsType, _normalize_contents, is_valid_response
from ._sync_client import SyncClient
from .models import Content, FunctionResponse, LLMResponse, Part, StreamChunk


class Session:
    """SDK 로컬에서 대화 이력을 자동 관리하는 세션.

    이중 히스토리 패턴:
    - curated: 유효한 턴만 -> API에 전달되는 이력
    - comprehensive: 모든 턴 -> 디버깅용 전체 기록

    무효 응답(빈 텍스트, 공백만, 또는 SAFETY/RECITATION 등 비정상 finish_reason)은
    curated에서 제외되어 다음 요청의 이력을 오염시키지 않는다.
    """

    def __init__(
        self,
        client: SyncClient,
        *,
        system: str | None = None,
        model: str | None = None,
    ) -> None:
        self._client = client
        self._system = system
        self._model = model
        self._curated_history: list[dict] = []
        self._comprehensive_history: list[dict] = []

    def _build_model_msg(self, response: LLMResponse) -> dict[str, Any]:
        """response 를 role='model' 메시지로 변환.

        assistant_message 가 있으면 그대로 dump (role + parts 통째 보존).
        없으면 text fallback (stream pseudo-response 처럼 assistant_message 누락 시).
        """
        if response.assistant_message is not None:
            return response.assistant_message.model_dump(exclude_none=True)
        return {"role": "model", "content": response.text or ""}

    def _record_history(
        self,
        contents: ContentsType,
        response: LLMResponse,
    ) -> None:
        """응답 유효성에 따라 이중 히스토리에 기록."""
        normalized = _normalize_contents(contents)
        user_msg = {"role": "user", "content": normalized}
        model_msg = self._build_model_msg(response)

        # comprehensive: 항상 기록
        self._comprehensive_history.append(user_msg)
        self._comprehensive_history.append(model_msg)

        # curated: 유효할 때만
        if is_valid_response(response):
            self._curated_history.append(user_msg)
            self._curated_history.append(model_msg)

    def call(
        self,
        contents: ContentsType,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        seed: int | None = None,
        output: str | None = None,
        schema: type | dict | None = None,
        thinking: str | None = None,
        tools: list[dict] | None = None,
        tool_config: str | None = None,
        cached_content: str | None = None,
        task_name: str | None = None,
        media_resolution: str | None = None,
    ) -> LLMResponse:
        """세션 내 호출 -- 이력 자동 관리."""
        response = self._client.call(
            contents,
            system=self._system,
            model=self._model,
            temperature=temperature,
            max_tokens=max_tokens,
            seed=seed,
            output=output,
            schema=schema,
            thinking=thinking,
            tools=tools,
            tool_config=tool_config,
            history=self._curated_history if self._curated_history else None,
            cached_content=cached_content,
            task_name=task_name,
            media_resolution=media_resolution,
        )
        self._record_history(contents, response)
        return response

    def stream(
        self,
        contents: ContentsType,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        seed: int | None = None,
        output: str | None = None,
        schema: type | dict | None = None,
        thinking: str | None = None,
        tools: list[dict] | None = None,
        tool_config: str | None = None,
        cached_content: str | None = None,
        task_name: str | None = None,
        media_resolution: str | None = None,
    ) -> Iterator[StreamChunk]:
        """세션 내 스트리밍 -- 이력 자동 관리."""
        collected_text = ""
        last_finish_reason: str | None = None
        collected_function_calls = None
        collected_tool_calls = None
        collected_tool_responses = None
        collected_code_blocks = None
        collected_code_results = None
        collected_grounding = None
        for chunk in self._client.stream(
            contents,
            system=self._system,
            model=self._model,
            temperature=temperature,
            max_tokens=max_tokens,
            seed=seed,
            output=output,
            schema=schema,
            thinking=thinking,
            tools=tools,
            tool_config=tool_config,
            history=self._curated_history if self._curated_history else None,
            cached_content=cached_content,
            task_name=task_name,
            media_resolution=media_resolution,
        ):
            collected_text += chunk.text or ""
            if chunk.finish_reason is not None:
                last_finish_reason = chunk.finish_reason
            if chunk.function_calls is not None:
                collected_function_calls = chunk.function_calls
            if chunk.tool_calls is not None:
                collected_tool_calls = chunk.tool_calls
            if chunk.tool_responses is not None:
                collected_tool_responses = chunk.tool_responses
            if chunk.code_blocks is not None:
                collected_code_blocks = chunk.code_blocks
            if chunk.code_results is not None:
                collected_code_results = chunk.code_results
            if chunk.grounding is not None:
                collected_grounding = chunk.grounding
            yield chunk

        # Handler 의 done event 에서 한 turn 의 완성된 assistant_message (parts 단위 — ts 포함) 복원.
        # 기존 handler (done payload 없음) 와 호환: 없으면 None → _build_model_msg 가 text fallback.
        done_payload = getattr(self._client, "_last_done_payload", None)
        assistant_message: Content | None = None
        if isinstance(done_payload, dict):
            am_dict = done_payload.get("assistant_message")
            if isinstance(am_dict, dict):
                assistant_message = Content.model_validate(am_dict)

        pseudo_response = LLMResponse(
            text=collected_text or None,
            model="",
            request_id="",
            finish_reason=last_finish_reason,
            function_calls=collected_function_calls,
            tool_calls=collected_tool_calls,
            tool_responses=collected_tool_responses,
            code_blocks=collected_code_blocks,
            code_results=collected_code_results,
            grounding=collected_grounding,
            assistant_message=assistant_message,
        )
        self._record_history(contents, pseudo_response)

    def send_tool_results(
        self,
        results: list[tuple[str | None, str, dict[str, Any]]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        seed: int | None = None,
        thinking: str | None = None,
        tools: list[dict] | None = None,
        tool_config: str | None = None,
        cached_content: str | None = None,
        task_name: str | None = None,
        media_resolution: str | None = None,
    ) -> LLMResponse:
        """function 실행 결과를 role='tool' 메시지로 history 에 추가하고 다음 응답 받기.

        results: [(id, name, response_dict), ...] — id 는 Turn 1 응답의 function_call.id 매칭.
        직전 turn 이 function_call 을 포함했어야 함 (history 에 model_msg 이미 누적된 상태).

        client.call(messages=...) 경유 — 빈 user message append 우회 위해 messages path 사용.
        """

        if tools is None:
            for msg in reversed(self._curated_history):
                if msg.get("role") != "model":
                    continue
                content = msg.get("content")
                if isinstance(content, list) and any(p.get("type") == "function_call" for p in content):
                    raise ValueError(
                        "send_tool_results 호출 시 `tools=` 가 필요합니다. "
                        "직전 turn 의 모델 응답에 function_call 이 포함되어 있는데, "
                        "Gemini 가 function_response 를 해석하려면 같은 함수 정의(tools)가 다시 전달되어야 합니다. "
                        "원래 call() 에 사용한 tools 리스트를 그대로 전달하세요."
                    )
                break  # 가장 최근 model turn 만 검사 후 종료

        tool_msg = Content(
            role="tool",
            content=[
                Part(
                    type="function_response",
                    function_response=FunctionResponse(name=name, response=response_dict, id=id_),
                )
                for id_, name, response_dict in results
            ],
        )
        tool_dump = tool_msg.model_dump(exclude_none=True)
        self._curated_history.append(tool_dump)
        self._comprehensive_history.append(tool_dump)

        messages = [Content.model_validate(m) for m in self._curated_history]
        try:
            response = self._client.call(
                messages=messages,
                system=self._system,
                model=self._model,
                temperature=temperature,
                max_tokens=max_tokens,
                seed=seed,
                thinking=thinking,
                tools=tools,
                tool_config=tool_config,
                cached_content=cached_content,
                task_name=task_name,
                media_resolution=media_resolution,
            )
        except BaseException:
            # Handler 호출 실패 시 위에서 append 한 tool_msg 를 되돌려야 한다 — 그렇지 않으면
            # 다음 호출에서 orphan tool message (model 응답 없이 tool 메시지로 끝남) 가 보여
            # Gemini 가 빈응답/400 으로 연쇄 실패. KeyboardInterrupt / CancelledError 도 포함하려면
            # BaseException 으로 잡아야 함 (task cancel 시에도 history 정합성 유지).
            self._curated_history.pop()
            self._comprehensive_history.pop()
            raise

        # 모델 응답을 history 에 echo (이 path 는 user_msg 없이 model_msg 만 추가)
        model_msg = self._build_model_msg(response)
        self._comprehensive_history.append(model_msg)
        if is_valid_response(response):
            self._curated_history.append(model_msg)
        return response

    def get_history(self) -> list[dict]:
        """curated 이력 반환 (유효한 턴만)."""
        return list(self._curated_history)

    def get_full_history(self) -> list[dict]:
        """comprehensive 이력 반환 (무효 턴 포함 전체)."""
        return list(self._comprehensive_history)

    def clear(self) -> None:
        """양쪽 이력 모두 초기화."""
        self._curated_history.clear()
        self._comprehensive_history.clear()
