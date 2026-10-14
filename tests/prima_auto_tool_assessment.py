"""Automatic tools through real HTTP; correctness only, never performance acceptance."""

import json
import time

import httpx
from prima_protocol_assessment import logprob_errors


def response_errors(protocol, body, expected):
    want_calls = isinstance(expected, list) and bool(expected)
    try:
        if protocol == "chat/completions":
            message = body["choices"][0]["message"]
            calls = message.get("tool_calls", [])
            values = [(c["id"], c["function"]["name"], json.loads(c["function"]["arguments"])) for c in calls]
            text = message.get("content") or ""
            finish = body["choices"][0]["finish_reason"]
            usage = body["usage"]["completion_tokens"]
            valid_finish = finish == ("tool_calls" if want_calls else "stop")
        elif protocol == "responses":
            values = [(c["call_id"], c["name"], json.loads(c["arguments"])) for c in body["output"] if c["type"] == "function_call"]
            text = "".join(p["text"] for c in body["output"] if c["type"] == "message" for p in c["content"])
            usage = body["usage"]["output_tokens"]
            valid_finish = body["status"] == "completed"
        else:
            values = [(c["id"], c["name"], c["input"]) for c in body["content"] if c["type"] == "tool_use"]
            text = "".join(c["text"] for c in body["content"] if c["type"] == "text")
            usage = body["usage"]["output_tokens"]
            valid_finish = body["stop_reason"] == ("tool_use" if want_calls else "end_turn")
        if expected == "auto_choice":
            return response_errors(protocol, body, ["hello"] if values else "unmarked")
        if not valid_finish or type(usage) is not int or usage < 1:
            return ["Automatic completion lost termination or usage"]
        if expected == "unmarked":
            return [] if not values and json.loads(text) == {"name": "echo", "arguments": {"text": "hello"}} else ["Unmarked JSON differs from baseline text"]
        if expected:
            if (len(values) != len(expected) or len({v[0] for v in values}) != len(values)
                    or any(not v[0] or v[1] != "echo" or set(v[2]) != {"text"} for v in values)
                    or sorted(v[2]["text"] for v in values) != sorted(expected)):
                return ["Incorrect automatic call identity/name/schema/arguments"]
        elif values or text.strip().lower() != "hello":
            return ["Automatic choice did not decline tools and answer hello"]
        if "<tool_call>" in text or "</tool_call>" in text:
            return ["Tool markers leaked into text"]
        return []
    except (KeyError, IndexError, TypeError, ValueError):
        return ["Malformed automatic response"]


