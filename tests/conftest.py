from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Tests never read the developer's real .env or HEALTH_AGENT_* variables."""
    for key in list(os.environ):
        if key.startswith("HEALTH_AGENT_") or key == "DATABRICKS_RUNTIME_VERSION":
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def ns(**kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)
