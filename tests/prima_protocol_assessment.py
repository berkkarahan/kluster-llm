"""Model-backed native Responses/Messages evidence, separate from gateway acceptance."""

import argparse
from functools import partial
import json
import math
from pathlib import Path

import httpx

import prima_assessment as assessment
from kllm.modules.runtimes.interactors.registry import file_digest


PROMPT = "Output exactly the lowercase ASCII word hello, without translation, explanation, punctuation or other text."
SCHEMA = {"type": "object", "properties": {"text": {"type": "string", "const": "hello"}},
          "required": ["text"], "additionalProperties": False}


def validate_body(protocol, kind, status, body, model="assessment"):
    if status != 200:
        return [f"HTTP {status}"]
    try:
        usage = body["usage"]
        if any(type(usage[k]) is not int or usage[k] < 1 for k in ("input_tokens", "output_tokens")):
            return ["Invalid usage"]
        if body["model"] != model:
            return ["Incorrect model alias"]
        if protocol == "responses":
            if body["object"] != "response" or body["status"] != "completed":
                return ["Response did not complete"]
            if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
                return ["Incorrect total usage"]
            item = body["output"][0]
            if kind == "tool":
                if item["type"] != "function_call" or item["name"] != "echo" or not item["call_id"]:
                    return ["Missing forced function call"]
                return [] if json.loads(item["arguments"]) == {"text": "hello"} else ["Incorrect tool arguments"]
            text = item["content"][0]["text"]
            if item["role"] != "assistant":
                return ["Incorrect output role"]
        else:
            if body["type"] != "message" or body["role"] != "assistant":
                return ["Incorrect Messages envelope"]
            item = body["content"][0]
            if kind == "tool":
                return [] if (body["stop_reason"] == "tool_use" and item["type"] == "tool_use"
                    and item["name"] == "echo" and item["input"] == {"text": "hello"} and item["id"]) else ["Incorrect tool-use block"]
            if body["stop_reason"] != "end_turn":
                return ["Incorrect Messages termination"]
            text = item["text"]
        if kind == "structured":
            return [] if json.loads(text) == {"text": "hello"} else ["Incorrect structured output"]
        return [] if text.strip().lower() == "hello" else ["Incorrect text output"]
    except (KeyError, TypeError, ValueError, IndexError):
        return ["Malformed protocol response"]


def stream_case(client, protocol, body):
    events, text = [], []
    with client.stream("POST", f"/v1/{protocol}", json={**body, "stream": True}) as response:
        event_name = None
        for line in response.iter_lines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                if line[5:].strip() == "[DONE]":
                    continue
                event = json.loads(line[5:])
                events.append({"event": event_name, "data": event})
                if event.get("type") == "response.output_text.delta":
                    text.append(event["delta"])
                if event.get("type") == "content_block_delta":
                    text.append(event.get("delta", {}).get("text", ""))
    errors = []
    types = [e["data"].get("type") for e in events]
    if response.status_code != 200:
        errors.append(f"HTTP {response.status_code}")
    if any(e["event"] != e["data"].get("type") for e in events):
        errors.append("SSE event field and data type disagree")
    if "".join(text).strip().lower() != "hello":
        errors.append("Stream did not produce requested answer")
    expected = ({"response.created", "response.output_item.added", "response.content_part.added",
                 "response.output_text.delta", "response.output_text.done", "response.content_part.done",
                 "response.output_item.done", "response.completed"} if protocol == "responses" else
                {"message_start", "content_block_start", "content_block_delta", "content_block_stop", "message_delta", "message_stop"})
    if not expected <= set(types):
        errors.append("Missing stream lifecycle event")
    if protocol == "responses":
        if [e["data"].get("sequence_number") for e in events] != list(range(len(events))):
            errors.append("Response sequence numbers are not consecutive")
        terminal = [e["data"]["response"] for e in events if e["data"].get("type") == "response.completed"]
        if len(terminal) != 1:
            errors.append("Missing or repeated completed response")
        else:
            errors += validate_body(protocol, "text", 200, terminal[0], body["model"])
    else:
        usage = [e["data"].get("usage", {}) for e in events if e["data"].get("type") == "message_delta"]
        if len(usage) != 1 or usage[0].get("output_tokens", 0) < 1:
            errors.append("Missing Messages output usage")
    return {"case": protocol + "_stream", "status": response.status_code,
            "semantic_errors": errors, "events": events}


