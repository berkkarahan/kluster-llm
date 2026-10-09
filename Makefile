.PHONY: install build test hooks

install:
	uv run python scripts/install.py

build:
	uv run python scripts/build.py

hooks:
	uv sync
	uv run pre-commit install

test:
	uv run python -m pytest -q
	npm --prefix dashboard test
	PYO3_PYTHON="$$(uv run python -c 'import sys; print(sys.executable)')" cargo test --manifest-path crates/kllm-native/Cargo.toml
	npm --prefix dashboard run check
