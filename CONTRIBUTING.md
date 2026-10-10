# Contributing

Kluster LLM is a trusted-LAN cluster tool. Read [SECURITY.md](SECURITY.md) before changing networking, authentication, or process launch.

## Setup

Install [uv](https://docs.astral.sh/uv/), then from the repository root:

```bash
uv sync
uv run pre-commit install
```

Python is pinned in `.python-version`. After packaging or native changes, rebuild with `uv run python scripts/build.py` or `uv run python scripts/install.py`.

## Tests

`make test` runs the Python suite, the dashboard tests, the Rust tests, and the dashboard check. Without Make:

```bash
uv run python -m pytest -q
npm --prefix dashboard test
PYO3_PYTHON="$(uv run python -c 'import sys; print(sys.executable)')" cargo test --manifest-path crates/kllm-native/Cargo.toml
npm --prefix dashboard run check
```

Pull requests run Ruff and the Python tests. Run the dashboard and Rust suites locally when those areas change.

`git push` runs Ruff through the pre-commit hook on the Python files in the commits being pushed.
