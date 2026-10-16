import pytest

from kllm.modules.primacpp.interactors.qualification import api_features


@pytest.mark.parametrize("protocol,body", [
    ("chat", {"messages": [{"role": "assistant", "tool_calls": [{"id": "call"}], "content": None},
                           {"role": "tool", "tool_call_id": "call", "content": "hello"}]}),
    ("responses", {"input": [{"type": "function_call", "call_id": "call", "name": "echo", "arguments": "{}"},
                             {"type": "function_call_output", "call_id": "call", "output": "hello"}]}),
    ("messages", {"messages": [{"role": "assistant", "content": [{"type": "tool_use", "id": "call", "name": "echo", "input": {}}]},
                               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call", "content": [{"type": "text", "text": "hello"}]}]}]}),
])
def test_tool_history_requires_its_own_scope_without_claiming_vision(protocol, body):
    features = api_features(body, protocol)
    assert "tool_turns" in features
    assert "vision" not in features


@pytest.mark.parametrize("protocol,body", [
    ("responses", {"input": [{"type": "function_call_output", "call_id": "call", "output": [{"type": "input_image"}]}]}),
    ("messages", {"messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call", "content": [{"type": "image"}]}]}]}),
])
def test_non_text_tool_result_cannot_bypass_vision_scope(protocol, body):
    assert {"tool_turns", "vision"} <= api_features(body, protocol)


def test_anthropic_none_choice_does_not_request_automatic_tools():
    assert "tools:auto" not in api_features({"tools": [{"name": "echo"}], "tool_choice": {"type": "none"}}, "messages")
    assert api_features({"input": "plain text"}, "responses") == {"responses"}
    assert api_features({"input": 12}, "responses") == {"responses"}
    assert "vision" not in api_features({"messages": [{"role": "user", "content": [12]}]}, "messages")
