# Layer Split Calculator

Automatic placement lives in `python/kllm/tensor_split.py` and `python/kllm/placement.py`. You do **not** pass `--tensor-split` on the CLI; `kllm plan` / `kllm run` emit it.

## Rules (memory-pooling first)

1. If the model fits on **master-local** GPUs (one or more), run without `--rpc`.
2. Else if **one** remote accelerator alone can hold it, use single-device RPC.
3. Else **pool** across devices with VRAM-proportional layer assignment (never an even split on heterogeneous GPUs).

Always use `--split-mode layer` (pipeline parallelism). Do not use tensor/row split across mixed backends — see [engine-decision.md](engine-decision.md).

## Proportional weights

For a 24 GiB + 8 GiB pool, the helper emits weights like `24,8` (llama.cpp normalizes):

```python
from kllm.tensor_split import tensor_split_proportions, proportional_weights

assert tensor_split_proportions([24, 8]) == [24.0, 8.0]
```

Live plans still allocate **contiguous layer counts** via dynamic programming against per-device budgets (KV + overhead aware), then launch with those counts as `--tensor-split`.

## Worker provisioning

1. `python scripts/install.py` on the worker (same `llama-release` pin as master).
2. `kllm join --key <join-key>` → registers VRAM/devices and starts `rpc-server` on private IP:50052 with `-c`.
3. On master: `kllm model download …` then `kllm plan` / `kllm run`. **No GGUF copy** to workers.

See [plans/rpc-memory-pooling.md](../plans/rpc-memory-pooling.md) and [inference-distribution.md](inference-distribution.md).
