from copy import deepcopy

import pytest

from prima_protocol_assessment import validate_tool_stream


def chat_events():
    return [{"event": "data", "data": {"choices": [{"delta": {"role": "assistant", "tool_calls": [{
        "index": 0, "id": "call_1", "type": "function", "function": {"name": "echo", "arguments": ""}}]},
        "finish_reason": None}]}},
        {"event": "data", "data": {"choices": [{"delta": {"tool_calls": [{"index": 0,
            "function": {"arguments": '{"text":"hello"}'}}]}, "finish_reason": None}]}},
        {"event": "data", "data": {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}}}]


def test_valid_named_call_and_partial_limit_have_distinct_termination():
    assert validate_tool_stream("chat/completions", chat_events(), done=1) == []
    events = chat_events()
    events[1]["data"]["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] = '{"text":'
    events[-1]["data"]["choices"][0]["finish_reason"] = "length"
    assert validate_tool_stream("chat/completions", events, done=1, limited=True) == []
    assert validate_tool_stream("chat/completions", events, done=1)


@pytest.mark.parametrize("mutation", ["identity", "channel", "duplicate_terminal", "arguments", "usage"])
def test_receipt_rejects_misleading_tool_streams(mutation):
    events = deepcopy(chat_events())
    delta = events[1]["data"]["choices"][0]["delta"]
    if mutation == "identity":
        delta["tool_calls"][0]["id"] = "foreign"
    elif mutation == "channel":
        delta["content"] = '{"text":"hello"}'
    elif mutation == "duplicate_terminal":
        events.append(deepcopy(events[-1]))
    elif mutation == "arguments":
        delta["tool_calls"][0]["function"]["arguments"] = '{"text":"wrong"}'
    else:
        events[-1]["data"]["usage"]["completion_tokens"] = 0
    assert validate_tool_stream("chat/completions", events, done=1)


def test_receipt_requires_one_sentinel_and_handles_empty_stream():
    for done in (0, 2):
        assert validate_tool_stream("chat/completions", chat_events(), done=done)
    assert validate_tool_stream("responses", [])
    assert validate_tool_stream("messages", [])
