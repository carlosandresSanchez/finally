"""Shared fixtures: an isolated app (temp SQLite, deterministic simulator, mock LLM)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import Database
from app.main import create_app
from app.market import PriceCache
from app.market.simulator import SimulatorDataSource


def _source_factory(cache: PriceCache) -> SimulatorDataSource:
    # Long interval: prices stay at their seeds during a test, so math is predictable.
    return SimulatorDataSource(price_cache=cache, update_interval=3600, seed=42)


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


@pytest.fixture
def client(db, tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "true")
    app = create_app(
        database=db,
        source_factory=_source_factory,
        snapshot_interval=3600,
        frontend_dir=tmp_path / "no-frontend",
    )
    with TestClient(app) as c:
        yield c
