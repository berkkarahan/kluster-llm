"""Five identical streaming requests; writes timings without prompts or credentials.

Run before and after a controlled restart with the same model/settings.
No parameters are tuned. Use --output to retain each comparison artifact.
"""

import argparse
import json
import statistics
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:18765")
    parser.add_argument("--token-file", type=Path, default=Path.home() / ".kllm/api_token")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    headers = {"Authorization": "Bearer " + args.token_file.read_text().strip()}
    body = {
        "model": "default",
        "messages": [
            {"role": "user", "content": "Explain why the sky is blue in a short paragraph."}
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": 64,
        "seed": 1234,
        "cache_prompt": False,
    }
    rows = []
    with httpx.Client(timeout=300, trust_env=False) as client:
        for _ in range(5):
            start = time.perf_counter()
            first = None
            usage = {}
            timings = {}
            with client.stream(
                "POST", args.base + "/v1/chat/completions", headers=headers, json=body
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    data = json.loads(line[6:])
                    delta = (data.get("choices") or [{}])[0].get("delta") or {}
                    if first is None and any(
                        delta.get(k) for k in ("content", "reasoning_content", "reasoning")
                    ):
                        first = time.perf_counter() - start
                    usage = data.get("usage") or usage
                    timings = data.get("timings") or timings
            elapsed = time.perf_counter() - start
            tokens = usage.get("completion_tokens")
            row = dict(
                ttft_s=first,
                elapsed_s=elapsed,
                tokens=tokens,
                generation_tps=timings.get("predicted_per_second")
                or ((tokens - 1) / (elapsed - first) if tokens and first is not None else None),
            )
            rows.append(row)
            print(json.dumps(row), flush=True)
    summary = {}
    for key in ("ttft_s", "elapsed_s", "generation_tps"):
        values = [r[key] for r in rows if r[key] is not None]
        if values:
            summary[key] = dict(
                median=statistics.median(values), minimum=min(values), maximum=max(values)
            )
    args.output.write_text(json.dumps(dict(rows=rows, summary=summary), indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
