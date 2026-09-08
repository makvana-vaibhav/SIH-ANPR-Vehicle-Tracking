"""Configuration for the end-to-end suite.

These tests run against a **live stack** rather than an in-process app, so they
have their own async setup: `services/api/pyproject.toml` sets
`asyncio_mode = auto` for the unit suite, and that config does not reach here.
Without this, async fixtures are collected but never awaited and every test
fails with "coroutine was never awaited" — a failure that looks like broken
tests and is really a missing setting.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "asyncio: run this test in an event loop")