def logprob_errors(entries, top):
    errors = []
    for entry in entries:
        if (not isinstance(entry.get("token"), str) or not isinstance(entry.get("bytes"), list)
                or any(type(b) is not int or not 0 <= b <= 255 for b in entry.get("bytes", []))
                or not math.isfinite(entry.get("logprob", float("nan"))) or entry["logprob"] > 0):
            errors.append("Invalid selected token logprob/bytes")
        alternatives = entry.get("top_logprobs")
        if not isinstance(alternatives, list) or len(alternatives) > top or (top and not alternatives):
            errors.append("Invalid top alternatives")
        for alternative in alternatives or []:
            if not math.isfinite(alternative.get("logprob", float("nan"))) or alternative["logprob"] > 0:
                errors.append("Invalid alternative logprob")
    return errors


def logprob_cases(client, model):
    body = {"model": model, "messages": [{"role": "user", "content": PROMPT}],
            "temperature": 0, "seed": 0, "max_tokens": 128,
            "chat_template_kwargs": {"enable_thinking": False}}
    results, outputs = [], []
    plain = client.post("/v1/chat/completions", json=body)
    plain.raise_for_status()
    reference = plain.json()
    for top in (0, 3, 20):
        response = client.post("/v1/chat/completions", json={**body, "logprobs": True, "top_logprobs": top})
        data = response.json()
        errors = []
        try:
            entries = data["choices"][0]["logprobs"]["content"]
            errors += logprob_errors(entries, top)
            if len(entries) != data["usage"]["completion_tokens"]:
                errors.append("Reported token entries differ from generated usage")
            if data["choices"][0]["message"]["content"] != reference["choices"][0]["message"]["content"]:
                errors.append("Logprob reporting changed greedy output")
            if data["usage"]["completion_tokens"] != reference["usage"]["completion_tokens"]:
                errors.append("Logprob reporting changed greedy token count")
            outputs.append(data["choices"][0]["message"]["content"])
        except (KeyError, TypeError):
            errors.append("Missing OpenAI choice logprobs")
        results.append({"case": f"chat_logprobs_{top}", "status": response.status_code,
                        "body": data, "semantic_errors": errors if response.status_code == 200 else [f"HTTP {response.status_code}"]})
    chunks, entries, text, roles, done = [], [], [], [], 0
    with client.stream("POST", "/v1/chat/completions", json={**body, "stream": True, "logprobs": True,
            "top_logprobs": 3, "stream_options": {"include_usage": True}}) as response:
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            if line[5:].strip() == "[DONE]":
                done += 1
                continue
            chunk = json.loads(line[5:])
            chunks.append(chunk)
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                text.append(delta.get("content") or "")
                if delta.get("role"):
                    roles.append(delta["role"])
                entries += (choice.get("logprobs") or {}).get("content", [])
    errors = logprob_errors(entries, 3)
    usages = [chunk["usage"] for chunk in chunks if chunk.get("usage")]
    if done != 1 or roles != ["assistant"] or "".join(text) != reference["choices"][0]["message"]["content"]:
        errors.append("Logprob stream lost role/content/termination")
    if not usages or len(entries) != usages[-1]["completion_tokens"]:
        errors.append("Stream logprob entries differ from generated usage")
    results.append({"case": "chat_logprobs_stream", "status": response.status_code, "events": chunks, "semantic_errors": errors})
    for name, options in (("disabled_top", {"logprobs": False, "top_logprobs": 3}),
                          ("invalid_type", {"logprobs": "true"}),
                          ("negative_top", {"logprobs": True, "top_logprobs": -1}),
                          ("large_top", {"logprobs": True, "top_logprobs": 21})):
        response = client.post("/v1/chat/completions", json={**body, **options})
        results.append({"case": "chat_logprobs_" + name, "status": response.status_code, "body": response.json(),
                        "semantic_errors": [] if response.status_code == 400 else ["Invalid logprob request was not rejected"]})
    return results


