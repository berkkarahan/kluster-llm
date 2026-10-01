"""Real helper/binding boundary checks; no cluster or user runtime mutations."""

import subprocess
import sys
import time
import concurrent.futures
import json
import os
import psutil
import queue
import threading
import re
import socket
import httpx
from datetime import datetime, timezone

import pytest

from kllm import native

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not native.resource_probe_available(), reason="requires rebuilt native extension")]
PYTHON = (getattr(sys, "_base_executable", None) or sys.executable) if sys.platform == "win32" else sys.executable


@pytest.fixture(autouse=True)
def controlled_gpu_source(monkeypatch):
    """Default checks exercise pipes/bindings without a GPU driver dependency."""
    from kllm import usage_probe

    if os.environ.get("KLLM_TEST_HARDWARE") == "1":
        return

    original = usage_probe.helper_command
    code = (
        "import sys,json; from kllm.usage_probe import failure; "
        "exec('for line in sys.stdin:\\n r=json.loads(line)\\n "
        "o={k:r[k] for k in (\"version\",\"generation\",\"request_id\")}\\n "
        "o[\"result\"]=failure(\"gpu\",\"unsupported_counter\")\\n "
        "print(json.dumps(o),flush=True)')"
    )
    monkeypatch.setattr(usage_probe, "helper_command", lambda source: (
        [PYTHON, "-c", code] if source == "gpu" else original(source)))


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="new host collector targets Windows/macOS")
def test_host_only_probe_and_invalid_selection():
    try:
        sample = native.probe_resources(node_id="test-node", managed_processes=[])
        assert sample["schema_version"] == 1
        assert sample["processes"] == []
        assert sample["host"]["memory"]["total"]["status"] == "measured"
        assert sample["snapshot_id"] == f"{sample['generation']}:{sample['sequence']}"
        for key in ("total", "used", "free", "available"):
            metric = sample["host"]["memory"][key]
            assert metric["unit"] == "bytes"
            assert metric["observed_at"].endswith("Z")
            assert (metric["value"] is not None) == (metric["status"] == "measured")
        with pytest.raises(ValueError):
            native.probe_resources(node_id="test-node", managed_processes=[{"pid": True}])
    finally:
        native.close_resource_probes()


def test_private_binding_rejects_malformed_requests():
    collector = native.require_native()._UsageCollector("host")
    for request in ('{}', '{"version":1,"version":1}', '{"value":NaN}', 'x' * 65537):
        with pytest.raises(ValueError):
            collector.collect_json(request)