def stream_errors(protocol, events, expected, done):
    calls, text, terminal, finishes = {}, [], [], []
    want_calls = isinstance(expected, list) and bool(expected)
    try:
        for event in events:
            if "error" in event or event.get("type") == "error":
                return ["Automatic stream contains an error"]
            if protocol == "chat/completions":
                for choice in event.get("choices", []):
                    delta = choice.get("delta", {})
                    text.append(delta.get("content") or "")
                    for call in delta.get("tool_calls", []):
                        current = calls.setdefault(call["index"], {"id": None, "name": None, "arguments": ""})
                        if call.get("id"):
                            if current["id"] and current["id"] != call["id"]:
                                return ["Automatic stream changed call ID"]
                            current["id"] = call["id"]
                        if call.get("function", {}).get("name"):
                            current["name"] = call["function"]["name"]
                        current["arguments"] += call.get("function", {}).get("arguments", "")
                    if choice.get("finish_reason"):
                        finishes.append(choice["finish_reason"])
                if event.get("usage"):
                    terminal.append(event["usage"])
            elif protocol == "responses":
                if event["type"] == "response.output_item.added" and event["item"]["type"] == "function_call":
                    item = event["item"]
                    calls[event["output_index"]] = {"id": item["call_id"], "name": item["name"], "arguments": "", "item_id": item["id"]}
                elif event["type"] == "response.function_call_arguments.delta":
                    call = calls[event["output_index"]]
                    if event["item_id"] != call["item_id"]:
                        return ["Automatic stream changed item ID"]
                    call["arguments"] += event["delta"]
                elif event["type"] == "response.output_text.delta":
                    text.append(event["delta"])
                elif event["type"] == "response.completed":
                    terminal.append(event["response"])
            else:
                if event["type"] == "content_block_start" and event["content_block"]["type"] == "tool_use":
                    block = event["content_block"]
                    calls[event["index"]] = {"id": block["id"], "name": block["name"], "arguments": ""}
                elif event["type"] == "content_block_delta":
                    if event["delta"]["type"] == "input_json_delta":
                        calls[event["index"]]["arguments"] += event["delta"]["partial_json"]
                    elif event["delta"]["type"] == "text_delta":
                        text.append(event["delta"]["text"])
                elif event["type"] == "message_delta":
                    terminal.append(event["usage"])
                    finishes.append(event["delta"]["stop_reason"])
        if expected == "auto_choice":
            return stream_errors(protocol, events, ["hello"] if calls else "unmarked", done)
        if len(terminal) != 1:
            return ["Missing/repeated automatic stream terminal usage"]
        if protocol == "responses":
            if [e["sequence_number"] for e in events] != list(range(len(events))):
                return ["Automatic stream sequence gap"]
            errors = response_errors(protocol, terminal[0], expected)
            finals = {c["call_id"]: c["arguments"] for c in terminal[0]["output"] if c["type"] == "function_call"}
            if finals != {c["id"]: c["arguments"] for c in calls.values()}:
                errors.append("Automatic stream arguments differ from final response")
            if errors:
                return errors
        elif protocol == "chat/completions":
            if done != 1 or finishes != ["tool_calls" if want_calls else "stop"] or terminal[0]["completion_tokens"] < 1:
                return ["Automatic Chat stream did not terminate with usage"]
        elif finishes != ["tool_use" if want_calls else "end_turn"] or terminal[0]["output_tokens"] < 1:
            return ["Automatic Messages stream did not terminate with usage"]
        if expected == "unmarked":
            return [] if not calls and json.loads("".join(text)) == {"name": "echo", "arguments": {"text": "hello"}} else ["Unmarked stream JSON differs from baseline text"]
        if (len(calls) != len(expected) or len({c["id"] for c in calls.values()}) != len(calls)
                or any(not c["id"] or c["name"] != "echo" for c in calls.values())
                or any(set(json.loads(c["arguments"])) != {"text"} for c in calls.values())
                or sorted(json.loads(c["arguments"])["text"] for c in calls.values()) != sorted(expected)):
            return ["Incorrect automatic streamed calls"]
        if not expected and "".join(text).strip().lower() != "hello":
            return ["Automatic stream did not decline tools"]
        if "<tool_call>" in "".join(text):
            return ["Automatic stream leaked tool markers"]
        return []
    except (KeyError, IndexError, TypeError, ValueError):
        return ["Malformed automatic stream"]


def read_stream(client, protocol, body):
    events, done = [], 0
    with client.stream("POST", "/v1/" + protocol, json={**body, "stream": True}) as streamed:
        for line in streamed.iter_lines():
            if line.startswith("data:"):
                if line[5:].strip() == "[DONE]":
                    done += 1
                else:
                    events.append(json.loads(line[5:]))
    return streamed.status_code, events, done


def limited_errors(protocol, status, events, done):
    if status != 200 or any("error" in e or e.get("type") == "error" for e in events):
        return ["Automatic limited stream failed"]
    if protocol == "chat/completions":
        finish = [c["finish_reason"] for e in events for c in e.get("choices", []) if c.get("finish_reason")]
        return [] if done == 1 and finish == ["length"] else ["Automatic Chat length termination missing"]
    if protocol == "responses":
        terminal = [e for e in events if e.get("type") == "response.incomplete"]
        return [] if len(terminal) == 1 and terminal[0]["response"]["incomplete_details"]["reason"] == "max_output_tokens" else ["Automatic Responses truncation missing"]
    finish = [e["delta"]["stop_reason"] for e in events if e.get("type") == "message_delta"]
    return [] if finish == ["max_tokens"] and sum(e.get("type") == "message_stop" for e in events) == 1 else ["Automatic Messages truncation missing"]


