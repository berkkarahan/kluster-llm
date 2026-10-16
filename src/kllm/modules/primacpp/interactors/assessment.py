"""Semantic checks shared by native and gateway Prima assessments."""

import json


def response_errors(case: str, status: int, body: dict) -> list[str]:
    if status != 200:
        return [f"HTTP {status}"]
    try:
        choice = body["choices"][0]
        message = choice["message"]
        if body.get("model") != "assessment" or message.get("role") != "assistant":
            return ["Incorrect model or message role"]
        if choice.get("finish_reason") not in ("stop", "length", "tool_calls"):
            return ["Invalid finish reason"]
        usage = body["usage"]
        counts = [usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")]
        if any(type(n) is not int or n < 0 for n in counts) or counts[0] + counts[1] != counts[2]:
            return ["Invalid token usage"]
        if case == "chat":
            if not isinstance(message.get("content"), str) or message["content"].strip().lower() != "hello":
                return ["Chat did not produce the requested answer"]
        elif case == "structured":
            if json.loads(message["content"]) != {"reply": "hello"}:
                return ["Structured output does not satisfy the requested schema"]
        elif case == "tools":
            calls = message["tool_calls"]
            if len(calls) != 1 or choice["finish_reason"] != "tool_calls":
                return ["Forced tool call or finish reason is absent"]
            call = calls[0]
            if (not call.get("id") or call.get("type") != "function" or call["function"]["name"] != "echo"
                    or json.loads(call["function"]["arguments"]) != {"text": "hello"}):
                return ["Tool call arguments or identity are incorrect"]
        else:
            return ["Unknown assessment case"]
        return []
    except (KeyError, TypeError, ValueError, IndexError):
        return ["Malformed completion response"]