def test_helper_rejects_unterminated_oversized_frame():
    from kllm import usage_probe
    child = subprocess.Popen(
        usage_probe.helper_command("host"), env=usage_probe.helper_environment(),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        out, _ = child.communicate(b"x" * 65537, timeout=5)
        assert child.returncode == 2
        assert not out
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


@pytest.mark.parametrize("blocked", ["gpu", "host"])
def test_hung_source_is_bounded_and_closed(monkeypatch, blocked):
    from kllm import usage_probe

    native.close_resource_probes()
    original = usage_probe.helper_command
    monkeypatch.setattr(usage_probe, "helper_command", lambda source: (
        [PYTHON, "-c", "import time; time.sleep(60)"]
        if source == blocked else original(source)
    ))
    children = []
    try:
        began = time.monotonic()
        result = native.probe_resources(node_id="timeout-node")
        assert time.monotonic() - began < 2
        if blocked == "gpu":
            assert result["device_discovery"]["status"] == "error"
            assert result["device_discovery"]["reason"] == "timeout"
            assert result["devices"] == []
            assert result["host"]["memory"]["total"]["status"] in ("measured", "unsupported")
        else:
            assert result["host"]["memory"]["total"]["reason"] == "timeout"
            assert result["device_discovery"]["reason"] == "unsupported_counter"
        children = [slot.process for slot in usage_probe._slots.values() if slot.process]
    finally:
        native.close_resource_probes()
    assert all(child.poll() is not None for child in children)


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="new host collector targets Windows/macOS")
def test_concurrent_calls_share_bounded_helpers():
    from kllm import usage_probe

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: native.probe_resources(node_id="concurrent"), range(4)))
        assert len({r["snapshot_id"] for r in results}) == 4
        assert len(usage_probe._slots) <= 2
        assert all(r["host"]["memory"]["total"]["status"] == "measured" for r in results)
    finally:
        native.close_resource_probes()


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="new process collector targets Windows/macOS")
def test_registered_process_rss_baseline_and_exit(tmp_path, monkeypatch):
    from kllm import runtime

    monkeypatch.setattr(runtime, "runtime_path", lambda: tmp_path / "runtime.json")
    monkeypatch.setattr(runtime, "ensure_home", lambda: None)
    child = subprocess.Popen([PYTHON, "-c", "import time; time.sleep(60)"])
    try:
        token, started = native.require_native()._process_identity(child.pid)
        runtime.save(runtime.RuntimeInfo(children=[runtime.ChildProc(child.pid, "llama-server", token, started)]))
        selection = dict(pid=child.pid, role="master", start_token=token, started_at=started)
        first = native.probe_resources(node_id="process-test", managed_processes=[selection])
        point = first["processes"][0]
        assert point["identity_verified"] is True
        assert point["rss"]["value"] > 0
        assert point["cpu"]["status"] == "initializing"
        time.sleep(0.25)
        second = native.probe_resources(node_id="process-test", managed_processes=[selection])
        assert second["processes"][0]["cpu"]["status"] == "measured"
        # Simulate an obsolete registration for this PID, even with the same coarse timestamp.
        runtime.save(runtime.RuntimeInfo(children=[runtime.ChildProc(child.pid, "llama-server", "0", started)]))
        mismatch = native.probe_resources(node_id="process-test", managed_processes=[dict(selection, start_token="0")])
        assert mismatch["processes"][0]["state"] == "exited"
        assert mismatch["processes"][0]["rss"]["value"] is None
        assert mismatch["processes"][0]["cpu"]["reason"] == "process_identity_mismatch"
        runtime.save(runtime.RuntimeInfo(children=[runtime.ChildProc(child.pid, "llama-server", token, started)]))
        child.terminate()
        child.wait(timeout=5)
        exited = native.probe_resources(node_id="process-test", managed_processes=[selection])
        assert exited["processes"][0]["rss"]["value"] is None
        assert exited["host"]["memory"]["total"]["status"] == "measured"
    finally:
        native.close_resource_probes()
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


def test_source_schema_rejects_scope_and_bad_host():
    from kllm import usage_probe

    source = usage_probe.failure("host", "source_error")
    validate = native.require_native()._validate_source_json
    validate("host", json.dumps(source))
    source["host"]["memory"]["total"]["scope"] = "process"
    with pytest.raises(ValueError):
        validate("host", json.dumps(source))
    source["host"] = None
    with pytest.raises(ValueError):
        validate("host", json.dumps(source))


def test_shared_pool_references_are_not_duplicate_physical_memory():
    from kllm import usage_probe

    source = usage_probe.failure("gpu", "unsupported_counter")
    missing = usage_probe.metric("bytes", "device", "unsupported_counter")
    source["device_discovery"].update(status="measured", reason=None)
    for index in range(2):
        source["devices"].append(dict(device_key=f"gpu:{index}", name="same GPU name", vendor="test",
            memory_kind="shared", inventory_device_id=None, memory=[dict(kind="shared", pool_id="host:ram",
            backing_pool_id="host:ram", total=missing.copy(), used=missing.copy(), free=missing.copy())]))
    native.require_native()._validate_source_json("gpu", json.dumps(source))
    host = usage_probe.failure("host", "source_error")["host"]
    host["memory"]["total"].update(value=16 * 1024**3, status="measured", reason=None)
    totals = usage_probe.memory_totals(host, source)
    assert totals["physical_capacity"]["value"] == 16 * 1024**3
    assert totals["physical_capacity"]["included_pool_ids"] == ["host:ram"]
    source["devices"][1]["memory"][0]["backing_pool_id"] = "other"
    with pytest.raises(ValueError):
        native.require_native()._validate_source_json("gpu", json.dumps(source))


