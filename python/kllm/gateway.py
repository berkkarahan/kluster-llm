"""Thin OpenAI / Anthropic proxies to local llama-server."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.responses import Response


def infer_base(port: int) -> str:
    return f"http://127.0.0.1:{port}"


async def _upstream_reachable(port: int) -> bool:
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            r = await client.get(f"{infer_base(port)}/health")
            return r.status_code < 500
    except Exception:
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                r = await client.get(f"{infer_base(port)}/v1/models")
                return r.status_code < 500
        except Exception:
            return False


async def require_inference(port: int) -> None:
    if not await _upstream_reachable(port):
        raise HTTPException(
            503,
            "inference not running — use `kllm run --model …` or dashboard Plan & Run",
        )


def _openai_tools_passthrough(body: dict[str, Any]) -> dict[str, Any]:
    """Keep tools / tool_choice / stop for llama-server when present."""
    return body


async def proxy_chat_completions(port: int, request: Request) -> Response:
    await require_inference(port)
    body = await request.json()
    body = _openai_tools_passthrough(body)
    stream = bool(body.get("stream"))
    url = f"{infer_base(port)}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}

    if stream:
        return StreamingResponse(
            _stream_upstream(url, body, headers),
            media_type="text/event-stream",
        )

    async with httpx.AsyncClient(timeout=None) as client:
        r = await client.post(url, json=body, headers=headers)
        return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


async def _stream_upstream(url: str, body: dict[str, Any], headers: dict[str, str]) -> AsyncIterator[bytes]:
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream("POST", url, json=body, headers=headers) as resp:
            async for chunk in resp.aiter_bytes():
                yield chunk


def _anthropic_to_openai(body: dict[str, Any]) -> dict[str, Any]:
    """Minimal Anthropic Messages → OpenAI Chat Completions mapping."""
    messages_out: list[dict[str, Any]] = []
    system = body.get("system")
    if isinstance(system, str) and system.strip():
        messages_out.append({"role": "system", "content": system})
    elif isinstance(system, list):
        text = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in system
        )
        if text.strip():
            messages_out.append({"role": "system", "content": text})

    for msg in body.get("messages") or []:
        role = msg.get("role", "user")
        content = msg.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif isinstance(part, str):
                    parts.append(part)
            content = "\n".join(parts)
        messages_out.append({"role": role, "content": content or ""})

    out: dict[str, Any] = {
        "model": body.get("model", "default"),
        "messages": messages_out,
        "max_tokens": body.get("max_tokens", 1024),
        "stream": bool(body.get("stream")),
    }
    if "temperature" in body:
        out["temperature"] = body["temperature"]
    if "stop_sequences" in body:
        out["stop"] = body["stop_sequences"]
    if "tools" in body:
        # Map Anthropic tools → OpenAI functions shape when possible
        tools = []
        for t in body["tools"]:
            if not isinstance(t, dict):
                continue
            name = t.get("name")
            if not name:
                continue
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                    },
                }
            )
        if tools:
            out["tools"] = tools
    tc = body.get("tool_choice")
    if isinstance(tc, dict):
        t = tc.get("type")
        if t == "auto":
            out["tool_choice"] = "auto"
        elif t == "any":
            out["tool_choice"] = "required"
        elif t == "none":
            out["tool_choice"] = "none"
        elif t == "tool" and tc.get("name"):
            out["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
    return out


def _openai_chunk_to_anthropic_sse(data: dict[str, Any], msg_id: str) -> list[str]:
    """Convert one OpenAI stream chunk into Anthropic SSE event lines."""
    events: list[str] = []
    choices = data.get("choices") or []
    if not choices:
        return events
    delta = choices[0].get("delta") or {}
    finish = choices[0].get("finish_reason")
    if "role" in delta:
        events.append(
            "event: message_start\ndata: "
            + json.dumps(
                {
                    "type": "message_start",
                    "message": {
                        "id": msg_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": data.get("model", ""),
                        "stop_reason": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                }
            )
        )
        events.append(
            "event: content_block_start\ndata: "
            + json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        )
    text = delta.get("content")
    if text:
        events.append(
            "event: content_block_delta\ndata: "
            + json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})
        )
    if finish:
        events.append(
            "event: content_block_stop\ndata: " + json.dumps({"type": "content_block_stop", "index": 0})
        )
        events.append(
            "event: message_delta\ndata: "
            + json.dumps({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 0}})
        )
        events.append("event: message_stop\ndata: " + json.dumps({"type": "message_stop"}))
    return events


async def proxy_anthropic_messages(port: int, request: Request) -> Response:
    await require_inference(port)
    body = await request.json()
    oai = _anthropic_to_openai(body)
    stream = bool(oai.get("stream"))
    url = f"{infer_base(port)}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}

    if not stream:
        async with httpx.AsyncClient(timeout=None) as client:
            r = await client.post(url, json=oai, headers=headers)
            if r.status_code >= 400:
                return Response(content=r.content, status_code=r.status_code, media_type="application/json")
            data = r.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            text = message.get("content") or ""
            anthropic = {
                "id": data.get("id", "msg_kllm"),
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "model": data.get("model", oai.get("model")),
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": (data.get("usage") or {}).get("prompt_tokens", 0),
                    "output_tokens": (data.get("usage") or {}).get("completion_tokens", 0),
                },
            }
            return Response(content=json.dumps(anthropic).encode(), media_type="application/json")

    async def gen() -> AsyncIterator[bytes]:
        msg_id = "msg_kllm_stream"
        started = False
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, json=oai, headers=headers) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        if started:
                            for ev in (
                                "event: content_block_stop\ndata: "
                                + json.dumps({"type": "content_block_stop", "index": 0}),
                                "event: message_delta\ndata: "
                                + json.dumps(
                                    {
                                        "type": "message_delta",
                                        "delta": {"stop_reason": "end_turn"},
                                        "usage": {"output_tokens": 0},
                                    }
                                ),
                                "event: message_stop\ndata: " + json.dumps({"type": "message_stop"}),
                            ):
                                yield (ev + "\n\n").encode()
                        break
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if not started:
                        # Force message_start even if role omitted
                        for ev in _openai_chunk_to_anthropic_sse(
                            {"choices": [{"delta": {"role": "assistant"}}], "model": data.get("model", "")},
                            msg_id,
                        ):
                            yield (ev + "\n\n").encode()
                        started = True
                    for ev in _openai_chunk_to_anthropic_sse(data, msg_id):
                        if "message_start" in ev or "content_block_start" in ev:
                            continue  # already emitted
                        yield (ev + "\n\n").encode()

    return StreamingResponse(gen(), media_type="text/event-stream")
