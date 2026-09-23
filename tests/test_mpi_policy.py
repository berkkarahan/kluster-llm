from copy import deepcopy

import pytest

from kllm.mpi_policy import MpiAdmissionError, select_mpi_ranks


def member(node_id, role, platform, vendor, backends):
    physical_id = f"gpu-{node_id}"
    vendor_id = {"nvidia": "0x10de", "amd": "0x1002", "apple": "0x106b",
                 "intel": "0x8086"}.get(vendor)
    return {
        "node_id": node_id, "role": role, "backend": "rpc-preference-only",
        "mpi_capabilities": {
            "protocol": 1, "available": True, "platform": platform,
            "fingerprint": f"inventory-{node_id}",
            "devices": [{"physical_id": physical_id, "vendor": vendor,
                         "vendor_id": vendor_id}],
            "provider": {
                "upstream_revision": "99be8ac", "local_patch_digest": "sha256:fork",
                "protocol_version": 1, "target_triple": platform,
                "features": [], "binary_sha256": f"sha256:binary-{node_id}",
                "self_test": "passed",
            },
            "backends": {
                backend: {
                    "available": True, "backend": backend,
                    "operation_profile": "dense_llama_text_f16kv_flash_tp_v1",
                    "supported_architectures": ["llama"],
                    "engine_revision": "b11026-test-patch",
                    "mpi_compatibility_id": "test-provider",
                    "fingerprint": f"probe-{node_id}-{backend}",
                    "devices": [{"physical_id": physical_id, "device": f"{backend}0",
                                 "backend": backend}],
                } for backend in backends
            },
        },
    }


def mixed_cluster():
    return [member("win", "master", "win32", "amd", ["vulkan"]),
            member("mac", "worker", "darwin", "apple", ["metal", "vulkan"]),
            member("linux", "worker", "linux", "nvidia", ["cuda", "vulkan"])]


@pytest.mark.parametrize("hardware,expected", [
    ([("darwin", "apple"), ("darwin", "amd")], "metal"),
    ([("linux", "nvidia"), ("win32", "nvidia")], "cuda"),
    ([("linux", "amd"), ("linux", "amd")], "rocm"),
    ([("linux", "nvidia"), ("linux", "amd")], "vulkan"),
    ([("linux", "unknown"), ("linux", "nvidia")], "vulkan"),
])
def test_cluster_backend_heuristic(hardware, expected):
    nodes = [member(str(i), "master" if i == 0 else "worker", platform, vendor,
                    ["metal", "cuda", "rocm", "vulkan"])
             for i, (platform, vendor) in enumerate(hardware)]
    decision = select_mpi_ranks(nodes)
    assert decision["preferred_backend"] == expected
    assert decision["effective_backend"] == expected
    assert {rank["backend"] for rank in decision["ranks"]} == {expected}


def test_mixed_cluster_requires_vulkan_and_is_input_order_independent():
    nodes = mixed_cluster()
    original = deepcopy(nodes)
    result = select_mpi_ranks(list(reversed(nodes)))
    assert result["cluster_class"] == "heterogeneous_or_unknown"
    assert result["effective_backend"] == "vulkan"
    assert [rank["node_id"] for rank in result["ranks"]] == ["win", "linux", "mac"]
    assert nodes == original


def test_native_falls_back_to_common_vulkan_with_diagnostics():
    nodes = [member("a", "master", "linux", "amd", ["rocm", "vulkan"]),
             member("b", "worker", "win32", "amd", ["vulkan"])]
    result = select_mpi_ranks(nodes)
    assert result["preferred_backend"] == "rocm"
    assert result["effective_backend"] == "vulkan"
    assert "rocm unavailable" in result["backend_reason"]
    assert any("b/gpu-b" in message for message in result["backend_diagnostics"]["rocm"])


def test_missing_common_flavor_blocks_without_partial_ranks():
    nodes = mixed_cluster()
    del nodes[1]["mpi_capabilities"]["backends"]["vulkan"]
    result = select_mpi_ranks(nodes)
    assert result["effective_backend"] is None
    assert result["ranks"] == []
    assert any("mac/gpu-mac" in message for message in result["blockers"])