@pytest.mark.parametrize("mode", ["generation", "duplicate", "nonfinite", "scope", "oversize"])
def test_malformed_helper_response_preserves_the_other_source(monkeypatch, mode):
    from kllm import usage_probe

    native.close_resource_probes()
    original = usage_probe.helper_command
    script = (
        "import sys,json; from kllm.usage_probe import failure; "
        "r=json.loads(sys.stdin.readline()); "
        "o={k:r[k] for k in ('version','generation','request_id')}; "
        f"o['result']=failure({'host' if mode == 'scope' else 'gpu'!r},'source_error'); "
    )
    if mode == "generation":
        script += "o['generation']='0'*32; "
    script += "s=json.dumps(o); "
    if mode == "duplicate":
        script += "s=s.replace('{','{\"version\":1,',1); "
    elif mode == "nonfinite":
        script += "s=s.replace('\"version\": 1','\"version\": NaN'); "
    elif mode == "oversize":
        script += "s='x'*65537; "
    script += "print(s,flush=True)"
    monkeypatch.setattr(usage_probe, "helper_command", lambda source: [PYTHON, "-c", script] if source == "gpu" else original(source))
    try:
        result = native.probe_resources(node_id="malformed")
        assert result["device_discovery"]["status"] == "error"
        assert result["device_discovery"]["reason"] == "source_error"
        assert result["host"]["memory"]["total"]["status"] in ("measured", "unsupported")
    finally:
        native.close_resource_probes()


def test_parent_death_cleans_owned_helpers():
    code = """
import json,sys,threading,time
from kllm import native,usage_probe
original=usage_probe.helper_command
usage_probe.helper_command=lambda source: [sys._base_executable,'-c','import sys; sys.stdin.buffer.read()'] if source=='gpu' else original(source)
threading.Thread(target=lambda: native.probe_resources(node_id='parent-death'),daemon=True).start()
while True:
    with usage_probe._lock:
        children=[s.process.pid for s in usage_probe._slots.values() if s.process and s.job]
    if len(children)==2:
        print(json.dumps(children),flush=True)
        break
    time.sleep(.005)
time.sleep(60)
"""
    from kllm import usage_probe
    parent = subprocess.Popen([PYTHON, "-c", code], env=usage_probe.helper_environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    children = []
    try:
        ready = queue.Queue(maxsize=1)
        reader = threading.Thread(target=lambda: ready.put(parent.stdout.readline()), daemon=True)
        reader.start()
        line = ready.get(timeout=5)
        children = [psutil.Process(pid) for pid in json.loads(line)]
        parent.kill()
        parent.wait(timeout=5)
        deadline = time.monotonic() + 5
        while any(child.is_running() for child in children) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not any(child.is_running() for child in children)
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        parent.stdout.close()
        parent.stderr.close()
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass


def test_helper_environment_does_not_inherit_credentials(monkeypatch):
    from kllm import usage_probe

    monkeypatch.setenv("HF_TOKEN", "test-token")
    monkeypatch.setenv("KLLM_JOIN_KEY", "test-key")
    assert "HF_TOKEN" not in usage_probe.helper_environment()
    assert "KLLM_JOIN_KEY" not in usage_probe.helper_environment()


def test_unreaped_source_is_quarantined_without_spawning_a_replacement(monkeypatch):
    from kllm import usage_probe

    native.close_resource_probes()
    command, kill = usage_probe.helper_command, usage_probe.Slot.kill
    monkeypatch.setattr(usage_probe, "helper_command", lambda source: (
        [PYTHON, "-c", "import time; time.sleep(60)"] if source == "gpu" else command(source)))
    def cannot_kill(slot, reason="timeout"):
        if slot.source == "gpu":
            with usage_probe._lock:
                slot.cancelled, slot.cancel_reason = True, reason
        else:
            kill(slot, reason)
    monkeypatch.setattr(usage_probe.Slot, "kill", cannot_kill)
    try:
        first = native.probe_resources(node_id="quarantine-test")
        assert first["device_discovery"]["reason"] == "timeout"
        process = usage_probe._slots["gpu"].process
        assert process is not None and process.poll() is None
        second = native.probe_resources(node_id="quarantine-test")
        assert second["device_discovery"]["reason"] == "source_quarantined"
        assert usage_probe._slots["gpu"].process is process
        assert len(usage_probe._slots) == 2
    finally:
        monkeypatch.setattr(usage_probe.Slot, "kill", kill)
        native.close_resource_probes()


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="requires target host CPU source")
def test_short_cpu_interval_does_not_renew_its_observation():
    native.close_resource_probes()
    try:
        first = native.probe_resources(node_id="cpu-interval")
        assert first["host"]["cpu"]["status"] == "initializing"
        deadline = time.monotonic() + 2.0
        measured = first["host"]["cpu"]
        while measured["status"] != "measured" and time.monotonic() < deadline:
            time.sleep(.05)
            measured = native.probe_resources(node_id="cpu-interval")["host"]["cpu"]
        assert measured["status"] == "measured"
        repeated = native.probe_resources(node_id="cpu-interval")["host"]["cpu"]
        assert repeated == measured
    finally:
        native.close_resource_probes()


