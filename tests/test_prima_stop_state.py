"""Retired coordinator identity still owns clearing its matching serving state."""

from types import SimpleNamespace

import pytest

from kllm.modules.primacpp.interactors.distributed_runs import DistributedPrimaRuns
from kllm.modules.primacpp.interactors.runs import LocalPrimaRuns


@pytest.mark.parametrize("matching", [True, False])
def test_stop_after_monitor_retirement_clears_only_the_owned_configuration(matching):
    saved = []
    config = {"engine": "primacpp", "run_id": "owned" if matching else "another-run"}
    state = SimpleNamespace(process_pid=None, process_role=None, active_config=config,
                            save=lambda: saved.append("saved"))
    runs = object.__new__(DistributedPrimaRuns)
    runs.control = SimpleNamespace(service=SimpleNamespace(state=lambda: state))
    runs.coordinator = SimpleNamespace(active=None, stop=lambda: None)
    runs.run_id = "owned"
    runs.preparation = runs.runtime = runs.context = object()
    runs.stop()
    assert state.active_config == (None if matching else config)
    assert saved == (["saved"] if matching else [])
    assert runs.run_id is None


@pytest.mark.parametrize("matching", [True, False])
def test_monitor_retirement_preserves_an_unrelated_active_run(matching):
    saved = []
    config = {"engine": "primacpp" if matching else "llamacpp",
              "run_id": "owned" if matching else "other"}
    state = SimpleNamespace(process_pid=37, process_role="master", active_config=config,
                            apply_error=None, save=lambda: saved.append(True))

    class RetiredCoordinator:
        active = object()

        def monitor_once(self):
            self.active = None
            return {"phase": "idle"}

        def stop(self):
            return {"phase": "idle"}

    runs = object.__new__(DistributedPrimaRuns)
    runs.control = SimpleNamespace(service=SimpleNamespace(state=lambda: state))
    runs.coordinator = RetiredCoordinator()
    runs.run_id = "owned"
    runs.preparation = runs.runtime = runs.context = object()
    runs.monitor_once()

    if matching:
        assert state.process_pid is state.process_role is state.active_config is None
        assert state.apply_error == "Prima ranks stopped; prepare a new run"
        assert len(saved) == 2
    else:
        assert state.process_pid == 37 and state.process_role == "master"
        assert state.active_config is config and state.apply_error is None
        assert saved == []


def test_distributed_monitor_does_not_report_a_replacement_published_during_stop():
    replacement = {"engine": "llamacpp", "run_id": "new"}
    state = SimpleNamespace(process_pid=37, process_role="master",
                            active_config={"engine": "primacpp", "run_id": "owned"},
                            apply_error=None, save=lambda: None)

    class RetiredCoordinator:
        active = object()

        def monitor_once(self):
            self.active = None
            return {"phase": "idle"}

        def stop(self):
            state.active_config = replacement
            state.process_pid = 59
            return {"phase": "idle"}

    runs = object.__new__(DistributedPrimaRuns)
    runs.control = SimpleNamespace(service=SimpleNamespace(state=lambda: state))
    runs.coordinator = RetiredCoordinator()
    runs.run_id = "owned"
    runs.preparation = runs.runtime = runs.context = object()

    runs.monitor_once()

    assert state.active_config is replacement and state.process_pid == 59
    assert state.apply_error is None


@pytest.mark.parametrize("replace_during_serve", [False, True])
def test_serving_monitor_drains_only_stale_prima_without_overwriting_replacement(replace_during_serve):
    replacement = {"engine": "llamacpp", "run_id": "new"}
    state = SimpleNamespace(process_pid=59, process_role="master",
                            active_config={"engine": "primacpp", "run_id": "retired"}
                            if replace_during_serve else replacement,
                            apply_error=None, save=lambda: None)
    stopped, serving = [], []

    class Coordinator:
        active = SimpleNamespace(run_id="retired")

        def monitor_once(self):
            return {"phase": "serving"}

        def stop(self):
            stopped.append(True)
            self.active = None
            return {"phase": "idle"}

    runs = object.__new__(DistributedPrimaRuns)
    runs.control = SimpleNamespace(service=SimpleNamespace(state=lambda: state))
    runs.coordinator = Coordinator()
    runs.run_id = "retired"
    runs.preparation = runs.runtime = runs.context = object()
    def publish(_):
        serving.append(True)
        state.active_config = replacement
    runs._serving = publish

    runs.monitor_once()

    assert stopped == [True]
    assert serving == ([True] if replace_during_serve else [])
    assert state.active_config is replacement and state.process_pid == 59
    assert state.apply_error is None


def test_local_monitor_retirement_preserves_an_unrelated_active_run():
    saved, registry = [], []
    config = {"engine": "llamacpp", "run_id": "new"}
    state = SimpleNamespace(process_pid=53, process_role="master", active_config=config,
                            apply_error=None, save=lambda: saved.append(True))
    rank = SimpleNamespace(owner={"run_id": "retired"})
    def renew(*_):
        raise ValueError("Rank exited")
    def stop(*_):
        rank.owner = None
    rank.renew, rank.stop = renew, stop
    control = SimpleNamespace(rank=rank, registry=SimpleNamespace(clear_running=lambda **kw: registry.append(kw)),
                              service=SimpleNamespace(state=lambda: state))
    runs = LocalPrimaRuns(control)
    runs.manifest = SimpleNamespace(run_id="retired")

    runs.monitor_once()

    assert runs.manifest is None and rank.owner is None
    assert registry == [{"engine": "primacpp", "run_id": "retired"}]
    assert state.process_pid == 53 and state.process_role == "master"
    assert state.active_config is config and state.apply_error is None
    assert saved == []


def test_local_monitor_does_not_clear_a_replacement_published_during_stop():
    replacement = {"engine": "llamacpp", "run_id": "new"}
    state = SimpleNamespace(process_pid=53, process_role="master",
                            active_config={"engine": "primacpp", "run_id": "retired"},
                            apply_error=None, save=lambda: None)
    rank = SimpleNamespace(owner={"run_id": "retired"})
    def renew(*_):
        raise ValueError("Rank exited")
    def stop(*_):
        rank.owner = None
        state.active_config = replacement
        state.process_pid = 59
    rank.renew, rank.stop = renew, stop
    control = SimpleNamespace(rank=rank, registry=SimpleNamespace(clear_running=lambda **_: None),
                              service=SimpleNamespace(state=lambda: state))
    runs = LocalPrimaRuns(control)
    runs.manifest = SimpleNamespace(run_id="retired")

    runs.monitor_once()

    assert state.active_config is replacement and state.process_pid == 59
    assert state.apply_error is None
