import pytest
from pydantic import ValidationError

from kllm.run_settings import RunBody


def test_legacy_settings_default_to_rpc():
    body = RunBody(model="toy.gguf")
    assert body.transport == "rpc"
    assert body.snapshot_for_remember()["transport"] == "rpc"


def test_mpi_settings_round_trip_without_remembering_fingerprint():
    body = RunBody(
        model="toy.gguf", transport="mpi", device="gpu",
        mpi_device_ids={"worker": ["gpu-a", "gpu-b"]},
        plan_fingerprint="one-use-fingerprint",
    )
    snapshot = body.snapshot_for_remember()
    assert "plan_fingerprint" not in snapshot
    restored = RunBody(model="toy.gguf", **snapshot)
    assert restored.transport == "mpi"
    assert restored.mpi_device_ids == {"worker": ["gpu-a", "gpu-b"]}


@pytest.mark.parametrize("fields", [
    {"transport": "mpi", "device": "cpu"},
    {"transport": "tcp"},
    {"transport": "mpi", "mpi_device_ids": {"worker": []}},
    {"transport": "mpi", "mpi_device_ids": {"worker": ["a", "a"]}},
    {"transport": "rpc", "mpi_device_ids": {"worker": ["a"]}},
])
def test_invalid_transport_or_selection_is_rejected(fields):
    with pytest.raises(ValidationError):
        RunBody(model="toy.gguf", **fields)


def test_cli_mpi_run_plans_first_and_submits_fingerprint(monkeypatch):
    from typer.testing import CliRunner
    import kllm.cli as cli

    calls = []

    class Response:
        status_code = 200
        text = ""

        def __init__(self, value):
            self.value = value

        def json(self):
            return self.value

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, path, json):
            calls.append((path, dict(json)))
            if path == "/v1/plan":
                return Response({"transport": "mpi", "launchable": True,
                                 "plan_fingerprint": "sha256:fresh"})
            return Response({"plan": {"transport": "mpi"}})

    monkeypatch.setattr(cli, "_client", lambda port: Client())
    result = CliRunner().invoke(cli.app, ["run", "--model", "toy.gguf", "--transport", "mpi",
                                          "--mpi-device", "node=gpu-1"])
    assert result.exit_code == 0, result.output
    assert [path for path, _ in calls] == ["/v1/plan", "/v1/run"]
    assert calls[1][1]["plan_fingerprint"] == "sha256:fresh"
    assert calls[1][1]["mpi_device_ids"] == {"node": ["gpu-1"]}


def test_cli_mpi_blocker_is_submitted_with_the_fresh_fingerprint(monkeypatch):
    from typer.testing import CliRunner
    import kllm.cli as cli

    calls = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, path, json):
            calls.append((path, dict(json)))
            if path == "/v1/plan":
                return type("Response", (), {
                    "status_code": 200,
                    "text": "",
                    "json": lambda self: {
                        "launchable": False,
                        "blockers": ["engine unavailable"],
                        "plan_fingerprint": "sha256:blocked",
                    },
                })()
            return type("Response", (), {
                "status_code": 503,
                "text": "engine unavailable",
            })()

    monkeypatch.setattr(cli, "_client", lambda port: Client())
    result = CliRunner().invoke(cli.app, ["run", "--model", "toy.gguf", "--transport", "mpi"])
    assert result.exit_code == 1
    assert [path for path, _ in calls] == ["/v1/plan", "/v1/run"]
    assert calls[1][1]["plan_fingerprint"] == "sha256:blocked"
    assert "engine unavailable" in result.output