@pytest.mark.skipif(sys.platform not in ("win32", "darwin"), reason="requires target host source")
def test_native_sampler_collects_host_resources_without_inventing_a_process(tmp_path, monkeypatch):
    from kllm import usage
    from kllm.state import ClusterState

    monkeypatch.setenv("KLLM_HOME", str(tmp_path / "state"))
    state = ClusterState(node_id="native-host-only", role="master")
    sampler = usage.Sampler(resource_probe=native.probe_resources)
    try:
        assert sampler.sample(state) is None
        assert sampler.resources["processes"] == []
        assert sampler.resources["host"]["memory"]["total"]["status"] == "measured"
    finally:
        native.close_resource_probes()


@pytest.mark.skipif(sys.platform != "darwin" or os.environ.get("KLLM_TEST_HARDWARE") != "1", reason="opt-in macOS Metal topology acceptance")
def test_hardware_macos_metal_scope_is_truthful():
    native.close_resource_probes()
    try:
        result = native.probe_resources(node_id="metal-topology")
        assert result["host"]["memory"]["total"]["status"] == "measured"
        assert result["device_discovery"]["status"] == "measured"
        if not result["devices"]:
            pytest.skip("no exposed Metal device; GPU topology acceptance remains unverified")
        keys = [d["device_key"] for d in result["devices"]]
        assert len(keys) == len(set(keys))
        for device in result["devices"]:
            assert device["memory_kind"] in ("shared", "dedicated")
            for view in device["memory"]:
                if device["memory_kind"] == "shared":
                    assert view["pool_id"] == view["backing_pool_id"] == "host:ram"
                else:
                    assert view["backing_pool_id"] is None
                assert view["used"]["status"] == view["free"]["status"] == "unsupported"
                assert view["used"]["value"] is None
    finally:
        native.close_resource_probes()


