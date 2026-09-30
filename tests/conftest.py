"""Test selection notes: `-m unit` and `-m integration` are convenience subsets only.

The complete `uv run pytest -q` invocation remains the validation command and collects all
non-opt-in tests, including real integration checks.
"""
