"""Compare all bundled presets with Python Jinja; no inference or performance gate.

Run with ``uv run --with jinja2 python tests/prima_template_assessment.py ...``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from jinja2.sandbox import SandboxedEnvironment

from kllm.modules.inference.interactors.chat_templates import PRESETS, selected_template_text
from kllm.modules.runtimes.interactors.registry import file_digest


def fail(message):
    raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    environment = SandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
    environment.globals["raise_exception"] = fail
    checks = []
    initial_hash = file_digest(args.binary)
    with tempfile.TemporaryDirectory(prefix="prima template correctness ") as temp:
        root = Path(temp)
        for name in PRESETS:
            source = selected_template_text(name, None, "")
            template_file = root / "selected template.jinja"
            template_file.write_text(source, encoding="utf-8")
            for thinking in (False, True):
                for add in (False, True):
                    messages = [{"role": "user", "content": "Hello İstanbul 🐾"}]
                    body = {"messages": messages, "add_generation_prompt": add,
                            "chat_template_kwargs": {"enable_thinking": thinking}}
                    input_file = root / "input.json"
                    input_file.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
                    result = subprocess.run([str(args.binary.resolve()), str(template_file), str(input_file)],
                                            capture_output=True, text=True, encoding="utf-8", timeout=60)
                    if result.returncode:
                        raise ValueError(f"{name}: native renderer failed: {result.stderr[-1000:]}")
                    actual = json.loads(result.stdout)["prompt"]
                    expected = environment.from_string(source).render(
                        messages=messages, bos_token="", eos_token="", enable_thinking=thinking,
                        add_generation_prompt=add)
                    checks.append({"preset": name, "enable_thinking": thinking,
                                   "add_generation_prompt": add, "passed": actual == expected,
                                   "native_prompt": actual, "reference_prompt": expected})
    if initial_hash != file_digest(args.binary):
        raise ValueError("Template test binary changed during assessment")
    receipt = {"checks": checks, "binary_sha256": initial_hash,
               "scope": "preset rendering correctness; not model or performance qualification"}
    args.output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"checks": len(checks), "passed": all(x["passed"] for x in checks)}))
    return 0 if checks and all(x["passed"] for x in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