@pytest.mark.skipif(os.environ.get("KLLM_TEST_HARDWARE") != "1" or sys.platform not in ("win32", "darwin"), reason="opt-in Windows/macOS native hardware acceptance")
def test_hardware_sustained_probe_delivery(tmp_path, monkeypatch):
    from kllm import runtime, usage_probe

    monkeypatch.setattr(runtime, "runtime_path", lambda: tmp_path / "runtime.json")
    monkeypatch.setattr(runtime, "ensure_home", lambda: None)
    child = subprocess.Popen([PYTHON, "-c", "data=bytearray(64*1024*1024)\ndata[::4096]=b'x'*16384\nwhile True: pass"])
    me = psutil.Process()
    native.close_resource_probes()
    try:
        token, started = native.require_native()._process_identity(child.pid)
        runtime.save(runtime.RuntimeInfo(children=[runtime.ChildProc(child.pid, "llama-server", token, started)]))
        selection = dict(pid=child.pid, role="master", start_token=token, started_at=started)
        def sample(_):
            began = time.monotonic()
            result = native.probe_resources(node_id="hardware-check", managed_processes=[selection])
            return time.monotonic() - began, result
        sample(0)
        time.sleep(2)
        _, warm = sample(0)
        assert warm["host"]["cpu"]["status"] == "measured"
        assert warm["processes"][0]["cpu"]["status"] == "measured"
        required_gpu = [(d["device_key"], m["kind"], field) for d in warm["devices"] for m in d["memory"]
                        for field in ("used", "free") if m[field]["status"] == "measured"]
        def rss_tree():
            return me.memory_info().rss + sum(psutil.Process(s.process.pid).memory_info().rss
                for s in usage_probe._slots.values() if s.process and s.process.poll() is None)
        durations = []
        successful = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(sample, range(4)))
            baseline_tree, baseline_parent = rss_tree(), me.memory_info().rss
            handles = me.num_handles() if sys.platform == "win32" else me.num_fds()
            began = time.monotonic()
            for cycle in range(300):
                time.sleep(max(0, began + cycle * 2 - time.monotonic()))
                for duration, result in pool.map(sample, range(4)):
                    durations.append(duration)
                    metrics = [result["host"]["cpu"], result["host"]["memory"]["used"],
                               result["host"]["memory"]["available"], result["processes"][0]["cpu"], result["processes"][0]["rss"]]
                    devices = {d["device_key"]: d for d in result["devices"]}
                    missing = False
                    for key, kind, field in required_gpu:
                        try:
                            metrics.append(next(m[field] for m in devices[key]["memory"] if m["kind"] == kind))
                        except (KeyError, StopIteration):
                            missing = True
                    now = datetime.now(timezone.utc)
                    ok = not missing and all(m["status"] == "measured" and 0 <= (now - datetime.fromisoformat(m["observed_at"].replace("Z", "+00:00"))).total_seconds() <= 2 for m in metrics)
                    successful += int(ok)
                assert len(usage_probe._slots) == 2
            time.sleep(max(0, began + 600 - time.monotonic()))
            growth = rss_tree() - baseline_tree
        native.close_resource_probes()
        handle_growth = (me.num_handles() if sys.platform == "win32" else me.num_fds()) - handles
        report = dict(requests=len(durations), successful=successful, p95=sorted(durations)[1139], maximum=max(durations),
                      tree_growth=growth, retained_parent_growth=me.memory_info().rss-baseline_parent, handle_growth=handle_growth,
                      gpu_names=[d["name"] for d in warm["devices"]])
        print(json.dumps(report, indent=2))
        assert successful >= 1140
        assert report["p95"] <= 1 and report["maximum"] <= 2
        assert growth <= 16 * 1024**2 and report["retained_parent_growth"] <= 16 * 1024**2 and handle_growth <= 5
    finally:
        native.close_resource_probes()
        child.kill()
        child.wait(timeout=5)


