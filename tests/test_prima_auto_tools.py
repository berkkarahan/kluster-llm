import pytest

from kllm.modules.primacpp.interactors import qualification


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("protocol,choice", [("chat", "auto"), ("responses", "required"), ("messages", {"type": "any"})])
def test_automatic_tools_require_native_capability_and_execution_evidence(monkeypatch, stream, protocol, choice):
    body = {"tools": [{"name": "echo"}], "tool_choice": choice, "stream": stream}
    requested = qualification.api_features(body, protocol)
    assert "tools:auto" in requested
    active = {"engine": "primacpp", "runtime": {"capabilities": {"features": []}},
              "qualification_context": {"execution": {"fixture": True}}}
    allowed = set(requested)
    monkeypatch.setattr(qualification, "qualification_status", lambda *a, **k: {
        "inference_supported": True, "qualified_features": list(allowed)})
    with pytest.raises(ValueError, match="native runtime does not support automatic tools"):
        qualification.require_api_features(body, active, protocol)
    active["runtime"]["capabilities"]["features"] = ["automatic_tools_v1"]
    if stream:
        with pytest.raises(ValueError, match="automatic tool streams"):
            qualification.require_api_features(body, active, protocol)
        active["runtime"]["capabilities"]["features"].append("streaming_automatic_tools_v1")
    qualification.require_api_features(body, active, protocol)
    allowed.remove("tools:auto")
    with pytest.raises(ValueError, match="not qualified: tools:auto"):
        qualification.require_api_features(body, active, protocol)


@pytest.mark.parametrize("protocol,body,parallel", [
    ("chat", {"parallel_tool_calls": False}, False),
    ("chat", {}, True),
    ("responses", {"parallel_tool_calls": True}, True),
    ("messages", {"tool_choice": {"type": "any", "disable_parallel_tool_use": True}}, False),
    ("messages", {"tool_choice": {"type": "auto"}}, True),
])
def test_parallel_automatic_tools_have_separate_evidence(protocol, body, parallel):
    required = qualification.api_features({"tools": [{"name": "echo"}], **body}, protocol)
    assert ("parallel_tools" in required) is parallel
