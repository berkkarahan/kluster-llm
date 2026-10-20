"""Tests for Prima rank launch schema and native supervisor interface (PRIMA-DELTA-MAINT-001)."""

import pytest
from kllm import _native
from kllm.modules.primacpp.interactors.placement import (
    topology_from_plan,
    rank_launch,
    rank_cli_args,
)


def _sample_topology():
    inventory = {
        "master": {"role": "master", "rpc_bind": "10.10.10.2"},
        "worker": {"role": "worker", "rpc_bind": "10.10.10.3", "devices": [{"device": "CUDA0"}]},
        "unused": {"role": "worker", "rpc_bind": "10.10.10.4"},
    }
    plan = {
        "estimated_layers": 8,
        "split_mode": "layer",
        "fit_status": "fits",
        "cpu_layers": 0,
        "device_allocations": [
            {
                "node_id": "worker",
                "layer_start": 0,
                "layer_end": 5,
                "layers": 5,
                "device": "RPC[10.10.10.3:50052]/0",
                "rpc_index": 0,
                "local": False,
                "memory_kind": "gpu",
            },
            {
                "node_id": "master",
                "layer_start": 5,
                "layer_end": 8,
                "layers": 3,
                "device": "Metal0",
                "local": True,
                "memory_kind": "gpu",
            },
        ],
    }
    return topology_from_plan(plan, inventory, "a" * 64)


def test_rank_launch_conforms_to_native_supervisor_contract(tmp_path):
    """Generates launch spec via rank_launch(); asserts structured schema and native parser acceptance."""
    topo = _sample_topology()
    launch = rank_launch(topo, "worker")

    # Structured schema validation
    assert launch["ngl"] == 5
    assert launch["world"] == 2
    assert launch["rank"] == 1
    assert launch["master"] == "10.10.10.2"
    assert launch["next"] == "10.10.10.2"
    assert launch["data_port"] == 9000
    assert launch["signal_port"] == 10000
    assert launch["layer_window"] == "3,5"
    assert launch["devices"] == ["CUDA0"]
    assert launch["env"] == {"KLLM_PRIMA_BIND": "10.10.10.3"}
    assert launch["extra_args"] == []

    # Verify native supervisor parser acceptance without ValueError
    bin_path = tmp_path / "llama-cli.exe"
    bin_path.write_bytes(b"dummy_binary")
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"dummy_model")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    spec = dict(launch)
    spec["binary_path"] = str(bin_path)
    spec["model"] = str(model_path)
    spec["manifest_path"] = str(manifest_path)

    # Calling start_prima_rank validates the dictionary in PyO3 rust code before process spawn.
    # It must not raise ValueError (schema or reserved flag validation errors).
    try:
        _native.start_prima_rank(spec)
    except RuntimeError:
        # Expected on dummy binary during OS process spawn; schema validation succeeded!
        pass
    except ValueError as e:
        pytest.fail(f"Native launch spec schema rejected by supervisor: {e}")


def test_extra_args_reserved_flags_rejected(tmp_path):
    """Asserts that passing reserved flags in extra_args continues to be rejected by the security guard in process.rs."""
    bin_path = tmp_path / "llama-cli.exe"
    bin_path.write_bytes(b"dummy_binary")
    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"dummy_model")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")

    reserved_flags = [
        "--rank",
        "--world",
        "--master",
        "--next",
        "--data-port",
        "--signal-port",
        "-ngl",
        "-ts",
        "-lw",
        "--model",
    ]

    for flag in reserved_flags:
        spec = {
            "binary_path": str(bin_path),
            "rank": 1,
            "model": str(model_path),
            "manifest_path": str(manifest_path),
            "extra_args": [f"{flag}=1"],
        }
        with pytest.raises(ValueError, match="extra_args cannot override reserved flag"):
            _native.start_prima_rank(spec)


def test_rank_cli_args_formatting():
    """Validates that rank_cli_args accurately converts structured spec fields into CLI arguments."""
    topo = _sample_topology()
    launch = rank_launch(topo, "worker")
    args = rank_cli_args(launch)

    assert args[args.index("-ngl") + 1] == "5"
    assert args[args.index("--world") + 1] == "2"
    assert args[args.index("--rank") + 1] == "1"
    assert args[args.index("--master") + 1] == "10.10.10.2"
    assert args[args.index("--next") + 1] == "10.10.10.2"
    assert args[args.index("--data-port") + 1] == "9000"
    assert args[args.index("--signal-port") + 1] == "10000"
    assert args[args.index("-lw") + 1] == "3,5"
    assert args[args.index("-ts") + 1] == "1"