@pytest.mark.skipif(os.environ.get("KLLM_TEST_HARDWARE") != "1" or sys.platform not in ("win32", "darwin"), reason="opt-in Windows/macOS native hardware acceptance")
def test_hardware_host_and_process_accuracy(tmp_path, monkeypatch):
    from kllm import runtime

    monkeypatch.setattr(runtime, "runtime_path", lambda: tmp_path / "runtime.json")
    monkeypatch.setattr(runtime, "ensure_home", lambda: None)
    child = subprocess.Popen([PYTHON, "-c", "data=bytearray(64*1024*1024)\ndata[::4096]=b'x'*16384\nwhile True: pass"])
    native.close_resource_probes()
    try:
        token, started = native.require_native()._process_identity(child.pid)
        runtime.save(runtime.RuntimeInfo(children=[runtime.ChildProc(child.pid, "llama-server", token, started)]))
        selection = dict(pid=child.pid, role="master", start_token=token, started_at=started)
        process = psutil.Process(child.pid)
        native.probe_resources(node_id="reference-check", managed_processes=[selection])
        psutil.cpu_percent(None)
        process.cpu_percent(None)
        counts = dict(host_cpu=0, process_cpu=0, rss=0, total=0, used=0, available=0)
        pairs = []
        for _ in range(30):
            time.sleep(2)
            at = time.monotonic()
            point = native.probe_resources(node_id="reference-check", managed_processes=[selection])
            cpu, process_cpu = psutil.cpu_percent(None), process.cpu_percent(None) / psutil.cpu_count()
            memory, rss = psutil.virtual_memory(), process.memory_info().rss
            expected = dict(total=memory.total, used=memory.used, available=memory.available)
            if sys.platform == "darwin":
                text = subprocess.check_output(["/usr/bin/vm_stat"], text=True, timeout=1)
                page_size = int(re.search(r"page size of (\d+) bytes", text)[1])
                values = {name: int(re.search(rf"{name}:\s+(\d+)", text)[1]) for name in ("Pages free", "Pages active", "Pages inactive", "Pages wired down")}
                expected.update(used=(values["Pages active"] + values["Pages wired down"]) * page_size,
                                available=(values["Pages free"] + values["Pages inactive"]) * page_size)
            assert time.monotonic() - at <= 1, "reference observation skew exceeded one second"
            actual_cpu, actual_process = point["host"]["cpu"]["value"], point["processes"][0]["cpu"]["value"]
            counts["host_cpu"] += int(actual_cpu is not None and abs(actual_cpu - cpu) <= 5)
            counts["process_cpu"] += int(actual_process is not None and abs(actual_process - process_cpu) <= 5)
            actual_rss = point["processes"][0]["rss"]["value"]
            assert process_cpu > 0, "reference must measure the busy workload, not a launcher"
            assert rss >= 64 * 1024**2, "reference must include the touched workload allocation"
            counts["rss"] += int(actual_rss is not None and abs(actual_rss - rss) <= max(rss * .05, 64 * 1024**2))
            for field, value in expected.items():
                actual = point["host"]["memory"][field]["value"]
                counts[field] += int(actual is not None and abs(actual - value) <= max(value * .05, 64 * 1024**2))
            pairs.append(dict(host_cpu=[actual_cpu, cpu], process_cpu=[actual_process, process_cpu], rss=[actual_rss, rss]))
        print(json.dumps(dict(platform=sys.platform, passing_pairs=counts, observations=30, pairs=pairs)))
        assert min(counts.values()) >= 29
    finally:
        native.close_resource_probes()
        child.kill()
        child.wait(timeout=5)


@pytest.mark.skipif(os.environ.get("KLLM_TEST_HARDWARE") != "1" or sys.platform != "win32", reason="opt-in Windows WDDM accuracy acceptance")
def test_hardware_windows_gpu_reference():
    def powershell(script):
        value = json.loads(subprocess.check_output(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], text=True, timeout=8))
        return value if isinstance(value, list) else [value]
    capacity_script = r"""
Get-ChildItem 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}' -ErrorAction SilentlyContinue | ForEach-Object {
  $p=Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
  if ($p.DriverDesc -and $p.'HardwareInformation.qwMemorySize') {
    [pscustomobject]@{Name=$p.DriverDesc;Total=[UInt64]$p.'HardwareInformation.qwMemorySize'}
  }
} | ConvertTo-Json -Compress
exit 0
"""
    native.close_resource_probes()
    try:
        capacities = powershell(capacity_script)
        native.probe_resources(node_id="gpu-reference")
        time.sleep(2)
        warm = native.probe_resources(node_id="gpu-reference")
        eligible = [(device, view) for device in warm["devices"] for view in device["memory"]
                    if view["kind"] == "dedicated" and view["used"]["status"] == "measured" and view["free"]["status"] == "measured"]
        assert eligible, "SC-002 requires measured Windows dedicated used/free"
        device, view = eligible[0]
        matches = {int(row["Total"]) for row in capacities if row["Name"] == device["name"]}
        assert len(matches) == 1, "capacity reference is missing or ambiguous"
        capacity = matches.pop()
        instance = device["device_key"].removeprefix("win:")
        pairs = []
        passed = 0
        for _ in range(30):
            current = native.probe_resources(node_id="gpu-reference")
            measured = next(v for d in current["devices"] if d["device_key"] == device["device_key"] for v in d["memory"] if v["kind"] == "dedicated")
            counters = powershell(r"(Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage' -MaxSamples 1).CounterSamples | Select-Object InstanceName,CookedValue | ConvertTo-Json -Compress")
            reference = next(int(row["CookedValue"]) for row in counters if row["InstanceName"].lower() == instance)
            free = capacity - reference
            actual_used, actual_free = measured["used"]["value"], measured["free"]["value"]
            ok = actual_used is not None and actual_free is not None and free >= 0 and abs(actual_used - reference) <= max(reference * .05, 64 * 1024**2) and abs(actual_free - free) <= max(free * .05, 64 * 1024**2)
            passed += int(ok)
            pairs.append(dict(used=[actual_used, reference], free=[actual_free, free]))
            time.sleep(.2)
        print(json.dumps(dict(gpu=device["name"], reference_capacity=capacity, passing_pairs=passed, observations=30, pairs=pairs)))
        assert passed >= 29
    finally:
        native.close_resource_probes()