def cancel_case(client, protocol, body, native_port):
    body = json.loads(json.dumps(body))
    schema = {"type": "object", "properties": {"text": {"type": "string", "const": " ".join(["hello"] * 1000)}},
              "required": ["text"], "additionalProperties": False}
    body["max_output_tokens" if protocol == "responses" else "max_tokens"] = 2048
    if protocol == "responses":
        body["tools"][0]["parameters"] = schema
    elif protocol == "messages":
        body["tools"][0]["input_schema"] = schema
    else:
        body["tools"][0]["function"]["parameters"] = schema
    with httpx.Client(base_url=f"http://127.0.0.1:{native_port}", timeout=10, trust_env=False) as observer:
        with client.stream("POST", "/v1/" + protocol, json={**body, "stream": True}) as response:
            if response.status_code != 200:
                return {"case": protocol.replace("/", "_") + "_automatic_cancel", "status": response.status_code,
                        "semantic_errors": ["Automatic cancellation stream refused"]}
            rows = response.iter_lines()
            for line in rows:
                if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                    continue
                event = json.loads(line[5:])
                active = (event.get("type") == "response.function_call_arguments.delta"
                          or (isinstance(event.get("delta"), dict) and event["delta"].get("type") == "input_json_delta")
                          or any(c.get("delta", {}).get("tool_calls") for c in event.get("choices", [])))
                if active:
                    break
            else:
                return {"case": protocol.replace("/", "_") + "_automatic_cancel", "status": 200,
                        "semantic_errors": ["Automatic argument stream did not start"]}
            before = observer.get("/slots").json()
        deadline = time.monotonic() + 10
        after = observer.get("/slots").json()
        while any(s["state"] != 0 for s in after) and time.monotonic() < deadline:
            time.sleep(.05)
            after = observer.get("/slots").json()
        errors = []
        if not any(s["state"] != 0 for s in before):
            errors.append("Automatic stream finished before disconnect")
        if any(s["state"] != 0 or s["next_token"]["n_decoded"] >= 1000 for s in after):
            errors.append("Automatic disconnect did not settle active slots")
        return {"case": protocol.replace("/", "_") + "_automatic_cancel", "status": 200,
                "semantic_errors": errors, "before": before, "after": after}


