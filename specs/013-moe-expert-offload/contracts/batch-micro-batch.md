# Contract: Batch and Micro-Batch Ceilings

## Fields and defaults (unchanged)

| Field | Wire name | Default (auto) | Acceptable explicit range |
|-------|-----------|----------------|---------------------------|
| Batch size | `batch_size` | 512 | positive integer, effective `min(requested, max(2048, context))` |
| Micro-batch | `micro_batch` | 128 | positive integer, effective `min(requested, batch_size, 512)` |

## Behavior contract

- Omitting a value (`"auto"`) resolves to the default above — unchanged from today.
- An explicit batch size is honored up to 2048 regardless of a small context window, and up to the context size when the context exceeds 2048.
- An explicit micro-batch size is honored up to 512 and never exceeds the resolved batch size.
- Non-positive values are rejected with a clear validation error. Micro-batch above 512 or above the resolved batch size is capped (clamped), matching existing micro-batch-to-batch behavior; batch above 2048 is honored up to the context size.

## Consistency contract

- The resolved values must be identical between the estimate/plan path and the launched run (`batch_size` and `micro_batch` reported and passed to the launch record agree).
- The dashboard Run panel `max` attributes and any input validation must match these ceilings so the UI cannot submit a value the backend rejects, and vice versa.

## Why these numbers

Upstream llama.cpp `llama-server` defaults: logical `--batch-size` 2048, physical `--ubatch-size` 512. kllm's own defaults (512 / 128) are intentionally lower and unchanged; the ceilings are raised to the upstream maximums so operators can tune higher while the safe defaults stay.
