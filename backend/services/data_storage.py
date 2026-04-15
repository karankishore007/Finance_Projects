"""
Data Storage Layer — Persistent local cache for historical OHLCV,
fundamentals, and sentiment data.

Uses Parquet for time-series (columnar, fast reads) and SQLite for
structured metadata / sentiment logs.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

import pandas as pd
import yfinance as yf

from backend.config import get_config, resolve_path, setup_logging

logger = setup_logging("agent.data_storage")
cfg = get_config()


# ---------------------------------------------------------------------------
# Helper: retry with exponential backoff
# ---------------------------------------------------------------------------
def _retry(fn, *, max_retries: int = 3, backoff: float = 1.5, label: str = ""):
    """Execute *fn* with exponential-backoff retries on transient failures."""
    retries = cfg.get("data.api_retry.max_retries", max_retries)
    factor = cfg.get("data.api_retry.backoff_factor", backoff)
    last_exc: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            wait = factor ** attempt
            logger.warning(
                "Retry %d/%d for %s — waiting %.1fs",
                attempt, retries, label, wait,
                extra={"error_type": type(exc).__name__, "context": label},
            )
            time.sleep(wait)

    logger.error("All %d retries exhausted for %s", retries, label, exc_info=last_exc)
    raise last_exc  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SQLite context manager
# ---------------------------------------------------------------------------
@contextmanager
def _sqlite_connection(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Yield a SQLite connection with auto-commit / rollback."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================================
# OHLCV Storage (Parquet)
# ============================================================================
class OHLCVStorage:
    """Read / write daily OHLCV data as per-ticker Parquet files."""

    def __init__(self) -> None:
        self._dir = resolve_path("data.storage.historical_dir")
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, ticker: str) -> Path:
        safe = ticker.replace(".", "_").replace("/", "_")
        return self._dir / f"{safe}.parquet"

    # ------------------------------------------------------------------
    def store(self, ticker: str, df: pd.DataFrame) -> None:
        """Persist a DataFrame of OHLCV data (indexed by Date)."""
        path = self._path_for(ticker)
        start_ts = time.perf_counter()
        try:
            if path.exists():
                existing = pd.read_parquet(path)
                combined = pd.concat([existing, df])
                combined = combined[~combined.index.duplicated(keep="last")]
                combined.sort_index(inplace=True)
                combined.to_parquet(path, engine="pyarrow")
            else:
                df.sort_index().to_parquet(path, engine="pyarrow")
            elapsed = (time.perf_counter() - start_ts) * 1000
            logger.info(
                "Stored %d rows for %s (%.1fms)",
                len(df), ticker, elapsed,
                extra={"ticker": ticker, "latency_ms": round(elapsed, 1)},
            )
        except Exception:
            logger.exception("Failed to store OHLCV for %s", ticker, extra={"ticker": ticker})
            raise

    def load(
        self,
        ticker: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        """Load stored OHLCV data, optionally filtered by date range."""
        path = self._path_for(ticker)
        if not path.exists():
            logger.debug("No cached data for %s", ticker, extra={"ticker": ticker})
            return None
        df = pd.read_parquet(path)
        if start_date:
            df = df[df.index >= pd.Timestamp(start_date)]
        if end_date:
            df = df[df.index <= pd.Timestamp(end_date)]
        return df if not df.empty else None

    def has_data(self, ticker: str) -> bool:
        return self._path_for(ticker).exists()

    def latest_date(self, ticker: str) -> Optional[datetime]:
        """Return the most recent date in the stored data."""
        df = self.load(ticker)
        if df is not None and not df.empty:
            return df.index.max().to_pydatetime()
        return None


# ============================================================================
# Fundamentals Storage (Parquet)
# ============================================================================
class FundamentalsStorage:
    """Store quarterly fundamentals (P/E, D/E, revenue, net income)."""

    def __init__(self) -> None:
        self._dir = resolve_path("data.storage.fundamentals_dir")
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, ticker: str) -> Path:
        safe = ticker.replace(".", "_").replace("/", "_")
        return self._dir / f"{safe}_fundamentals.parquet"

    def store(self, ticker: str, df: pd.DataFrame) -> None:
        path = self._path_for(ticker)
        df.to_parquet(path, engine="pyarrow")
        logger.info("Stored fundamentals for %s (%d rows)", ticker, len(df),
                     extra={"ticker": ticker})

    def load(self, ticker: str) -> Optional[pd.DataFrame]:
        path = self._path_for(ticker)
        if not path.exists():
            return None
        return pd.read_parquet(path)


# ============================================================================
# Sentiment Storage (SQLite)
# ============================================================================
class SentimentStorage:
    """Append-only log of headline-level sentiment scores."""

    _DDL = """
    CREATE TABLE IF NOT EXISTS sentiment (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker      TEXT    NOT NULL,
        date        TEXT    NOT NULL,
        headline    TEXT    NOT NULL,
        score       REAL    NOT NULL,
        label       TEXT    NOT NULL,
        source      TEXT,
        model       TEXT    DEFAULT 'vader',
        created_at  TEXT    DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_sentiment_ticker_date
        ON sentiment (ticker, date);
    """

    def __init__(self) -> None:
        self._db_path = resolve_path("data.storage.sentiment_db")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with _sqlite_connection(self._db_path) as conn:
            conn.executescript(self._DDL)

    def insert(
        self,
        ticker: str,
        date: str,
        headline: str,
        score: float,
        label: str,
        source: str = "",
        model: str = "vader",
    ) -> None:
        with _sqlite_connection(self._db_path) as conn:
            conn.execute(
                "INSERT INTO sentiment (ticker, date, headline, score, label, source, model) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticker, date, headline, score, label, source, model),
            )

    def insert_batch(self, rows: List[Dict[str, Any]]) -> None:
        """Bulk-insert sentiment rows efficiently."""
        if not rows:
            return
        with _sqlite_connection(self._db_path) as conn:
            conn.executemany(
                "INSERT INTO sentiment (ticker, date, headline, score, label, source, model) "
                "VALUES (:ticker, :date, :headline, :score, :label, :source, :model)",
                rows,
            )
        logger.info("Inserted %d sentiment records", len(rows))

    def query(
        self,
        ticker: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return recent sentiment rows for a ticker."""
        sql = "SELECT * FROM sentiment WHERE ticker = ?"
        params: list = [ticker]
        if start_date:
            sql += " AND date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND date <= ?"
            params.append(end_date)
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(limit)

        with _sqlite_connection(self._db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


# ============================================================================
# Data Ingestion Orchestrator
# ============================================================================
class DataIngestionService:
    """High-level service that backfills and incrementally updates data."""

    def __init__(self) -> None:
        self.ohlcv = OHLCVStorage()
        self.fundamentals = FundamentalsStorage()
        self.sentiment = SentimentStorage()
        self._watchlist: List[str] = cfg.get("data.watchlist", [])
        self._backfill_months: int = cfg.get("data.backfill_period_months", 24)

    # ------------------------------------------------------------------
    def backfill_ticker(self, ticker: str) -> bool:
        """Download full historical OHLCV and store locally.

        Returns:
            True if data was successfully fetched and stored.
        """
        period = f"{self._backfill_months}mo"
        logger.info("Backfilling %s for %s", ticker, period, extra={"ticker": ticker})

        try:
            def _fetch():
                t = yf.Ticker(ticker)
                return t.history(period=period, interval="1d")

            hist = _retry(_fetch, label=f"backfill:{ticker}")
            if hist is None or hist.empty:
                logger.warning("No data returned for %s", ticker, extra={"ticker": ticker})
                return False

            self.ohlcv.store(ticker, hist)
            return True
        except Exception:
            logger.exception("Backfill failed for %s", ticker, extra={"ticker": ticker})
            return False

    def backfill_all(self) -> Dict[str, bool]:
        """Backfill every ticker on the watchlist."""
        results: Dict[str, bool] = {}
        for ticker in self._watchlist:
            results[ticker] = self.backfill_ticker(ticker)
        success = sum(v for v in results.values())
        logger.info(
            "Backfill complete: %d/%d tickers succeeded",
            success, len(self._watchlist),
        )
        return results

    def incremental_update(self, ticker: str) -> bool:
        """Fetch only new data since the last stored date."""
        latest = self.ohlcv.latest_date(ticker)
        if latest is None:
            return self.backfill_ticker(ticker)

        start = (latest + timedelta(days=1)).strftime("%Y-%m-%d")
        end = datetime.now().strftime("%Y-%m-%d")
        if start >= end:
            logger.debug("Data already up-to-date for %s", ticker, extra={"ticker": ticker})
            return True

        try:
            def _fetch():
                t = yf.Ticker(ticker)
                return t.history(start=start, end=end, interval="1d")

            hist = _retry(_fetch, label=f"incremental:{ticker}")
            if hist is not None and not hist.empty:
                self.ohlcv.store(ticker, hist)
            return True
        except Exception:
            logger.exception("Incremental update failed for %s", ticker, extra={"ticker": ticker})
            return False

    def update_all(self) -> Dict[str, bool]:
        """Incrementally update every ticker on the watchlist."""
        results: Dict[str, bool] = {}
        for ticker in self._watchlist:
            results[ticker] = self.incremental_update(ticker)
        return results

    def fetch_fundamentals(self, ticker: str) -> bool:
        """Fetch and store quarterly fundamentals for a ticker."""
        try:
            def _fetch():
                return yf.Ticker(ticker)

            t = _retry(_fetch, label=f"fundamentals:{ticker}")
            info = t.info or {}

            q_fin = t.quarterly_financials
            records = []
            if q_fin is not None and not q_fin.empty:
                for col in q_fin.columns:
                    row: Dict[str, Any] = {"date": col}
                    for idx_name in q_fin.index:
                        row[idx_name] = (
                            float(q_fin.loc[idx_name, col])
                            if pd.notna(q_fin.loc[idx_name, col])
                            else None
                        )
                    records.append(row)

            # Attach valuation ratios from info
            fundamentals_df = pd.DataFrame(records) if records else pd.DataFrame()
            if not fundamentals_df.empty:
                fundamentals_df["pe_ratio"] = info.get("trailingPE")
                fundamentals_df["pb_ratio"] = info.get("priceToBook")
                fundamentals_df["debt_to_equity"] = info.get("debtToEquity")
                fundamentals_df["market_cap"] = info.get("marketCap")
                self.fundamentals.store(ticker, fundamentals_df)

            logger.info("Fetched fundamentals for %s", ticker, extra={"ticker": ticker})
            return True
        except Exception:
            logger.exception("Fundamentals fetch failed for %s", ticker, extra={"ticker": ticker})
            return False

    def get_ohlcv(
        self,
        ticker: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        """Public accessor — load OHLCV from cache, backfill if missing."""
        df = self.ohlcv.load(ticker, start_date, end_date)
        if df is None:
            logger.info("Cache miss for %s, triggering backfill", ticker, extra={"ticker": ticker})
            self.backfill_ticker(ticker)
            df = self.ohlcv.load(ticker, start_date, end_date)
        return df