@pytest.mark.skipif(os.environ.get("KLLM_TEST_HARDWARE") != "1" or sys.platform not in ("win32", "darwin"), reason="opt-in real private-agent/native integration")
def test_hardware_private_agents_publish_host_only_resources(tmp_path):
    from kllm import usage_probe

    def port():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]
    master_port, worker_port = port(), port()
    assert master_port != worker_port
    key = "temporary-resource-integration-key"
    processes, logs = [], []
    try:
        for role, node, control_port in [("master", "native-master", master_port), ("worker", "native-worker", worker_port)]:
            script = (
                "from kllm.state import ClusterState; import uvicorn; "
                f"s=ClusterState(node_id={node!r},role={role!r},control_port={control_port},"
                f"master_host='127.0.0.1',master_port={master_port},join_key={key!r},rpc_bind='127.0.0.1'); "
                f"s.members={{{node!r}:dict(node_id={node!r},host='127.0.0.1',role={role!r})}}; "
                "s.save(); "
                f"uvicorn.run('kllm.agent:app',host='127.0.0.1',port={control_port},log_level='warning')"
            )
            output = (tmp_path / f"{role}.log").open("wb")
            logs.append(output)
            process = subprocess.Popen([PYTHON, "-c", script], stdout=output, stderr=subprocess.STDOUT,
                env=dict(usage_probe.helper_environment(), KLLM_HOME=str(tmp_path / role)))
            processes.append(process)
            url = f"http://127.0.0.1:{control_port}"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                assert process.poll() is None, (tmp_path / f"{role}.log").read_text(errors="replace")
                try:
                    response = httpx.get(url + "/v1/usage/live", timeout=1)
                    if response.status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(.1)
            else:
                pytest.fail(f"{role} did not start: " + (tmp_path / f"{role}.log").read_text(errors="replace"))
            if role == "master":
                registration = httpx.post(url + "/v1/register", headers={"X-Kllm-Key": key},
                    json=dict(node_id="native-worker", host="127.0.0.1", control_port=worker_port), timeout=5)
                assert registration.status_code == 200, registration.text
        master_url = f"http://127.0.0.1:{master_port}"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            live = httpx.get(master_url + "/v1/usage/live", timeout=2).raise_for_status().json()
            worker = live["nodes"].get("native-worker") or {}
            resources = worker.get("resources")
            if resources and resources["host"]["cpu"]["status"] == "measured":
                break
            time.sleep(.25)
        else:
            pytest.fail("worker native resources were not delivered: " + (tmp_path / "worker.log").read_text(errors="replace"))
        assert resources["host"]["memory"]["total"]["value"] > 0
        assert resources["collector"]["kind"] == "native"
        assert resources["processes"] == [] and worker["reading"] is None
        assert worker["process_state"] == "no managed process"
        assert worker["resource_freshness"] == "fresh"
        history = httpx.get(master_url + "/v1/usage/lifetimes", params=dict(node_id="native-worker"), timeout=2).raise_for_status().json()
        assert history["lifetimes"] == []
        print(json.dumps(dict(registered_nodes=list(live["nodes"]), worker_snapshot=resources["snapshot_id"],
                              host_cpu=resources["host"]["cpu"]["value"], process_history_rows=len(history["lifetimes"]))))
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
        for output in logs:
            output.close()
