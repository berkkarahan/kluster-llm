"""Diagnostic binaries must never masquerade as timed acceptance builds."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

import prima_assessment as assessment
import prima_local_benchmark as paired


def test_development_probe_uses_actual_capabilities_and_keeps_published_record(tmp_path, monkeypatch):
    binary = tmp_path / "server with spaces.exe"
    binary.write_bytes(b"diagnostic binary")
    original = {"capabilities": {"features": ["published"]}, "artifacts": {"other": "retained"}}
    capabilities = {"features": ["diagnostic_vulkan_timing_v1"]}
    def probe(command, **kwargs):
        assert command == [str(binary), "--list-devices"]
        assert kwargs["timeout"] == 60 and kwargs["check"] is True
        return SimpleNamespace(stdout="KLLM_PRIMA_CAPABILITIES=" + json.dumps(capabilities))
    monkeypatch.setattr(assessment.subprocess, "run", probe)
    observed = assessment.capture_development_runtime(original, binary)
    assert observed["capabilities"] == capabilities
    assert observed["artifacts"][binary.name] == assessment.file_digest(binary)
    assert original == {"capabilities": {"features": ["published"]}, "artifacts": {"other": "retained"}}


@pytest.mark.parametrize("reports", [[], [{"features": "invalid"}], [{"features": [1]}],
    [{"features": []}, {"features": []}]])
def test_development_probe_refuses_ambiguous_or_malformed_reports(tmp_path, monkeypatch, reports):
    binary = tmp_path / "server.exe"
    binary.write_bytes(b"binary")
    monkeypatch.setattr(assessment.subprocess, "run", lambda *a, **k: SimpleNamespace(
        stdout="\n".join("KLLM_PRIMA_CAPABILITIES=" + json.dumps(r) for r in reports)))
    with pytest.raises(ValueError, match="capability report"):
        assessment.capture_development_runtime({}, binary)


def test_development_probe_refuses_binary_replacement(tmp_path, monkeypatch):
    binary = tmp_path / "server.exe"
    binary.write_bytes(b"before")
    def probe(*args, **kwargs):
        binary.write_bytes(b"after")
        return SimpleNamespace(stdout='KLLM_PRIMA_CAPABILITIES={"features":[]}')
    monkeypatch.setattr(assessment.subprocess, "run", probe)
    with pytest.raises(ValueError, match="digest"):
        assessment.capture_development_runtime({}, binary)


def test_paired_measurement_refuses_diagnostics_before_assets_or_processes(monkeypatch):
    def unexpected(*args):
        pytest.fail("Diagnostic refusal must precede asset resolution")
    monkeypatch.setattr(paired, "asset_files", unexpected)
    with pytest.raises(ValueError, match="Diagnostic Vulkan timing"):
        paired.benchmark({"capabilities": {"features": ["diagnostic_vulkan_timing_v1"]}},
                         Path("unused"), Path("unused"), 1, "unused")


def test_owned_server_failure_retains_logs_after_process_settles(tmp_path, monkeypatch):
    original = paired.subprocess.Popen
    children = []
    script = (
        "import sys; from http.server import BaseHTTPRequestHandler,HTTPServer\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        " def do_GET(self): self.send_response(200); self.end_headers()\n"
        " def log_message(self,*args): pass\n"
        "print('fixture ready',flush=True)\n"
        "HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()\n"
    )
    def launch(command, **kwargs):
        port = command[command.index("--port") + 1]
        child = original([sys.executable, "-u", "-c", script, port], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(paired.subprocess, "Popen", launch)
    retained = tmp_path / "retained.log"
    with pytest.raises(RuntimeError, match="controlled failure"):
        with paired.server(Path("fixture"), Path("unused"), {}, [], tmp_path, 512, log_copy=retained):
            raise RuntimeError("controlled failure")
    assert children and children[0].poll() is not None
    assert "fixture ready" in retained.read_text()