def validate_tool_stream(protocol, events, *, limited=False, done=0, logprobs=False):
    """Reject incomplete, misidentified or text-channel tool stream receipts."""
    data = [row["data"] for row in events]
    errors, arguments = [], []
    try:
        if protocol == "chat/completions":
            calls = [choice["delta"]["tool_calls"][0] for event in data
                     for choice in event.get("choices", []) if choice.get("delta", {}).get("tool_calls")]
            if done != 1 or not calls[0].get("id") or calls[0].get("type") != "function" or calls[0]["function"].get("name") != "echo":
                errors.append("Missing tool identity or terminal sentinel")
            if any(call.get("index") != 0 or (call.get("id") and call["id"] != calls[0]["id"]) for call in calls):
                errors.append("Tool identity/index changed during stream")
            arguments = [call["function"].get("arguments", "") for call in calls]
            finishes = [choice["finish_reason"] for event in data for choice in event.get("choices", []) if choice.get("finish_reason")]
            if finishes != ["length" if limited else "tool_calls"]:
                errors.append("Tool finish reason was lost or duplicated")
            if any(choice.get("delta", {}).get("content") for event in data for choice in event.get("choices", [])):
                errors.append("Tool arguments leaked into text channel")
            usage = [event["usage"] for event in data if event.get("usage")]
            if len(usage) != 1 or usage[0]["completion_tokens"] < 1 or usage[0]["prompt_tokens"] < 1:
                errors.append("Missing tool stream usage")
            if logprobs:
                entries = [entry for event in data for choice in event.get("choices", [])
                           for entry in (choice.get("logprobs") or {}).get("content", [])]
                errors += logprob_errors(entries, 3)
                if len(entries) != usage[0]["completion_tokens"]:
                    errors.append("Tool stream logprobs differ from native usage")
        elif protocol == "responses":
            if [event.get("sequence_number") for event in data] != list(range(len(data))):
                errors.append("Tool response sequence is not consecutive")
            added = [event["item"] for event in data if event.get("type") == "response.output_item.added"]
            item = added[0]
            if len(added) != 1 or item["type"] != "function_call" or item["name"] != "echo" or not item.get("call_id"):
                errors.append("Missing response tool item")
            deltas = [event for event in data if event.get("type") == "response.function_call_arguments.delta"]
            arguments = [event["delta"] for event in deltas]
            if any(event.get("item_id") != item["id"] or event.get("output_index") != 0 for event in deltas):
                errors.append("Response argument item identity changed")
            finals = [event["response"] for event in data if event.get("type") in ("response.completed", "response.incomplete")]
            if len(finals) != 1 or finals[0]["status"] != ("incomplete" if limited else "completed"):
                errors.append("Tool response did not terminate correctly")
            final = finals[0]["output"][0]
            joined = "".join(arguments)
            dones = [event for event in data if event.get("type") == "response.function_call_arguments.done"]
            item_dones = [event["item"] for event in data if event.get("type") == "response.output_item.done"]
            if (len(dones) != 1 or dones[0].get("item_id") != item["id"] or dones[0].get("arguments") != joined
                    or item_dones != [final] or final["id"] != item["id"] or final["call_id"] != item["call_id"] or final["arguments"] != joined):
                errors.append("Completed tool arguments/identity differ from deltas")
            if finals[0]["usage"]["input_tokens"] < 1 or finals[0]["usage"]["output_tokens"] < 1:
                errors.append("Missing response tool usage")
            if any(event.get("type", "").startswith("response.output_text") for event in data):
                errors.append("Response tool arguments leaked into text channel")
        else:
            blocks = [event["content_block"] for event in data if event.get("type") == "content_block_start"]
            if len(blocks) != 1 or blocks[0]["type"] != "tool_use" or blocks[0]["name"] != "echo" or not blocks[0].get("id"):
                errors.append("Missing Messages tool-use block")
            deltas = [event["delta"] for event in data if event.get("type") == "content_block_delta"]
            if any(delta["type"] != "input_json_delta" for delta in deltas):
                errors.append("Messages arguments leaked into text channel")
            arguments = [delta.get("partial_json", "") for delta in deltas]
            final = [event for event in data if event.get("type") == "message_delta"]
            if len(final) != 1 or final[0]["delta"]["stop_reason"] != ("max_tokens" if limited else "tool_use") or final[0]["usage"]["output_tokens"] < 1:
                errors.append("Messages tool termination/usage is incorrect")
            types = [event.get("type") for event in data]
            if types.count("message_start") != 1 or types.count("content_block_stop") != 1 or types.count("message_stop") != 1:
                errors.append("Messages tool lifecycle did not settle once")
        if protocol != "chat/completions" and any(row["event"] != row["data"].get("type") for row in events):
            errors.append("Tool SSE event field and data type disagree")
        if not limited and json.loads("".join(arguments)) != {"text": "hello"}:
            errors.append("Streamed tool arguments are incorrect")
    except (KeyError, ValueError, IndexError, TypeError):
        errors.append("Malformed tool stream")
    return errors