def test_selection_uses_physical_ids_and_selected_gpu_family():
    nodes = [member("a", "master", "linux", "nvidia", ["cuda", "vulkan"]),
             member("b", "worker", "linux", "nvidia", ["cuda", "vulkan"])]
    cap = nodes[0]["mpi_capabilities"]
    cap["devices"].append({"physical_id": "extra-amd", "vendor": "amd",
                           "vendor_id": "0x1002"})
    cap["backends"]["vulkan"]["devices"].append(
        {"physical_id": "extra-amd", "device": "vulkan1", "backend": "vulkan"})
    assert select_mpi_ranks(nodes)["effective_backend"] == "vulkan"
    result = select_mpi_ranks(nodes, {"a": ["gpu-a"]})
    assert result["effective_backend"] == "cuda"
    assert result["ranks"][0]["device"] == "cuda0"


@pytest.mark.parametrize("selection", [
    {"ghost": ["gpu-win"]}, {"win": []},
    {"win": ["Vulkan0"]}, {"win": ["gpu-win", "gpu-win"]},
])
def test_invalid_selection_fails_closed(selection):
    with pytest.raises(MpiAdmissionError):
        select_mpi_ranks(mixed_cluster(), selection)


def test_rpc_backend_is_not_mpi_capability_evidence():
    nodes = mixed_cluster()
    nodes[1].pop("mpi_capabilities")
    with pytest.raises(MpiAdmissionError, match="mac.*capability"):
        select_mpi_ranks(nodes)


@pytest.mark.parametrize("field", ["engine_revision", "mpi_compatibility_id"])
def test_incompatible_builds_block(field):
    nodes = mixed_cluster()
    nodes[1]["mpi_capabilities"]["backends"]["vulkan"][field] = "different"
    result = select_mpi_ranks(nodes)
    assert result["effective_backend"] is None
    assert any("incompatible" in message for message in result["blockers"])


def test_duplicate_physical_inventory_fails_closed():
    nodes = mixed_cluster()
    devices = nodes[0]["mpi_capabilities"]["devices"]
    devices.append(dict(devices[0]))
    with pytest.raises(MpiAdmissionError, match="physical inventory"):
        select_mpi_ranks(nodes)


def test_flavor_cannot_relabel_device_backend():
    nodes = mixed_cluster()
    nodes[1]["mpi_capabilities"]["backends"]["vulkan"]["devices"][0]["backend"] = "metal"
    result = select_mpi_ranks(nodes)
    assert result["effective_backend"] is None
    assert any("mac/gpu-mac" in message for message in result["blockers"])


def test_name_without_vendor_id_does_not_prove_nvidia_family():
    nodes = [member("a", "master", "linux", "nvidia", ["cuda", "vulkan"]),
             member("b", "worker", "linux", "nvidia", ["cuda", "vulkan"])]
    del nodes[1]["mpi_capabilities"]["devices"][0]["vendor_id"]
    decision = select_mpi_ranks(nodes)
    assert decision["preferred_backend"] == "vulkan"


def test_missing_required_operations_blocks_candidate():
    nodes = mixed_cluster()
    del nodes[1]["mpi_capabilities"]["backends"]["vulkan"]["operation_profile"]
    decision = select_mpi_ranks(nodes)
    assert decision["effective_backend"] is None
    assert any("dense Llama" in message for message in decision["blockers"])


def test_provider_hashes_can_differ_but_fork_must_match():
    nodes = mixed_cluster()
    assert select_mpi_ranks(nodes)["effective_backend"] == "vulkan"
    nodes[1]["mpi_capabilities"]["provider"]["local_patch_digest"] = "sha256:other"
    decision = select_mpi_ranks(nodes)
    assert decision["effective_backend"] is None
    assert any("incompatible" in message for message in decision["blockers"])
