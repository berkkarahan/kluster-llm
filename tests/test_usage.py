"""Observable usage sampling and history behavior, without a real child process."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sqlite3
import subprocess

import pytest

from kllm import usage
from kllm.state import ClusterState


def reading(at, *, node="worker", pid=12, started=None, cpu=12.5, rss=2 * 1024**3):
    started = started or at - timedelta(minutes=1)
    begin = usage.utc(started)
    return {
        "lifetime_id": f"{node}:{pid}:{begin}", "pid": pid,
        "process_started_at": begin, "process_role": "worker",
        "observed_at": usage.utc(at),
        "process": {"cpu_percent_machine": cpu, "rss_bytes": rss},
        "host": {"cpu_percent": 10, "used_bytes": 4 * 1024**3, "total_bytes": 8 * 1024**3},
        "devices": [], "availability": {"process_cpu": "measured", "process_rss": "measured"},
    }


def test_sampler_process_identity_scope_and_first_cpu(monkeypatch):
    now = datetime.now(timezone.utc)
    started_at = [now.timestamp() - 60]

    class FakeProcess:
        def __init__(self, pid):
            self.primed = False
        def create_time(self):
            return started_at[0]
        def cpu_percent(self, interval=None):
            if not self.primed:
                self.primed = True
                return 0
            return 100
        def memory_info(self):
            return SimpleNamespace(rss=2 * 1024**3)

    monkeypatch.setattr(usage.psutil, "Process", FakeProcess)
    monkeypatch.setattr(usage.psutil, "cpu_count", lambda logical: 8)
    monkeypatch.setattr(usage.psutil, "cpu_percent", lambda interval=None: 20)
    monkeypatch.setattr(usage.psutil, "virtual_memory", lambda: SimpleNamespace(used=4 * 1024**3, total=8 * 1024**3))
    state = ClusterState(node_id="worker", role="worker", process_pid=12, process_role="worker")
    sampler = usage.Sampler()
    first = sampler.sample(state, now=now)
    assert first["process"]["cpu_percent_machine"] is None
    assert first["availability"]["process_cpu"] == "initializing"
    later = sampler.sample(state, now=now + timedelta(seconds=2))
    assert later["process"]["cpu_percent_machine"] == 12.5
    assert later["process"]["rss_bytes"] == 2 * 1024**3
    assert later["devices"] == []
    assert not hasattr(sampler._process, "cmdline")
    started_at[0] = now.timestamp() + 1
    reused = sampler.sample(state, now=now + timedelta(seconds=4))
    assert reused["lifetime_id"] != later["lifetime_id"]
    assert reused["process"]["cpu_percent_machine"] is None
    started_at[0] = now.timestamp() - 60
    assert usage.Sampler().sample(state, now=now + timedelta(seconds=6))["lifetime_id"] == later["lifetime_id"]


def test_optional_device_probe_is_bounded_and_partial_values_have_reasons(monkeypatch):
    state = ClusterState(node_id="worker", role="worker", members={"worker": {
        "backend": "cuda", "devices": [{"index": 0, "vram_bytes": 8 * 1024**3},
                                      {"index": 1, "vram_bytes": 4 * 1024**3}]}})
    monkeypatch.setattr(usage.shutil, "which", lambda _: "nvidia-smi")
    calls = []

    def probe(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout="0, 45, 2048, 8192, 65, 135\n1, N/A, 1024, 4096, N/A, N/A\n")

    monkeypatch.setattr(usage.subprocess, "run", probe)
    devices = usage._devices(state)
    assert calls[0][1]["timeout"] <= 1
    assert [d["device_id"] for d in devices] == ["0", "1"]
    assert all(d["scope"] == "device" for d in devices)
    assert devices[0]["utilization_percent"] == 45
    assert devices[0]["used_bytes"] == 2048 * 1024**2
    assert devices[0]["temperature_c"] == 65 and devices[0]["power_w"] == 135
    assert devices[1]["utilization_percent"] is None and devices[1]["used_bytes"] == 1024 * 1024**2
    assert devices[1]["unavailable_reason"]
    monkeypatch.setattr(usage.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        subprocess.TimeoutExpired("nvidia-smi", 0.8)))
    failed = usage._devices(state)
    assert all(d["utilization_percent"] is None and d["unavailable_reason"] for d in failed)
    state.members["worker"]["backend"] = "cpu"
    assert all(d["utilization_percent"] is None for d in usage._devices(state))


def test_null_host_and_device_values_require_truthful_availability_and_reasons():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    point = reading(now)
    point["host"]["cpu_percent"] = None
    point["availability"]["host_cpu"] = "measured"
    with pytest.raises(ValueError, match="host_cpu"):
        usage.validate("worker", point, now=now)
    point["availability"]["host_cpu"] = "error"
    usage.validate("worker", point, now=now)
    point["host"]["used_bytes"] = None
    point["availability"]["host_memory"] = "measured"
    with pytest.raises(ValueError, match="host_memory"):
        usage.validate("worker", point, now=now)
    point["availability"]["host_memory"] = "unsupported"
    point["devices"] = [{"device_id": "0", "scope": "device", "utilization_percent": None,
                         "used_bytes": None, "total_bytes": 8 * 1024**3, "temperature_c": None,
                         "power_w": None, "unavailable_reason": None}]
    with pytest.raises(ValueError, match="unavailable"):
        usage.validate("worker", point, now=now)
    point["devices"][0]["unavailable_reason"] = "GPU sensor unsupported"
    usage.validate("worker", point, now=now)


def test_history_durable_idempotent_late_and_restart(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    path = tmp_path / "usage.sqlite3"
    with usage.UsageStore(path) as store:
        newest = reading(now)
        assert store.record("worker", "host", newest, now=now) is True
        assert store.record("worker", "host", newest, now=now) is False
        late = reading(now - timedelta(seconds=2), started=now - timedelta(minutes=1))
        store.record("worker", "host", late, now=now)
        assert store.latest("worker")["observed_at"] == newest["observed_at"]
        assert store.last_successful_save is not None
    with usage.UsageStore(path) as store:
        assert store.latest("worker")["observed_at"] == newest["observed_at"]
        assert store.count() == 2
        assert store.record("worker", "host", reading(now + timedelta(seconds=2), pid=13), now=now + timedelta(seconds=2))
        assert store.count() == 3
        with pytest.raises(ValueError):
            store.record("worker", "host", reading(now, cpu=-1), now=now)
        with pytest.raises(ValueError):
            store.record("other", "host", newest, now=now)
        assert store.count() == 3


def test_model_changes_are_bounded_associations_without_worker_restart(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with usage.UsageStore(tmp_path / "usage.sqlite3") as store:
        first = reading(now - timedelta(seconds=4), started=now - timedelta(minutes=1))
        second = reading(now - timedelta(seconds=2), started=now - timedelta(minutes=1))
        store.record("worker", "host", first, now=now, model="first.gguf")
        store.record("worker", "host", second, now=now, model="second.gguf")
        lifetimes = store.lifetimes("worker")
        assert len(lifetimes) == 1
        assert [a["model"] for a in lifetimes[0]["associations"]] == ["first.gguf", "second.gguf"]
        assert lifetimes[0]["associations"][0]["observed_until"] == second["observed_at"]
        store.end_node("worker")
        ended = store.lifetimes("worker")[0]
        assert ended["state"] == "ended"
        assert ended["associations"][-1]["observed_until"] == second["observed_at"]


def test_late_reading_does_not_reopen_stopped_or_reused_process(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    old_start = now - timedelta(minutes=1)
    with usage.UsageStore(tmp_path / "usage.sqlite3") as store:
        old = reading(now - timedelta(seconds=12), started=old_start)
        new = reading(now - timedelta(seconds=4), started=now - timedelta(seconds=6))
        store.record("worker", "host", old, now=now)
        store.record("worker", "host", new, now=now)
        assert store.record("worker", "host", reading(now - timedelta(seconds=10), started=old_start), now=now)
        assert store.latest("worker")["lifetime_id"] == new["lifetime_id"]
        assert next(life for life in store.lifetimes("worker") if life["lifetime_id"] == old["lifetime_id"])["state"] == "ended"
        store.end_node("worker")
        assert store.record("worker", "host", reading(now - timedelta(seconds=5), started=now - timedelta(seconds=6)), now=now)
        assert all(life["state"] == "ended" for life in store.lifetimes("worker"))


def test_corrupt_history_reports_read_failure_but_other_node_stays_readable(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with usage.UsageStore(tmp_path / "usage.sqlite3") as store:
        store.record("worker", "host", reading(now), now=now)
        store.record("other", "host", reading(now, node="other"), now=now)
        with store.db:
            store.db.execute("UPDATE readings SET payload='{' WHERE node_id='worker'")
        with pytest.raises(sqlite3.DatabaseError, match="damaged reading"):
            store.latest("worker")
        assert store.latest("other")["process"]["rss_bytes"] == 2 * 1024**3


def test_five_minutes_without_browser_survive_recorder_restart(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    start = now - timedelta(minutes=5)
    path = tmp_path / "usage.sqlite3"
    with usage.UsageStore(path) as store:
        for i in range(150):
            at = start + timedelta(seconds=i * 2)
            store.record("worker", "host", reading(at, started=start - timedelta(minutes=1), cpu=i % 100), now=at)
    with usage.UsageStore(path) as store:
        life = store.lifetimes("worker")[0]["lifetime_id"]
        assert len(store.readings("worker", life, "15m", now=now, limit=500)["readings"]) == 150
        series = store.series("worker", life, "15m", now=now)
        assert sum(bucket["count"] for bucket in series["buckets"]) == 150
        assert any(bucket["count"] == 0 for bucket in series["buckets"])
        assert series["last_successful_save"] == usage.utc(now - timedelta(seconds=2))


def test_exited_tracked_child_closes_lifetime_even_if_pid_is_still_set(tmp_path, monkeypatch):
    import kllm.agent as agent

    monkeypatch.setenv("KLLM_HOME", str(tmp_path))
    state = ClusterState(node_id="master", role="master", process_pid=99, process_role="master")
    state.members["master"] = {"node_id": "master", "role": "master", "host": "local", "process_pid": 99}
    monkeypatch.setattr(agent, "STATE", state)
    store = agent._usage_store()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    old = reading(now - timedelta(seconds=2), node="master", pid=99, started=now - timedelta(minutes=1))
    old["process_role"] = "master"
    store.record("master", "local", old, now=now)
    monkeypatch.setattr(usage.Sampler, "sample", lambda *_: (_ for _ in ()).throw(usage.psutil.NoSuchProcess(99)))
    ticks = iter((False, True))
    monkeypatch.setattr(agent._background_stop, "wait", lambda _: next(ticks))
    agent._usage_loop()
    assert store.lifetimes("master")[0]["state"] == "ended"
    assert store.count() == 1
    assert state.process_pid is None
    assert store.recording == "interrupted"


def test_oldest_first_retention_and_failure_keeps_good_points(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with usage.UsageStore(tmp_path / "usage.sqlite3") as store:
        for offset in (25, 23, 0):
            at = now - timedelta(hours=offset)
            store.record("worker", "host", reading(at, started=at - timedelta(minutes=1)), now=at)
        assert store.count() == 2
        before = store.latest("worker")["observed_at"]
        write = store._write
        monkeypatch.setattr(store, "_write", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
        with pytest.raises(OSError):
            store.record("worker", "host", reading(now + timedelta(seconds=2)), now=now + timedelta(seconds=2))
        assert store.recording == "failed"
        assert store.latest("worker")["observed_at"] == before
        monkeypatch.setattr(store, "_write", write)
        assert not store.record("worker", "host", reading(now, started=now - timedelta(minutes=1)), now=now)
        assert store.recording == "failed"  # An old duplicate is not a successful new save.


def test_physical_budget_prunes_oldest_without_losing_latest(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with usage.UsageStore(tmp_path / "usage.sqlite3", max_bytes=96 * 1024) as store:
        for i in range(50):
            point = reading(now + timedelta(seconds=2 * i), started=now - timedelta(minutes=1))
            point["devices"] = [{"device_id": str(j), "scope": "device", "unavailable_reason": "u" * 240} for j in range(10)]
            store.record("worker", "host", point, now=now + timedelta(seconds=2 * i))
        assert 0 < store.count() < 50
        assert store.latest("worker")["observed_at"] == point["observed_at"]
        assert store._physical_bytes() <= store.max_bytes
        assert store._meta("storage_truncated") == "1"
        assert store.coverage()["storage_truncated"] is True
        with store.db:
            store.db.execute("UPDATE meta SET value=? WHERE key='storage_truncated_at'", (usage.utc(now - timedelta(hours=25)),))
        assert store.coverage()["storage_truncated"] is False


def test_too_small_physical_budget_never_acknowledges_a_discarded_new_reading(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with usage.UsageStore(tmp_path / "usage.sqlite3", max_bytes=64 * 1024) as store:
        old = reading(now - timedelta(seconds=2), started=now - timedelta(minutes=1))
        store.record("worker", "host", old, now=now)
        saved = store.last_successful_save
        point = reading(now)
        point["devices"] = [{"device_id": str(i), "scope": "device", "unavailable_reason": "sensor unsupported" * 12}
                            for i in range(32)]
        with pytest.raises(OSError, match="budget"):
            store.record("worker", "host", point, now=now)
        assert store.count() == 1
        assert store.latest("worker")["observed_at"] == old["observed_at"]
        assert store.recording == "failed"
        assert store.last_successful_save == saved
        assert store._physical_bytes() <= store.max_bytes


def test_filtered_lifetime_history_gaps_zero_and_seek_cursor(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with usage.UsageStore(tmp_path / "usage.sqlite3") as store:
        first = reading(now - timedelta(seconds=10), cpu=0, rss=0)
        second = reading(now - timedelta(seconds=6), started=now - timedelta(seconds=70), cpu=9)
        other = reading(now - timedelta(seconds=4), pid=13)
        for point in (first, second, other):
            store.record("worker", "host", point, now=now, model="model.gguf")
        ids = store.lifetimes("worker")
        assert {life["lifetime_id"] for life in ids} == {first["lifetime_id"], other["lifetime_id"]}
        assert all(life["model"] == "model.gguf" for life in ids)
        series = store.series("worker", first["lifetime_id"], "15m", now=now)
        assert len(series["buckets"]) <= 720
        assert any(b["count"] == 0 for b in series["buckets"])
        assert any(b["count"] and b["process_cpu_min"] == 0 for b in series["buckets"])
        page = store.readings("worker", first["lifetime_id"], "15m", limit=1, now=now)
        assert page["readings"][0]["observed_at"] == first["observed_at"]
        assert page["next_cursor"]
        next_page = store.readings("worker", first["lifetime_id"], "15m", limit=1, cursor=page["next_cursor"], now=now)
        assert next_page["readings"][0]["observed_at"] == second["observed_at"]
        with pytest.raises(ValueError):
            store.readings("worker", other["lifetime_id"], "15m", cursor=page["next_cursor"], now=now)
        with pytest.raises(ValueError):
            store.readings("worker", first["lifetime_id"], "bogus", now=now)

        import base64
        import json

        forged = json.loads(base64.urlsafe_b64decode(page["next_cursor"]))
        forged["start"] = usage.utc(now - timedelta(hours=1))
        invalid = base64.urlsafe_b64encode(json.dumps(forged).encode()).decode()
        with pytest.raises(ValueError, match="cursor"):
            store.readings("worker", first["lifetime_id"], "15m", limit=1, cursor=invalid, now=now)


def test_bucket_keeps_scoped_device_extrema_instead_of_last_point(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    started = now - timedelta(minutes=1)
    with usage.UsageStore(tmp_path / "usage.sqlite3") as store:
        for seconds, percent, used in ((8, 20, 512 * 1024**2), (7, 80, 1024**3)):
            point = reading(now - timedelta(seconds=seconds), started=started)
            point["devices"] = [{"device_id": "0", "scope": "device", "utilization_percent": percent,
                                 "used_bytes": used, "total_bytes": 8 * 1024**3,
                                 "temperature_c": 40 + percent / 10, "power_w": 100 + percent,
                                 "unavailable_reason": None}]
            store.record("worker", "host", point, now=now)
        series = store.series("worker", point["lifetime_id"], "1h", now=now)
        bucket = next(b for b in series["buckets"] if b["count"] == 2)
        assert bucket["devices"][0]["utilization_percent_min"] == 20
        assert bucket["devices"][0]["utilization_percent_max"] == 80
        assert bucket["devices"][0]["used_bytes_min"] == 512 * 1024**2
        assert bucket["devices"][0]["used_bytes_max"] == 1024**3
