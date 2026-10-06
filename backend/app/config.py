"""Runtime configuration read from the environment (and the project-root .env)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

# Real environment variables win over .env (override=False).
load_dotenv(PROJECT_ROOT / ".env", override=False)

DEFAULT_USER_ID = "default"
DEFAULT_CASH = 10_000.0
DEFAULT_WATCHLIST = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]

SNAPSHOT_INTERVAL_SECONDS = 30.0


def db_path() -> Path:
    """SQLite file location. DB_PATH overrides; default is <project>/db/finally.db."""
    raw = os.environ.get("DB_PATH", "").strip()
    return Path(raw) if raw else PROJECT_ROOT / "db" / "finally.db"


def static_dir() -> Path:
    """Directory holding the exported Next.js frontend. STATIC_DIR overrides."""
    raw = os.environ.get("STATIC_DIR", "").strip()
    return Path(raw) if raw else BACKEND_DIR / "static"


def llm_mock() -> bool:
    return os.environ.get("LLM_MOCK", "").strip().lower() in {"1", "true", "yes"}
