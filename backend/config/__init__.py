"""
Configuration management for the Financial Trading Agent.

Provides centralized access to YAML config and structured logging setup.
"""

import os
import yaml
import logging
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pathlib import Path


# ---------------------------------------------------------------------------
# Project root resolution
# ---------------------------------------------------------------------------
_THIS_DIR = Path(__file__).resolve().parent          # backend/config/
PROJECT_ROOT = _THIS_DIR.parent.parent               # Finance_Projects/
BACKEND_ROOT = _THIS_DIR.parent                      # Finance_Projects/backend/


# ---------------------------------------------------------------------------
# YAML Config Loader (singleton pattern)
# ---------------------------------------------------------------------------
class _ConfigStore:
    """Thread-safe, lazily-loaded config singleton."""

    _instance: Optional["_ConfigStore"] = None
    _data: Dict[str, Any] = {}

    def __new__(cls) -> "_ConfigStore":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        config_path = _THIS_DIR / "agent_config.yaml"
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")
        with open(config_path, "r") as fh:
            self._data = yaml.safe_load(fh) or {}

    def get(self, dotted_key: str, default: Any = None) -> Any:
        """Retrieve a nested value using dot notation.

        Example:
            cfg.get("models.legacy.mean_reversion.lookback_period", 252)
        """
        keys = dotted_key.split(".")
        node = self._data
        for key in keys:
            if isinstance(node, dict):
                node = node.get(key)
            else:
                return default
            if node is None:
                return default
        return node

    def as_dict(self) -> Dict[str, Any]:
        """Return the full config tree (read-only copy)."""
        return dict(self._data)


def get_config() -> _ConfigStore:
    """Return the global config singleton."""
    return _ConfigStore()


# ---------------------------------------------------------------------------
# Structured JSON Logger
# ---------------------------------------------------------------------------
class _JsonFormatter(logging.Formatter):
    """Produces one JSON object per log line for machine-parseable output."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # Attach extras (e.g., ticker, latency_ms, trade_id)
        for key in ("ticker", "latency_ms", "trade_id", "model", "signal",
                     "confidence", "error_type", "context"):
            val = getattr(record, key, None)
            if val is not None:
                log_entry[key] = val
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, default=str)


class _PlainFormatter(logging.Formatter):
    """Human-friendly formatter for local development."""

    FMT = "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"

    def __init__(self) -> None:
        super().__init__(fmt=self.FMT, datefmt="%Y-%m-%d %H:%M:%S")


def setup_logging(name: str = "agent") -> logging.Logger:
    """Create and return a structured logger.

    Args:
        name: Logger namespace (e.g., ``"agent.meta_learner"``).

    Returns:
        A configured :class:`logging.Logger` instance.
    """
    cfg = get_config()
    level_str = cfg.get("app.log_level", "INFO")
    log_format = cfg.get("app.log_format", "json")
    level = getattr(logging, level_str.upper(), logging.INFO)

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(level)
    logger.propagate = False

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(level)
    if log_format == "json":
        console.setFormatter(_JsonFormatter())
    else:
        console.setFormatter(_PlainFormatter())
    logger.addHandler(console)

    # File handler (append to logs/ directory)
    log_dir = BACKEND_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "agent.log", encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(_JsonFormatter())  # always JSON for files
    logger.addHandler(file_handler)

    return logger


# ---------------------------------------------------------------------------
# Convenience: resolve data paths from config
# ---------------------------------------------------------------------------
def resolve_path(config_key: str) -> Path:
    """Resolve a config-relative path to an absolute path.

    Example:
        resolve_path("data.storage.historical_dir")
        -> /abs/path/to/Finance_Projects/backend/data/historical
    """
    cfg = get_config()
    relative = cfg.get(config_key, "")
    return (PROJECT_ROOT / relative).resolve()
