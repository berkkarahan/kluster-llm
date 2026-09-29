"""Device-level contiguous placement matching the pinned llama.cpp layer splitter."""
from __future__ import annotations

import math
from typing import Any

from kllm.tensor_split import proportional_weights, select_routing_devices

MIB = 1024**2
WIDTHS = {"f32": 4, "f16": 2, "bf16": 2, "q8_0": 34 / 32,
          "q4_0": 18 / 32, "q4_1": 20 / 32, "q5_0": 22 / 32,
          "q5_1": 24 / 32, "iq4_nl": 18 / 32}


def memory_costs(layout, size, context, settings):
    approximate = not layout or layout.get("confidence") != "metadata"
    if not layout:
        count = 32 if size < 6000 * MIB else 48 if size < 12000 * MIB else 80
        layout = dict(architecture="unknown", metadata={}, layers=[size // (count + 2)] * count,
                      input_bytes=size // (count + 2), output_bytes=size // (count + 2))
    arch, meta = layout["architecture"], layout["metadata"]
    def val(key, default=0):
        return meta.get(f"{arch}.{key}", default)
    def at(value, index):
        return value[index] if isinstance(value, list) else value
    hybrid = arch in ("qwen35", "qwen35moe", "qwen3next")
    known = hybrid or arch in ("llama", "qwen2", "qwen2moe", "qwen3", "qwen3moe", "mistral", "gemma", "gemma2", "gemma3")
    approximate |= not known
    approximate |= any(f"{arch}.{key}" not in meta for key in ("embedding_length", "attention.head_count", "attention.head_count_kv"))
    layers = list(layout["layers"])
    embd = int(val("embedding_length", 4096))
    state = []
    kv = []
    for i, weight in enumerate(layers):
        heads = int(at(val("attention.head_count", 32), i)) or 1
        kvheads = int(at(val("attention.head_count_kv", heads), i))
        dk = int(at(val("attention.key_length", embd // heads), i))
        dv = int(at(val("attention.value_length", embd // heads), i))
        recurrent = hybrid and bool(at(val("attention.recurrent_layers", [(j + 1) % int(val("full_attention_interval", 4)) != 0 for j in range(len(layers))]), i))
        if recurrent:
            d = int(val("ssm.state_size", 128))
            rank = int(val("ssm.time_step_rank", 32))
            groups = int(val("ssm.group_count", 16))
            conv = int(val("ssm.conv_kernel", 4))
            # Float32 delta-net matrix plus convolution history per sequence.
            # Live device state is separate from server checkpoints stored on the host.
            inner = int(val("ssm.inner_size", d * rank))
            recurrent_bytes = 4 * (d * inner + (conv - 1) * (inner + 2 * groups * d)) * settings["parallel_slots"]
            state.append(recurrent_bytes)
            kv.append(0)
        else:
            # --ctx-size is total cache capacity across slots, not per slot.
            kv.append(math.ceil(context * kvheads * (dk * WIDTHS[settings["cache_type_k"]] + dv * WIDTHS[settings["cache_type_v"]])))
            state.append(0)
        layers[i] = weight + kv[-1] + state[-1]
    overhead = 256 * MIB + embd * settings["micro_batch"] * 32
    checkpoints = settings.get("checkpoints", "auto")
    checkpoint_count = 32 if checkpoints == "auto" else max(0, int(checkpoints))
    # Maximum checkpoint storage for recurrent state, allocated by the server on CPU.
    checkpoint_bytes = sum(state) * checkpoint_count
    return dict(costs=layers + [layout["output_bytes"]], input_bytes=layout["input_bytes"],
                kv_bytes=sum(kv), state_bytes=sum(state), checkpoint_bytes=checkpoint_bytes, overhead=overhead,
                confidence="approximate" if approximate else "metadata; execution overhead approximate",
                architecture=arch)


def _allocate_contiguous(devices, costs, *, cpu_budget, cpu_base, overhead, master_node_id):
    """Contiguous layer DP over ``devices``. Returns (cpu_count, cpu_bytes, allocations) or None."""
    count = len(costs)
    prefix = [0]
    for cost in costs:
        prefix.append(prefix[-1] + cost)
    for cpu_count in range(count + 1):
        cpu_bytes = cpu_base + prefix[cpu_count]
        if cpu_bytes > cpu_budget:
            continue
        states = {cpu_count: []}
        for d in devices:
            next_states = dict(states)
            for start, assignments in states.items():
                budget = d["budget_bytes"]
                if d["local"] and d["unified_memory"]:
                    budget = max(0, budget - prefix[cpu_count])
                if d["unified_memory"]:
                    budget = max(0, budget - sum(a["estimated_bytes"] for a in assignments if a["node_id"] == d["node_id"]))
                for end in range(start + 1, count + 1):
                    used = prefix[end] - prefix[start] + overhead
                    if used > budget:
                        break
                    if end not in next_states:
                        next_states[end] = assignments + [dict(d, layer_start=start, layer_end=end,
                                                              layers=end - start, output_layer=end == count,
                                                              estimated_bytes=used)]
            states = next_states
        if count in states:
            return cpu_count, cpu_bytes, states[count]
    return None


def _allocate_manual(nodes, devices, costs, shares, *, cpu_budget, cpu_base, overhead, master_node_id):
    """Round node byte targets to contiguous whole layers; never spill onto CPU."""
    if cpu_base > cpu_budget:
        return None, "master CPU base exceeds available RAM"
    prefix = [0]
    for cost in costs:
        prefix.append(prefix[-1] + cost)
    tolerance = max(costs) / 2 + 1
    targets = []
    target = 0.0
    for node in nodes:
        target += shares[node["node_id"]] * prefix[-1]
        targets.append(target)

    from functools import lru_cache

    @lru_cache(None)
    def assign(index, start):
        if index == len(nodes):
            return [] if start == len(costs) else None
        nid = nodes[index]["node_id"]
        eligible = [d for d in devices if d["node_id"] == nid]
        ends = ([len(costs)] if index == len(nodes) - 1 else
                [start] if shares[nid] == 0 else
                sorted(range(start, len(costs) + 1), key=lambda end: abs(prefix[end] - targets[index])))
        for end in ends:
            # ponytail: targets are rounded within half the largest indivisible
            # layer; a true byte-accurate split needs tensor (not layer) placement.
            if abs(prefix[end] - prefix[start] - shares[nid] * prefix[-1]) > tolerance:
                continue
            if end == start:
                here = []
            elif eligible:
                solved = _allocate_contiguous(eligible, costs[start:end], cpu_budget=0, cpu_base=0,
                                              overhead=overhead, master_node_id=master_node_id)
                if solved is None or solved[0] != 0:
                    continue
                here = [dict(a, layer_start=a["layer_start"] + start,
                             layer_end=a["layer_end"] + start,
                             output_layer=a["layer_end"] + start == len(costs)) for a in solved[2]]
            else:
                continue
            rest = assign(index + 1, end)
            if rest is not None:
                return here + rest
        return None

    allocations = assign(0, 0)
    if allocations is None:
        selected = ", ".join(n["node_id"] for n in nodes if shares[n["node_id"]] > 0)
        return None, f"requested node shares cannot fit GPU capacities with contiguous layers ({selected})"
    return (0, cpu_base, allocations), ""


def plan_devices(nodes, size, layout, settings, *, context, headroom, device, vram_weight, latency_weight,
                 node_shares=None):
    memory = memory_costs(layout, size, context, settings)
    costs = memory["costs"]
    count = len(costs)
    master = next((n for n in nodes if n.get("is_master")), None)
    if master is None:
        raise ValueError("master capacity is required for input tensors and CPU spill")
    def ram(n):
        # Only measured allocations of the process being replaced may be reclaimed.
        return min(int(n.get("total_ram_bytes", 0) * headroom),
                   int(n.get("available_ram_bytes", 0)) + int(n.get("reclaimable_ram_bytes", 0)))
    cpu_budget = ram(master)
    inputs = memory["input_bytes"] + settings.get("auxiliary_bytes", 0)
    cpu_base = inputs + memory["overhead"] + memory["checkpoint_bytes"]
    devices = []
    if device == "gpu":
        for n in nodes:
            inventory = n.get("devices") or []
            if not inventory and n.get("backend") not in ("cpu", "unknown"):
                raise ValueError(f"{n['node_id']}: device inventory missing; update/rejoin this node before planning")
            for i, d in enumerate(inventory):
                total = int(d.get("vram_bytes") or 0)
                free = int(d.get("free_bytes", total))
                budget = min(int(total * headroom), free + int(d.get("reclaimable_bytes", 0)))
                if n.get("unified_memory"):
                    budget = min(budget, ram(n))
                    if n.get("is_master"):
                        budget = max(0, budget - cpu_base)
                local = bool(n.get("is_master"))
                name = d.get("device")
                if not name:
                    raise ValueError(f"{n['node_id']}: missing llama.cpp device name")
                endpoint = n.get("rpc_endpoint")
                devices.append(dict(node_id=n["node_id"], device=name if local else f"RPC[{endpoint}]/{i}",
                                    backend=d.get("backend", n["backend"]), local=local,
                                    budget_bytes=budget, latency_ms=float(n.get("latency_ms") or 0),
                                    rpc_endpoint=None if local else endpoint, rpc_index=i,
                                    rpc_device_count=len(inventory), unified_memory=n.get("unified_memory", False)))
    max_cap = max((d["budget_bytes"] for d in devices), default=1) or 1
    max_lat = max((d["latency_ms"] for d in devices), default=0)
    for d in devices:
        d["score"] = vram_weight * d["budget_bytes"] / max_cap + latency_weight * (1 - d["latency_ms"] / max_lat if max_lat else 1)
    gpu_budgets = {n["node_id"]: (min(ram(n), sum(d["budget_bytes"] for d in devices if d["node_id"] == n["node_id"]))
                              if n.get("unified_memory") else sum(d["budget_bytes"] for d in devices if d["node_id"] == n["node_id"]))
                   for n in nodes}
    devices.sort(key=lambda d: -d["score"])

    model_bytes = sum(costs)
    overhead = memory["overhead"]
    locals_only = [d for d in devices if d.get("local")]
    locals_fit = (
        cpu_base <= cpu_budget
        and bool(locals_only)
        and _allocate_contiguous(
            locals_only, costs, cpu_budget=cpu_budget, cpu_base=cpu_base,
            overhead=overhead, master_node_id=master["node_id"],
        ) is not None
    )
    if node_shares is not None:
        if device != "gpu" or settings.get("split_mode", "layer") != "layer":
            raise ValueError("manual node shares require GPU layer placement")
        active = {n["node_id"] for n in nodes}
        if any(share > 0 for nid, share in node_shares.items() if nid not in active):
            raise ValueError("manual node shares must match current cluster members")
        node_shares = {nid: node_shares.get(nid, 0) for nid in active}
        routing_mode = "manual"
    else:
        routing_mode, devices = select_routing_devices(
            devices, model_bytes=model_bytes, overhead=overhead,
            cpu_base=cpu_base, cpu_budget=cpu_budget, locals_fit=locals_fit,
        )

    split_mode = settings.get("split_mode", "layer")
    # For each possible CPU prefix, find contiguous device segments by dynamic programming.
    # Includes the output layer, so ngl and tensor_split describe the same allocation.
    failure = ""
    if node_shares is not None:
        solution, failure = _allocate_manual(nodes, devices, costs, node_shares,
                                            cpu_budget=cpu_budget, cpu_base=cpu_base,
                                            overhead=overhead, master_node_id=master["node_id"])
    else:
        solution = _allocate_contiguous(devices, costs, cpu_budget=cpu_budget, cpu_base=cpu_base,
                                        overhead=overhead, master_node_id=master["node_id"])
    fits = solution is not None
    cpu_count, cpu_bytes, allocations = solution or (count, cpu_base + sum(costs), [])
    ngl = count - cpu_count
    # Slightly bias interior boundaries downward to avoid float rounding assigning
    # a boundary layer to the previous device in upstream upper_bound(float).
    split = [a["layers"] for a in allocations]
    if len(split) > 1:
        split[0] -= 0.0001
        split[-1] += 0.0001
    endpoints = list(dict.fromkeys(a["rpc_endpoint"] for a in allocations if a["rpc_endpoint"]))
    rpc_offset = 0
    for endpoint in endpoints:
        selected = [a for a in allocations if a["rpc_endpoint"] == endpoint]
        for a in selected:
            a["device"] = f"RPC{rpc_offset + a['rpc_index']}"
        rpc_offset += selected[0]["rpc_device_count"]
    if split_mode in ("row", "tensor"):
        # These modes use every selected device for each offloaded layer. The
        # layer DP remains the capacity estimate, while llama.cpp receives
        # proportional shares for the selected devices.
        split = proportional_weights(devices)
        device_order = [d["device"] for d in devices]
        endpoints = list(dict.fromkeys(d["rpc_endpoint"] for d in devices if d["rpc_endpoint"]))
    else:
        device_order = [a["device"] for a in allocations]
    node_ids = [n["node_id"] for n in nodes]
    node_alloc = [sum(a["estimated_bytes"] for a in allocations if a["node_id"] == nid) + (cpu_bytes if nid == master["node_id"] else 0) for nid in node_ids]
    node_workload = {nid: sum(sum(costs[a["layer_start"]:a["layer_end"]]) for a in allocations if a["node_id"] == nid) for nid in node_ids}
    budget = cpu_budget + sum(d["budget_bytes"] for d in devices if not (d["local"] and d["unified_memory"]))
    tight = fits and (cpu_bytes > cpu_budget * .9 or any(a["estimated_bytes"] > a["budget_bytes"] * .9 for a in allocations))
    return dict(node_ids=node_ids, node_alloc_bytes=node_alloc, distributable_bytes=model_bytes,
                node_workload_bytes=node_workload, node_gpu_budget_bytes=gpu_budgets,
                node_target_bytes={nid: round(node_shares[nid] * model_bytes) for nid in node_ids} if node_shares is not None else None,
                budgets_bytes=[ram(n) if n["is_master"] else sum(d["budget_bytes"] for d in devices if d["node_id"] == n["node_id"]) for n in nodes],
                tensor_split=split, tensor_split_csv=",".join(f"{v:.6f}" for v in split) or "0",
                device_allocations=allocations, device_order=device_order,
                rpc_endpoints=endpoints,
                routing_mode=routing_mode,
                cpu_layers=cpu_count, cpu_alloc_bytes=cpu_bytes, ngl=ngl,
                estimated_layers=count-1, model_size_bytes=size, context_effective=context,
                kv_estimate_bytes=memory["kv_bytes"], recurrent_state_bytes=memory["state_bytes"],
                checkpoint_estimate_bytes=memory["checkpoint_bytes"],
                execution_overhead_bytes=memory["overhead"] * (len(allocations)+1),
                estimated_bytes=sum(node_alloc), total_budget_bytes=budget, budget_bytes=budget,
                estimate_confidence=memory["confidence"], architecture=memory["architecture"],
                split_mode=split_mode, device_requested=device, device_effective="gpu" if ngl else "cpu",
                node_devices=[dict(node_id=nid, effective="gpu" if any(a["node_id"] == nid for a in allocations) else "cpu", fallback=False) for nid in node_ids],
                node_scores=[max((d["score"] for d in devices if d["node_id"] == nid), default=0) for nid in node_ids],
                node_latency_ms=[n.get("latency_ms") or 0 for n in nodes],
                vram_alloc_bytes=sum(a["estimated_bytes"] for a in allocations), ram_alloc_bytes=cpu_bytes,
                fit_status="overflow" if not fits else "tight" if tight else "fits",
                hint=(failure or "no contiguous device allocation fits available memory") if not fits else "allocation exceeds 90% of a memory budget" if tight else "")
