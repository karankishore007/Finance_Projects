"""
Backtesting Engine — Historical strategy simulation with realistic
position management, commission, slippage, and performance metrics.

Reports: CAGR, Max Drawdown, Sharpe Ratio, Win Rate, Equity Curve.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from backend.config import get_config, setup_logging
from backend.services.meta_learner import MetaLearner, TradeDecision, calculate_atr

logger = setup_logging("agent.backtest")
cfg = get_config()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class BacktestTrade:
    """Record of a single completed trade within a backtest."""

    ticker: str
    side: str                  # "buy" or "sell"
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    exit_reason: str           # "SL" | "TP" | "signal_reversal" | "end_of_data"
    holding_days: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "side": self.side,
            "entry_date": self.entry_date,
            "entry_price": round(self.entry_price, 2),
            "exit_date": self.exit_date,
            "exit_price": round(self.exit_price, 2),
            "quantity": self.quantity,
            "pnl": round(self.pnl, 2),
            "pnl_pct": round(self.pnl_pct, 4),
            "exit_reason": self.exit_reason,
            "holding_days": self.holding_days,
        }


@dataclass
class BacktestResult:
    """Aggregate performance of a backtest run."""

    ticker: str
    strategy: str
    start_date: str
    end_date: str
    initial_capital: float

    # Performance metrics
    final_capital: float = 0.0
    total_return_pct: float = 0.0
    cagr: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0

    # Trade stats
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_holding_days: float = 0.0

    # Series data
    equity_curve: List[float] = field(default_factory=list)
    drawdown_curve: List[float] = field(default_factory=list)
    equity_dates: List[str] = field(default_factory=list)
    trades: List[Dict] = field(default_factory=list)

    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "strategy": self.strategy,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "initial_capital": self.initial_capital,
            "final_capital": round(self.final_capital, 2),
            "total_return_pct": round(self.total_return_pct, 4),
            "cagr": round(self.cagr, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "avg_win_pct": round(self.avg_win_pct, 4),
            "avg_loss_pct": round(self.avg_loss_pct, 4),
            "avg_holding_days": round(self.avg_holding_days, 1),
            "equity_curve": self.equity_curve,
            "drawdown_curve": self.drawdown_curve,
            "equity_dates": self.equity_dates,
            "trades": self.trades,
            "latency_ms": round(self.latency_ms, 0),
        }


# ============================================================================
# Open position tracker (internal)
# ============================================================================
@dataclass
class _Position:
    ticker: str
    side: str
    entry_date: str
    entry_price: float
    quantity: int
    stop_loss: float
    take_profit: float


# ============================================================================
# Backtest Engine
# ============================================================================
class BacktestEngine:
    """Walk-forward backtester with configurable strategy function.

    Usage::

        engine = BacktestEngine()
        result = engine.run(
            ticker="TCS.NS",
            ohlcv_df=dataframe,
            headlines_by_date={},
            strategy="meta_learner",
        )
    """

    def __init__(
        self,
        initial_capital: Optional[float] = None,
        commission: Optional[float] = None,
        slippage_bps: Optional[int] = None,
    ) -> None:
        self._initial_capital = initial_capital or cfg.get(
            "backtest.default_initial_capital", 1_000_000
        )
        self._commission = commission if commission is not None else cfg.get(
            "backtest.commission_per_trade", 20.0
        )
        self._slippage_bps = slippage_bps if slippage_bps is not None else cfg.get(
            "backtest.slippage_bps", 5
        )
        self._warmup = cfg.get("backtest.warmup_days", 252)
        self._risk_free = cfg.get("backtest.risk_free_rate", 0.065)
        self._risk_per_trade = cfg.get("risk.risk_per_trade_pct", 0.02)
        self._max_position_pct = cfg.get("risk.max_position_pct", 0.05)

    # ------------------------------------------------------------------
    def run(
        self,
        ticker: str,
        ohlcv_df: pd.DataFrame,
        headlines_by_date: Optional[Dict[str, List[str]]] = None,
        strategy: str = "meta_learner",
    ) -> BacktestResult:
        """Execute backtest on historical data.

        Args:
            ticker:            NSE ticker (e.g. ``"TCS.NS"``).
            ohlcv_df:          Full historical OHLCV DataFrame.
            headlines_by_date: Optional ``{date_str: [headlines]}`` map.
            strategy:          ``"meta_learner"`` or a specific model name.

        Returns:
            BacktestResult with all performance metrics.
        """
        bt_start = time.perf_counter()
        if headlines_by_date is None:
            headlines_by_date = {}

        if len(ohlcv_df) <= self._warmup:
            logger.warning(
                "Not enough data for backtest (have %d, need >%d)",
                len(ohlcv_df), self._warmup,
            )
            return BacktestResult(
                ticker=ticker, strategy=strategy,
                start_date="", end_date="",
                initial_capital=self._initial_capital,
            )

        meta = MetaLearner()
        capital = self._initial_capital
        position: Optional[_Position] = None
        trades: List[BacktestTrade] = []
        equity_curve: List[float] = []
        equity_dates: List[str] = []

        start_idx = self._warmup
        end_idx = len(ohlcv_df)

        for idx in range(start_idx, end_idx):
            current_date = ohlcv_df.index[idx]
            date_str = current_date.strftime("%Y-%m-%d")
            current_price = float(ohlcv_df["Close"].iloc[idx])
            current_high = float(ohlcv_df["High"].iloc[idx])
            current_low = float(ohlcv_df["Low"].iloc[idx])

            # --- Check SL/TP on existing position ---
            if position is not None:
                exit_price = None
                exit_reason = ""

                if position.side == "buy":
                    if current_low <= position.stop_loss:
                        exit_price = self._apply_slippage(position.stop_loss, "sell")
                        exit_reason = "SL"
                    elif current_high >= position.take_profit:
                        exit_price = self._apply_slippage(position.take_profit, "sell")
                        exit_reason = "TP"
                else:  # short sell
                    if current_high >= position.stop_loss:
                        exit_price = self._apply_slippage(position.stop_loss, "buy")
                        exit_reason = "SL"
                    elif current_low <= position.take_profit:
                        exit_price = self._apply_slippage(position.take_profit, "buy")
                        exit_reason = "TP"

                if exit_price is not None:
                    trade = self._close_position(position, exit_price, date_str, exit_reason)
                    capital += trade.pnl - self._commission
                    trades.append(trade)
                    position = None

            # --- Generate signal (skip if position already open) ---
            if position is None:
                hist_slice = ohlcv_df.iloc[: idx + 1]
                headlines = headlines_by_date.get(date_str, [])

                try:
                    decision = meta.decide(ticker, hist_slice, headlines)
                except Exception:
                    logger.debug("Signal generation failed at %s", date_str)
                    decision = TradeDecision(
                        ticker=ticker, signal=0, signal_label="HOLD",
                        confidence=0.0,
                    )

                if decision.signal != 0 and decision.entry_price:
                    entry_price = self._apply_slippage(
                        decision.entry_price,
                        "buy" if decision.signal == -1 else "sell",
                    )
                    atr = calculate_atr(hist_slice)
                    qty = self._position_size(capital, entry_price, decision.stop_loss or entry_price)

                    if qty > 0 and (qty * entry_price) <= capital:
                        position = _Position(
                            ticker=ticker,
                            side="buy" if decision.signal == -1 else "sell",
                            entry_date=date_str,
                            entry_price=entry_price,
                            quantity=qty,
                            stop_loss=decision.stop_loss or entry_price,
                            take_profit=decision.take_profit or entry_price,
                        )
                        capital -= self._commission  # entry commission

            # --- Track equity ---
            unrealized = 0.0
            if position is not None:
                if position.side == "buy":
                    unrealized = position.quantity * (current_price - position.entry_price)
                else:
                    unrealized = position.quantity * (position.entry_price - current_price)

            equity_curve.append(round(capital + unrealized, 2))
            equity_dates.append(date_str)

        # --- Force-close any remaining position ---
        if position is not None:
            final_price = float(ohlcv_df["Close"].iloc[-1])
            trade = self._close_position(
                position, final_price, equity_dates[-1], "end_of_data"
            )
            capital += trade.pnl - self._commission
            trades.append(trade)
            equity_curve[-1] = round(capital, 2)

        # --- Compute metrics ---
        elapsed = (time.perf_counter() - bt_start) * 1000
        result = self._compute_metrics(
            ticker=ticker,
            strategy=strategy,
            initial_capital=self._initial_capital,
            final_capital=capital,
            equity_curve=equity_curve,
            equity_dates=equity_dates,
            trades=trades,
            elapsed_ms=elapsed,
        )

        logger.info(
            "Backtest %s/%s: CAGR=%.2f%% Sharpe=%.2f MaxDD=%.2f%% Trades=%d (%.0fms)",
            ticker, strategy,
            result.cagr * 100,
            result.sharpe_ratio,
            result.max_drawdown * 100,
            result.total_trades,
            elapsed,
        )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _apply_slippage(self, price: float, side: str) -> float:
        """Apply slippage to a fill price."""
        slip = price * (self._slippage_bps / 10_000)
        return price + slip if side == "buy" else price - slip

    def _position_size(
        self, capital: float, entry: float, stop_loss: float,
    ) -> int:
        """Risk-based position sizing (Kelly-lite)."""
        price_risk = abs(entry - stop_loss)
        if price_risk <= 0:
            return 0

        risk_amount = capital * self._risk_per_trade
        qty_by_risk = int(risk_amount / price_risk)

        max_by_position = int((capital * self._max_position_pct) / entry)
        return max(1, min(qty_by_risk, max_by_position))

    @staticmethod
    def _close_position(
        pos: _Position, exit_price: float, date: str, reason: str,
    ) -> BacktestTrade:
        if pos.side == "buy":
            pnl = pos.quantity * (exit_price - pos.entry_price)
        else:
            pnl = pos.quantity * (pos.entry_price - exit_price)

        pnl_pct = pnl / (pos.quantity * pos.entry_price) if pos.entry_price else 0

        entry_dt = datetime.strptime(pos.entry_date, "%Y-%m-%d")
        exit_dt = datetime.strptime(date, "%Y-%m-%d")
        holding = (exit_dt - entry_dt).days

        return BacktestTrade(
            ticker=pos.ticker,
            side=pos.side,
            entry_date=pos.entry_date,
            entry_price=pos.entry_price,
            exit_date=date,
            exit_price=exit_price,
            quantity=pos.quantity,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=reason,
            holding_days=holding,
        )

    def _compute_metrics(
        self,
        ticker: str,
        strategy: str,
        initial_capital: float,
        final_capital: float,
        equity_curve: List[float],
        equity_dates: List[str],
        trades: List[BacktestTrade],
        elapsed_ms: float,
    ) -> BacktestResult:
        eq = np.array(equity_curve, dtype=float)
        n_days = len(eq)

        # Total return
        total_return = (final_capital - initial_capital) / initial_capital

        # CAGR
        years = n_days / 252.0
        cagr = ((final_capital / initial_capital) ** (1.0 / max(years, 0.01))) - 1.0 if final_capital > 0 else -1.0

        # Drawdown
        running_max = np.maximum.accumulate(eq)
        drawdown = (eq - running_max) / np.where(running_max > 0, running_max, 1.0)
        max_dd = float(np.min(drawdown))
        dd_curve = drawdown.tolist()

        # Daily returns
        daily_returns = np.diff(eq) / np.where(eq[:-1] > 0, eq[:-1], 1.0)
        daily_rf = self._risk_free / 252.0

        # Sharpe
        excess = daily_returns - daily_rf
        sharpe = (
            float(np.mean(excess) / np.std(excess) * np.sqrt(252))
            if np.std(excess) > 0 else 0.0
        )

        # Sortino
        downside = excess[excess < 0]
        downside_std = float(np.std(downside)) if len(downside) > 0 else 1.0
        sortino = (
            float(np.mean(excess) / downside_std * np.sqrt(252))
            if downside_std > 0 else 0.0
        )

        # Trade stats
        winners = [t for t in trades if t.pnl > 0]
        losers = [t for t in trades if t.pnl <= 0]
        win_rate = len(winners) / len(trades) if trades else 0.0
        avg_win = float(np.mean([t.pnl_pct for t in winners])) if winners else 0.0
        avg_loss = float(np.mean([t.pnl_pct for t in losers])) if losers else 0.0
        gross_profit = sum(t.pnl for t in winners)
        gross_loss = abs(sum(t.pnl for t in losers))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        avg_hold = float(np.mean([t.holding_days for t in trades])) if trades else 0.0

        return BacktestResult(
            ticker=ticker,
            strategy=strategy,
            start_date=equity_dates[0] if equity_dates else "",
            end_date=equity_dates[-1] if equity_dates else "",
            initial_capital=initial_capital,
            final_capital=round(final_capital, 2),
            total_return_pct=total_return,
            cagr=cagr,
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            win_rate=win_rate,
            profit_factor=profit_factor,
            total_trades=len(trades),
            winning_trades=len(winners),
            losing_trades=len(losers),
            avg_win_pct=avg_win,
            avg_loss_pct=avg_loss,
            avg_holding_days=avg_hold,
            equity_curve=[round(v, 2) for v in equity_curve],
            drawdown_curve=[round(v, 4) for v in dd_curve],
            equity_dates=equity_dates,
            trades=[t.to_dict() for t in trades],
            latency_ms=elapsed_ms,
        )
