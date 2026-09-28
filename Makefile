.PHONY: install build test

install:
	uv run python scripts/install.py

build:
	uv run python scripts/build.py

test:
	uv run pytest -q
	cargo test --manifest-path crates/kllm-native/Cargo.toml
	npm --prefix dashboard run check
