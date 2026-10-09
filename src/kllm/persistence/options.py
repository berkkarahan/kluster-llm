"""Validated, immutable limits for each local SQLite owner."""

from dataclasses import dataclass
import math
import os


@dataclass(frozen=True)
class SQLiteOptions:
    pool_size: int = 1
    wait_timeout: float = 5.0
    shutdown_timeout: float = 10.0

    def __post_init__(self) -> None:
        if type(self.pool_size) is not int or not 1 <= self.pool_size <= 4:
            raise ValueError("KLLM_SQLITE_POOL_SIZE must be an integer from 1 to 4")
        for field, setting in (
            ("wait_timeout", "KLLM_SQLITE_WAIT_TIMEOUT"),
            ("shutdown_timeout", "KLLM_SQLITE_SHUTDOWN_TIMEOUT"),
        ):
            value = getattr(self, field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"{setting} must be a finite positive number")

    @classmethod
    def from_env(cls) -> "SQLiteOptions":
        values = {}
        for field, env, cast in (
            ("pool_size", "KLLM_SQLITE_POOL_SIZE", int),
            ("wait_timeout", "KLLM_SQLITE_WAIT_TIMEOUT", float),
            ("shutdown_timeout", "KLLM_SQLITE_SHUTDOWN_TIMEOUT", float),
        ):
            if env in os.environ:
                try:
                    values[field] = cast(os.environ[env])
                except (ValueError, OverflowError):
                    raise ValueError(f"invalid {env}") from None
        return cls(**values)