def cases(port, model, headers, native_port=None, include_logprobs=False):
    results = []
    schema = {"type": "object", "properties": {"text": {"type": "string", "enum": ["hello", "world"]}},
              "required": ["text"], "additionalProperties": False}
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers=headers, timeout=180, trust_env=False) as client:
        for protocol in ("chat/completions", "responses", "messages"):
            for mode, expected, prompt in (
                ("auto", ["hello"], "Call echo. Return exactly this tool call: <tool_call>\n{\"name\":\"echo\",\"arguments\":{\"text\":\"hello\"}}\n</tool_call>"),
                ("baseline_choice", "auto_choice", "Call echo with text hello. Output only the tool call."),
                ("required", ["hello"], "Call echo with text hello."),
                ("reasoning", ["hello"], "Call echo with text hello."),
                ("decline", [], "Do not call any tools. Output exactly the lowercase word hello and no other text."),
                ("parallel", ["hello", "world"], "Make two separate echo tool calls: first text hello, then text world. Output only these two calls."),
            ):
                body = {"model": model, "temperature": 0, "seed": 0, "chat_template_kwargs": {"enable_thinking": False},
                        "parallel_tool_calls": mode == "parallel"}
                choice = "required" if mode in ("required", "parallel", "reasoning") else "auto"
                if mode == "reasoning":
                    body["chat_template_kwargs"]["enable_thinking"] = True
                if protocol == "responses":
                    body.update(input=prompt, max_output_tokens=512, tool_choice=choice,
                                tools=[{"type": "function", "name": "echo", "parameters": schema, "strict": True}])
                else:
                    body.update(messages=[{"role": "user", "content": prompt}], max_tokens=512)
                    if protocol == "messages":
                        body.pop("seed", None)
                        body.update(thinking={"type": "disabled"}, tools=[{"name": "echo", "input_schema": schema}],
                                    tool_choice={"type": "any" if choice == "required" else "auto", "disable_parallel_tool_use": mode != "parallel"})
                        if mode == "reasoning":
                            body.pop("thinking")
                    else:
                        body.update(tools=[{"type": "function", "function": {"name": "echo", "parameters": schema, "strict": True}}], tool_choice=choice)
                response = client.post("/v1/" + protocol, json=body)
                data = response.json()
                errors = response_errors(protocol, data, expected) if response.status_code == 200 else [f"HTTP {response.status_code}"]
                if mode == "reasoning" and response.status_code == 200:
                    reasoning = (data["choices"][0]["message"].get("reasoning_content", "") if protocol == "chat/completions" else
                                 "".join(p["text"] for i in data["output"] if i["type"] == "reasoning" for p in i["summary"]) if protocol == "responses" else
                                 "".join(b["thinking"] for b in data["content"] if b["type"] == "thinking"))
                    if not reasoning.strip():
                        errors.append("Reasoning was not separated from tool arguments")
                results.append({"case": protocol.replace("/", "_") + "_automatic_" + mode, "status": response.status_code,
                                "body": data, "semantic_errors": errors})
                streamed_body = body
                if include_logprobs and protocol == "chat/completions" and mode == "auto":
                    streamed_body = {**body, "logprobs": True, "top_logprobs": 3}
                status, events, done = read_stream(client, protocol, streamed_body)
                errors = stream_errors(protocol, events, expected, done) if status == 200 else [f"HTTP {status}"]
                if streamed_body.get("logprobs") and status == 200:
                    entries = [p for e in events for c in e.get("choices", []) for p in (c.get("logprobs") or {}).get("content", [])]
                    errors += logprob_errors(entries, 3)
                    usages = [e["usage"] for e in events if e.get("usage")]
                    if len(usages) != 1 or len(entries) != usages[0]["completion_tokens"]:
                        errors.append("Automatic stream logprobs differ from generated usage")
                if mode == "reasoning" and status == 200:
                    reasoning = "".join(c.get("delta", {}).get("reasoning_content", "") for e in events for c in e.get("choices", [])) if protocol == "chat/completions" else (
                        "".join(e["delta"] for e in events if e.get("type") == "response.reasoning_summary_text.delta") if protocol == "responses" else
                        "".join(e["delta"]["thinking"] for e in events if e.get("type") == "content_block_delta" and e["delta"]["type"] == "thinking_delta"))
                    if not reasoning.strip():
                        errors.append("Reasoning stream used no reasoning channel")
                results.append({"case": protocol.replace("/", "_") + "_automatic_" + mode + "_stream", "status": status,
                                "events": events, "semantic_errors": errors})
                if mode == "required" and response.status_code == 200:
                    rejected = client.post("/v1/" + protocol, json={**body, "__prima_auto_chat": {}})
                    results.append({"case": protocol.replace("/", "_") + "_automatic_reserved", "status": rejected.status_code,
                                    "semantic_errors": [] if rejected.status_code == 400 else ["Internal parser configuration was not refused"]})
                    limited = {**body, "max_output_tokens" if protocol == "responses" else "max_tokens": 1}
                    status, events, done = read_stream(client, protocol, limited)
                    results.append({"case": protocol.replace("/", "_") + "_automatic_limit", "status": status,
                                    "events": events, "semantic_errors": limited_errors(protocol, status, events, done)})
                    if native_port:
                        results.append(cancel_case(client, protocol, body, native_port))
                    follow = {**body, "tool_choice": "none"}
                    prompt = "Output exactly the lowercase word hello and no other text."
                    if protocol == "responses":
                        follow["input"] = [{"role": "user", "content": body["input"]}, *data["output"],
                            *[{"type": "function_call_output", "call_id": c["call_id"], "output": "hello"}
                              for c in data["output"] if c["type"] == "function_call"], {"role": "user", "content": prompt}]
                    elif protocol == "messages":
                        follow["messages"] = [*body["messages"], {"role": "assistant", "content": data["content"]},
                            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": c["id"], "content": "hello"}
                              for c in data["content"] if c["type"] == "tool_use"]}, {"role": "user", "content": prompt}]
                    else:
                        message = data["choices"][0]["message"]
                        follow["messages"] = [*body["messages"], message,
                            *[{"role": "tool", "tool_call_id": c["id"], "content": "hello"} for c in message["tool_calls"]],
                            {"role": "user", "content": prompt}]
                    continued = client.post("/v1/" + protocol, json=follow)
                    payload = continued.json()
                    errors = response_errors(protocol, payload, []) if continued.status_code == 200 else [f"HTTP {continued.status_code}"]
                    results.append({"case": protocol.replace("/", "_") + "_automatic_history", "status": continued.status_code,
                                    "body": payload, "semantic_errors": errors})
                    status, events, done = read_stream(client, protocol, follow)
                    errors = stream_errors(protocol, events, [], done) if status == 200 else [f"HTTP {status}"]
                    results.append({"case": protocol.replace("/", "_") + "_automatic_history_stream", "status": status,
                                    "events": events, "semantic_errors": errors})
    return results