def tool_stream_case(client, protocol, body, *, limited=False, logprobs=False):
    events, done, name = [], 0, None
    options = {"stream": True}
    if limited:
        options["max_output_tokens" if protocol == "responses" else "max_tokens"] = 1
    if logprobs:
        options.update(logprobs=True, top_logprobs=3)
    with client.stream("POST", "/v1/" + protocol, json={**body, **options}) as response:
        for line in response.iter_lines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                if line[5:].strip() == "[DONE]":
                    done += 1
                else:
                    events.append({"event": name, "data": json.loads(line[5:])})
    errors = [f"HTTP {response.status_code}"] if response.status_code != 200 else validate_tool_stream(
        protocol, events, limited=limited, done=done, logprobs=logprobs)
    return {"case": protocol.replace("/", "_") + "_tool_stream" + ("_limit" if limited else "_logprobs" if logprobs else ""),
            "status": response.status_code, "semantic_errors": errors, "events": events,
            "done_count": done, "limited": limited, "logprobs": logprobs}


def tool_history_cases(client, model):
    results = []
    common = {"model": model, "temperature": 0, "max_tokens": 128,
              "chat_template_kwargs": {"enable_thinking": False}}
    start = "Call echo with text hello."
    tool = {"name": "echo", "parameters": SCHEMA}
    for protocol in ("chat/completions", "responses", "messages"):
        if protocol == "responses":
            initial = {"model": model, "temperature": 0, "max_output_tokens": 128,
                       "chat_template_kwargs": {"enable_thinking": False}, "input": start,
                       "tools": [{"type": "function", **tool}], "tool_choice": {"type": "function", "name": "echo"}}
        elif protocol == "messages":
            initial = {**common, "messages": [{"role": "user", "content": start}],
                       "tools": [{"name": "echo", "input_schema": SCHEMA}], "tool_choice": {"type": "tool", "name": "echo"}}
        else:
            initial = {**common, "messages": [{"role": "user", "content": start}],
                       "tools": [{"type": "function", "function": tool}],
                       "tool_choice": {"type": "function", "function": {"name": "echo"}}}
        response = client.post("/v1/" + protocol, json=initial)
        response.raise_for_status()
        first = response.json()
        if protocol == "responses":
            call = first["output"][0]
            follow = {**initial, "tool_choice": "none", "input": [
                {"role": "user", "content": start}, call,
                {"type": "function_call_output", "call_id": call["call_id"],
                 "output": [{"type": "input_text", "text": '{"text":"hello"}'}]},
                {"role": "user", "content": PROMPT}]}
        elif protocol == "messages":
            call = first["content"][0]
            follow = {**initial, "tool_choice": {"type": "none"}, "messages": [
                {"role": "user", "content": start}, {"role": "assistant", "content": first["content"]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call["id"],
                    "content": [{"type": "text", "text": '{"text":"hello"}'}]}, {"type": "text", "text": PROMPT}]}]}
        else:
            message = first["choices"][0]["message"]
            call = message["tool_calls"][0]
            follow = {**initial, "tool_choice": "none", "messages": [
                {"role": "user", "content": start}, message,
                {"role": "tool", "tool_call_id": call["id"], "content": '{"text":"hello"}'},
                {"role": "user", "content": PROMPT}]}
        response = client.post("/v1/" + protocol, json=follow)
        data = response.json()
        if protocol == "chat/completions":
            errors = [] if response.status_code == 200 and data["choices"][0]["message"]["content"].strip().lower() == "hello" else ["Chat tool history did not continue"]
            events, text, done = [], [], 0
            with client.stream("POST", "/v1/" + protocol, json={**follow, "stream": True}) as streamed:
                for line in streamed.iter_lines():
                    if line.startswith("data:"):
                        if line[5:].strip() == "[DONE]":
                            done += 1
                        else:
                            event = json.loads(line[5:])
                            events.append(event)
                            text += [c.get("delta", {}).get("content") or "" for c in event.get("choices", [])]
            stream_errors = [] if streamed.status_code == 200 and done == 1 and "".join(text).strip().lower() == "hello" else ["Chat history stream did not continue"]
            stream_result = {"status": streamed.status_code, "events": events, "semantic_errors": stream_errors}
        else:
            errors = validate_body(protocol, "text", response.status_code, data, model)
            stream_result = stream_case(client, protocol, follow)
        results.append({"case": protocol.replace("/", "_") + "_tool_history", "status": response.status_code,
                        "semantic_errors": errors, "body": data, "initial_call": first, "followup_request": follow})
        results.append({**stream_result, "case": protocol.replace("/", "_") + "_tool_history_stream"})
    return results


