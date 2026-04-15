"""
FastAPI Application — Intelligent Financial Analytics & Trading Agent.

This module defines all HTTP routes for:
  1. Market data & analytics (Sprint 1 — existing).
  2. Agent: signals, backtesting, paper trading, risk checks (Sprint 2+).
  3. Health / observability endpoints.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import os

from backend.config import get_config, setup_logging
from backend.services.data_service import DataService
from backend.services.dhan_service import dhan_service

# Lazy imports for agent services (avoids loading heavy ML models at startup)
_agent_services: Dict[str, Any] = {}

logger = setup_logging("agent.api")
cfg = get_config()


def _get_data_ingestion():
    if "data_ingestion" not in _agent_services:
        from backend.services.data_storage import DataIngestionService
        _agent_services["data_ingestion"] = DataIngestionService()
    return _agent_services["data_ingestion"]


def _get_meta_learner():
    if "meta_learner" not in _agent_services:
        from backend.services.meta_learner import MetaLearner
        _agent_services["meta_learner"] = MetaLearner()
    return _agent_services["meta_learner"]


def _get_paper_trader():
    if "paper_trader" not in _agent_services:
        from backend.services.paper_trading_engine import PaperTradingEngine
        _agent_services["paper_trader"] = PaperTradingEngine()
    return _agent_services["paper_trader"]


def _get_risk_manager():
    if "risk_manager" not in _agent_services:
        from backend.services.risk_manager import RiskManager
        _agent_services["risk_manager"] = RiskManager()
    return _agent_services["risk_manager"]


def _get_backtest_engine():
    from backend.services.backtest_engine import BacktestEngine
    return BacktestEngine()


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Server starting — %s v%s",
                cfg.get("app.name"), cfg.get("app.version"))
    yield
    logger.info("Server shutting down")


app = FastAPI(
    title="Intelligent Financial Analytics & Trading Agent API",
    version=cfg.get("app.version", "2.0.0"),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# Pydantic request / response models
# ============================================================================
class OrderRequest(BaseModel):
    ticker: str
    quantity: int
    side: str


class PaperTradeRequest(BaseModel):
    ticker: str
    side: str = Field(..., pattern="^(buy|sell)$")
    quantity: int = Field(..., gt=0)
    entry_price: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    confidence: float = Field(0.0, ge=0, le=1)
    reasoning: str = ""


class BacktestRequest(BaseModel):
    ticker: str
    strategy: str = "meta_learner"
    initial_capital: Optional[float] = None


class PriceUpdateRequest(BaseModel):
    prices: Dict[str, float]  # {ticker: current_price}


# ============================================================================
# Health & Observability
# ============================================================================
@app.get("/api/health")
async def health_check() -> Dict[str, Any]:
    """System health check with component status."""
    return {
        "status": "healthy",
        "version": cfg.get("app.version"),
        "environment": cfg.get("app.environment"),
        "components": {
            "dhan": "connected" if dhan_service.is_connected() else "disconnected",
            "paper_trading": cfg.get("paper_trading.enabled", False),
            "watchlist_size": len(cfg.get("data.watchlist", [])),
        },
    }


# ============================================================================
# Sprint 1 — Market Data (preserved from original)
# ============================================================================
@app.get("/api/stocks/top")
async def get_top_stocks():
    """Fetch top IT stocks with summary data."""
    return DataService.get_top_it_stocks()


@app.get("/api/stocks/search")
async def search_stocks(q: str = ""):
    """Search for stocks by name or ticker."""
    if not q:
        return []
    return DataService.search_tickers(q)


@app.get("/api/stocks/{ticker}")
async def get_stock_details(ticker: str, period: str = "1y"):
    """Detailed analytics for a specific ticker."""
    data = DataService.get_stock_data(ticker, period=period)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    return data


@app.get("/api/portfolio/summary")
async def get_portfolio_summary():
    """Fetch live Dhan holdings."""
    if not dhan_service.is_connected():
        return {"status": "disconnected", "message": "Dhan credentials not found."}

    holdings = dhan_service.get_holdings()
    positions = dhan_service.get_positions()

    return {
        "status": "connected",
        "holdings": holdings.get("data", []),
        "positions": positions.get("data", []),
    }


@app.post("/api/trade/place")
async def place_order(order: OrderRequest):
    """Place a live trade order via Dhan."""
    if not dhan_service.is_connected():
        return {"status": "error", "message": "Dhan not connected."}

    transaction_type = 0 if order.side.lower() == "buy" else 1
    response = dhan_service.place_market_order(
        order.ticker, order.quantity, transaction_type
    )
    return response


# ============================================================================
# Sprint 2+ — Agent: Signals
# ============================================================================
@app.get("/api/agent/signal/{ticker}")
async def get_agent_signal(ticker: str) -> Dict[str, Any]:
    """Run the meta-learner for a single ticker and return the trade decision.

    This is the primary endpoint for getting AI-driven trade signals.
    """
    start = time.perf_counter()

    try:
        ingestion = _get_data_ingestion()
        meta = _get_meta_learner()

        # Get OHLCV data (from cache or backfill)
        ohlcv = ingestion.get_ohlcv(ticker)
        if ohlcv is None or len(ohlcv) < 50:
            raise HTTPException(
                status_code=404,
                detail=f"Insufficient historical data for {ticker}",
            )

        # Get news headlines from existing DataService
        stock_data = DataService.get_stock_data(ticker, period="1mo")
        headlines = [n.get("title", "") for n in stock_data.get("news", [])]

        decision = meta.decide(ticker, ohlcv, headlines)
        elapsed = (time.perf_counter() - start) * 1000

        result = decision.to_dict()
        result["api_latency_ms"] = round(elapsed, 1)
        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Signal generation failed for %s", ticker)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/agent/signals/watchlist")
async def get_watchlist_signals() -> Dict[str, Any]:
    """Run signals for all tickers on the watchlist."""
    watchlist: List[str] = cfg.get("data.watchlist", [])
    results = {}

    for ticker in watchlist:
        try:
            ingestion = _get_data_ingestion()
            meta = _get_meta_learner()
            ohlcv = ingestion.get_ohlcv(ticker)
            if ohlcv is None or len(ohlcv) < 50:
                results[ticker] = {"signal_label": "NO_DATA", "confidence": 0}
                continue
            stock_data = DataService.get_stock_data(ticker, period="1mo")
            headlines = [n.get("title", "") for n in stock_data.get("news", [])]
            decision = meta.decide(ticker, ohlcv, headlines)
            results[ticker] = decision.to_dict()
        except Exception as exc:
            logger.warning("Signal failed for %s: %s", ticker, str(exc))
            results[ticker] = {"signal_label": "ERROR", "error": str(exc)}

    return {"watchlist": results, "count": len(results)}


# ============================================================================
# Agent: Backtesting
# ============================================================================
@app.post("/api/agent/backtest")
async def run_backtest(req: BacktestRequest) -> Dict[str, Any]:
    """Run a backtest on historical data for a ticker.

    Uses the full stored OHLCV data (up to 24 months).
    """
    try:
        ingestion = _get_data_ingestion()
        engine = _get_backtest_engine()

        ohlcv = ingestion.get_ohlcv(req.ticker)
        if ohlcv is None or len(ohlcv) < 300:
            raise HTTPException(
                status_code=400,
                detail=f"Need at least 300 days of data for backtest (have {len(ohlcv) if ohlcv is not None else 0})",
            )

        if req.initial_capital:
            engine._initial_capital = req.initial_capital

        result = engine.run(
            ticker=req.ticker,
            ohlcv_df=ohlcv,
            strategy=req.strategy,
        )
        return result.to_dict()

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Backtest failed for %s", req.ticker)
        raise HTTPException(status_code=500, detail=str(exc))


# ============================================================================
# Agent: Paper Trading
# ============================================================================
@app.get("/api/agent/paper/portfolio")
async def get_paper_portfolio() -> Dict[str, Any]:
    """Get current paper trading portfolio state."""
    trader = _get_paper_trader()
    return trader.get_portfolio_summary()


@app.post("/api/agent/paper/trade")
async def place_paper_trade(req: PaperTradeRequest) -> Dict[str, Any]:
    """Place a paper trade manually."""
    try:
        trader = _get_paper_trader()
        risk = _get_risk_manager()

        # Run risk check first
        portfolio = trader.get_portfolio_summary()
        peak_nav = max(
            [s.nav for s in trader.snapshots] + [portfolio["nav"]]
        ) if trader.snapshots else portfolio["nav"]

        check = risk.validate_trade(
            ticker=req.ticker,
            side=req.side,
            quantity=req.quantity,
            entry_price=req.entry_price,
            stop_loss=req.stop_loss,
            portfolio_nav=portfolio["nav"],
            cash=portfolio["cash"],
            open_position_count=portfolio["open_positions"],
            peak_nav=peak_nav,
        )

        if not check.approved:
            return {
                "status": "rejected",
                "reason": check.reason,
                "risk_metrics": check.risk_metrics,
            }

        order = trader.place_order(
            ticker=req.ticker,
            side=req.side,
            quantity=check.suggested_quantity,
            entry_price=req.entry_price,
            stop_loss=req.stop_loss,
            take_profit=req.take_profit,
            confidence=req.confidence,
            reasoning=req.reasoning,
        )

        return {
            "status": "filled",
            "order": order.to_dict(),
            "risk_check": check.reason,
        }

    except ValueError as ve:
        return {"status": "error", "message": str(ve)}
    except Exception as exc:
        logger.exception("Paper trade failed")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/agent/paper/update-prices")
async def update_paper_prices(req: PriceUpdateRequest) -> Dict[str, Any]:
    """Update paper positions with current market prices (checks SL/TP)."""
    trader = _get_paper_trader()
    closed = trader.update_prices(req.prices)
    return {
        "closed_count": len(closed),
        "closed_orders": [o.to_dict() for o in closed],
        "portfolio": trader.get_portfolio_summary(req.prices),
    }


@app.post("/api/agent/paper/reset")
async def reset_paper_portfolio() -> Dict[str, str]:
    """Reset paper portfolio to initial capital."""
    trader = _get_paper_trader()
    trader.reset()
    return {"status": "ok", "message": "Paper portfolio reset"}


# ============================================================================
# Agent: Data Management
# ============================================================================
@app.post("/api/agent/data/backfill")
async def backfill_data(
    ticker: Optional[str] = Query(None, description="Single ticker; omit for all"),
) -> Dict[str, Any]:
    """Trigger historical data backfill (24 months)."""
    ingestion = _get_data_ingestion()
    if ticker:
        success = ingestion.backfill_ticker(ticker)
        return {"ticker": ticker, "success": success}
    else:
        results = ingestion.backfill_all()
        return {"results": results, "total": len(results)}


@app.post("/api/agent/data/update")
async def update_data() -> Dict[str, Any]:
    """Incrementally update all watchlist tickers."""
    ingestion = _get_data_ingestion()
    results = ingestion.update_all()
    return {"results": results}


@app.get("/api/agent/config")
async def get_agent_config() -> Dict[str, Any]:
    """Return non-sensitive agent configuration for the dashboard."""
    return {
        "watchlist": cfg.get("data.watchlist", []),
        "paper_trading_enabled": cfg.get("paper_trading.enabled", False),
        "risk": {
            "max_position_pct": cfg.get("risk.max_position_pct"),
            "max_daily_loss_pct": cfg.get("risk.max_daily_loss_pct"),
            "max_drawdown_pct": cfg.get("risk.max_total_drawdown_pct"),
            "max_open_positions": cfg.get("risk.max_open_positions"),
        },
        "meta_learner": {
            "alignment_threshold": cfg.get("meta_learner.alignment_threshold"),
            "legacy_weight": cfg.get("meta_learner.legacy_council_weight"),
            "sota_weight": cfg.get("meta_learner.sota_council_weight"),
        },
    }


# ============================================================================
# Static File Serving (must be last — catches all unmatched routes)
# ============================================================================
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)

app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")


# ============================================================================
# Entry Point
# ============================================================================
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