def cases(port, model="assessment", headers=None, include_logprobs=False, include_stream_tools=False, include_tool_history=False):
    response_body = {"model": model, "input": PROMPT, "temperature": 0, "seed": 0,
                     "max_output_tokens": 128, "chat_template_kwargs": {"enable_thinking": False}}
    messages_body = {"model": model, "messages": [{"role": "user", "content": PROMPT}],
                     "max_tokens": 128, "temperature": 0, "thinking": {"type": "disabled"},
                     "chat_template_kwargs": {"enable_thinking": False}}
    bodies = [
        ("responses", "text", response_body),
        ("responses", "structured", {**response_body, "input": 'Return JSON: {"text":"hello"}.',
            "response_format": {"type": "json_schema", "json_schema": {"name": "reply", "strict": True, "schema": SCHEMA}},
            "text": {"format": {"type": "json_schema", "name": "reply", "strict": True, "schema": SCHEMA}}}),
        ("responses", "tool", {**response_body, "input": "Call echo with text hello.",
            "tools": [{"type": "function", "name": "echo", "parameters": SCHEMA}],
            "tool_choice": {"type": "function", "name": "echo"}}),
        ("messages", "text", messages_body),
        ("messages", "tool", {**messages_body, "messages": [{"role": "user", "content": "Call echo with text hello."}],
            "tools": [{"name": "echo", "input_schema": SCHEMA}], "tool_choice": {"type": "tool", "name": "echo"}}),
    ]
    result = []
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=headers, timeout=120, trust_env=False) as client:
        if include_logprobs:
            result += logprob_cases(client, model)
        if include_tool_history:
            result += tool_history_cases(client, model)
        for protocol, kind, body in bodies:
            response = client.post(f"/v1/{protocol}", json=body)
            data = response.json()
            result.append({"case": protocol + "_" + kind, "status": response.status_code, "body": data,
                           "semantic_errors": validate_body(protocol, kind, response.status_code, data, model)})
        counted = client.post("/v1/responses/input_tokens", json=response_body)
        data = counted.json()
        result.append({"case": "response_input_tokens", "status": counted.status_code, "body": data,
            "semantic_errors": [] if counted.status_code == 200 and data.get("object") == "response.input_tokens"
                and data.get("input_tokens") == next(c for c in result if c["case"] == "responses_text")["body"].get("usage", {}).get("input_tokens") else ["Input count differs from actual generation usage"]})
        result += [stream_case(client, "responses", response_body), stream_case(client, "messages", messages_body)]
        if include_stream_tools:
            tools = [(protocol, body) for protocol, kind, body in bodies if kind == "tool"]
            chat_tool = {**messages_body, "tools": [{"type": "function", "function": {"name": "echo", "parameters": SCHEMA}}],
                         "tool_choice": {"type": "function", "function": {"name": "echo"}}}
            chat_tool.pop("thinking", None)
            tools.append(("chat/completions", chat_tool))
            for protocol, body in tools:
                result += [tool_stream_case(client, protocol, body), tool_stream_case(client, protocol, body, limited=True)]
            if include_logprobs:
                result.append(tool_stream_case(client, "chat/completions", chat_tool, logprobs=True))
        for protocol, body, key, expected in (("responses", response_body, "max_output_tokens", "incomplete"),
                                             ("messages", messages_body, "max_tokens", "max_tokens")):
            body = {**body, key: 1}
            if protocol == "responses":
                body["input"] = "Count from one to ten, separated by commas."
            else:
                body["messages"] = [{"role": "user", "content": "Count from one to ten, separated by commas."}]
            response = client.post(f"/v1/{protocol}", json=body)
            data = response.json()
            actual = data.get("status") if protocol == "responses" else data.get("stop_reason")
            result.append({"case": protocol + "_limit", "status": response.status_code, "body": data,
                           "semantic_errors": [] if response.status_code == 200 and actual == expected else ["Length termination was not preserved"]})
        for name, body in (("reserved", {**response_body, "__prima_tool_name": "spoof"}),
                           ("invalid_input", {**response_body, "input": 12})):
            response = client.post("/v1/responses", json=body)
            result.append({"case": "responses_" + name, "status": response.status_code,
                           "body": response.json(), "semantic_errors": [] if response.status_code == 400 else ["Malformed input was not rejected as HTTP 400"]})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--baseline-server", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--ngl", type=int, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--development-server", type=Path)
    parser.add_argument("--logprobs", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        runtime = assessment.capture_runtime(args.runtime_dir)
        assessment.capture_baseline(args.baseline_server.resolve())
        if args.development_server:
            runtime["llama_server"] = str(args.development_server.resolve())
        assessment.request_cases = partial(cases, include_logprobs=args.logprobs)
        result = assessment.model_assessment(runtime, args.baseline_server.resolve(), args.model.resolve(), "vulkan", args.ngl, device=args.device)
        result["binary_sha256"] = file_digest(Path(runtime["llama_server"]))
        result["qualification"] = "open: developmental worktree" if args.development_server else "open: native protocol subset; gateway acceptance still required"
        result["native_status"] = "passed" if all(not c["semantic_errors"] for c in result["prima"]["cases"]) else "failed"
        result["baseline_status"] = "passed" if all(not c["semantic_errors"] for c in result["baseline"]["cases"]) else "failed"
        result["baseline_deviations"] = [{"case": c["case"], "errors": c["semantic_errors"]}
            for c in result["baseline"]["cases"] if c["semantic_errors"]]
        result["status"] = "observed" if result["native_status"] == "passed" and result["gpu_execution_evidence"]["status"] == "passed" else "failed"
    except Exception as error:
        result = {"status": "failed", "error": str(error), "qualification": "open"}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(args.output), "status": result["status"]}))
    return 0 if result["status"] == "observed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
