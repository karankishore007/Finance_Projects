"""
Paper Trading Engine — Virtual portfolio that simulates live execution
without real capital.  Tracks positions, SL/TP exits, and daily NAV.

State is persisted to a JSON file between server restarts.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from backend.config import get_config, resolve_path, setup_logging

logger = setup_logging("agent.paper_trading")
cfg = get_config()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class PaperOrder:
    """Represents a single paper-traded order."""

    order_id: str
    ticker: str
    side: str              # "buy" | "sell"
    quantity: int
    entry_price: float
    stop_loss: float
    take_profit: float
    status: str = "open"   # "open" | "closed"
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl: float = 0.0
    created_at: str = ""
    closed_at: Optional[str] = None
    signal_confidence: float = 0.0
    reasoning: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DailySnapshot:
    """End-of-day portfolio snapshot."""

    date: str
    nav: float
    cash: float
    unrealized_pnl: float
    realized_pnl_today: float
    open_positions: int


# ============================================================================
# Paper Trading Engine
# ============================================================================
class PaperTradingEngine:
    """Virtual trading system that mimics live Dhan execution.

    State is automatically saved to disk after every mutation so that
    server restarts don't lose paper portfolio data.
    """

    def __init__(self) -> None:
        self._initial_capital: float = cfg.get("paper_trading.initial_capital", 1_000_000)
        self._persistence_path = resolve_path("paper_trading.persistence_file")
        self._enabled: bool = cfg.get("paper_trading.enabled", True)

        # Core state
        self.cash: float = self._initial_capital
        self.orders: List[PaperOrder] = []
        self.snapshots: List[DailySnapshot] = []
        self.total_realized_pnl: float = 0.0
        self._order_counter: int = 0

        # Attempt to restore from disk
        self._restore_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def place_order(
        self,
        ticker: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        confidence: float = 0.0,
        reasoning: str = "",
    ) -> PaperOrder:
        """Create a new paper order.

        Validates cash availability and records the trade.

        Args:
            ticker: NSE ticker (e.g. ``"TCS.NS"``).
            side: ``"buy"`` or ``"sell"``.
            quantity: Number of shares.
            entry_price: Simulated fill price.
            stop_loss: SL price level.
            take_profit: TP price level.
            confidence: Meta-learner confidence.
            reasoning: Explanation string.

        Returns:
            The created PaperOrder.

        Raises:
            ValueError: If insufficient cash or invalid parameters.
        """
        if not self._enabled:
            raise RuntimeError("Paper trading is disabled in config")

        cost = quantity * entry_price
        if side == "buy" and cost > self.cash:
            raise ValueError(
                f"Insufficient cash: need ₹{cost:,.0f}, have ₹{self.cash:,.0f}"
            )

        self._order_counter += 1
        order_id = f"PT-{self._order_counter:06d}"
        now = datetime.now(timezone.utc).isoformat()

        order = PaperOrder(
            order_id=order_id,
            ticker=ticker,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status="open",
            created_at=now,
            signal_confidence=confidence,
            reasoning=reasoning,
        )

        if side == "buy":
            self.cash -= cost

        self.orders.append(order)
        self._save_state()

        logger.info(
            "Paper order %s: %s %d x %s @ ₹%.2f (SL=%.2f, TP=%.2f)",
            order_id, side.upper(), quantity, ticker, entry_price,
            stop_loss, take_profit,
            extra={"trade_id": order_id, "ticker": ticker},
        )
        return order

    def update_prices(self, market_prices: Dict[str, float]) -> List[PaperOrder]:
        """Check all open orders against current market prices for SL/TP.

        Args:
            market_prices: ``{ticker: current_price}`` map.

        Returns:
            List of orders that were closed.
        """
        closed: List[PaperOrder] = []

        for order in self.orders:
            if order.status != "open":
                continue

            price = market_prices.get(order.ticker)
            if price is None:
                continue

            exit_price = None
            exit_reason = ""

            if order.side == "buy":
                if price <= order.stop_loss:
                    exit_price, exit_reason = order.stop_loss, "SL"
                elif price >= order.take_profit:
                    exit_price, exit_reason = order.take_profit, "TP"
            else:  # sell / short
                if price >= order.stop_loss:
                    exit_price, exit_reason = order.stop_loss, "SL"
                elif price <= order.take_profit:
                    exit_price, exit_reason = order.take_profit, "TP"

            if exit_price is not None:
                self._close_order(order, exit_price, exit_reason)
                closed.append(order)

        if closed:
            self._save_state()
            logger.info(
                "Price update closed %d orders",
                len(closed),
            )
        return closed

    def close_order_manual(
        self, order_id: str, exit_price: float, reason: str = "manual",
    ) -> Optional[PaperOrder]:
        """Manually close an open order."""
        for order in self.orders:
            if order.order_id == order_id and order.status == "open":
                self._close_order(order, exit_price, reason)
                self._save_state()
                return order
        return None

    def get_open_orders(self) -> List[PaperOrder]:
        return [o for o in self.orders if o.status == "open"]

    def get_closed_orders(self, limit: int = 50) -> List[PaperOrder]:
        closed = [o for o in self.orders if o.status == "closed"]
        return closed[-limit:]

    def get_portfolio_summary(
        self, market_prices: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Return current portfolio state."""
        open_orders = self.get_open_orders()
        unrealized = 0.0

        if market_prices:
            for order in open_orders:
                price = market_prices.get(order.ticker, order.entry_price)
                if order.side == "buy":
                    unrealized += order.quantity * (price - order.entry_price)
                else:
                    unrealized += order.quantity * (order.entry_price - price)

        nav = self.cash + unrealized
        # Add back the cost of open buy positions
        for order in open_orders:
            if order.side == "buy":
                nav += order.quantity * order.entry_price

        return {
            "initial_capital": self._initial_capital,
            "cash": round(self.cash, 2),
            "nav": round(nav, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_realized_pnl": round(self.total_realized_pnl, 2),
            "total_return_pct": round(
                (nav - self._initial_capital) / self._initial_capital * 100, 2
            ),
            "open_positions": len(open_orders),
            "total_trades": len(self.orders),
            "open_orders": [o.to_dict() for o in open_orders],
            "recent_closed": [o.to_dict() for o in self.get_closed_orders(10)],
        }

    def take_snapshot(self, market_prices: Dict[str, float]) -> DailySnapshot:
        """Record an end-of-day portfolio snapshot."""
        summary = self.get_portfolio_summary(market_prices)
        today_closed = [
            o for o in self.orders
            if o.status == "closed"
            and o.closed_at
            and o.closed_at[:10] == datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ]
        realized_today = sum(o.pnl for o in today_closed)

        snap = DailySnapshot(
            date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            nav=summary["nav"],
            cash=summary["cash"],
            unrealized_pnl=summary["unrealized_pnl"],
            realized_pnl_today=round(realized_today, 2),
            open_positions=summary["open_positions"],
        )
        self.snapshots.append(snap)
        self._save_state()
        return snap

    def reset(self) -> None:
        """Reset portfolio to initial state (destructive)."""
        self.cash = self._initial_capital
        self.orders = []
        self.snapshots = []
        self.total_realized_pnl = 0.0
        self._order_counter = 0
        self._save_state()
        logger.info("Paper portfolio reset to ₹%.0f", self._initial_capital)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _close_order(
        self, order: PaperOrder, exit_price: float, reason: str,
    ) -> None:
        if order.side == "buy":
            pnl = order.quantity * (exit_price - order.entry_price)
            self.cash += order.quantity * exit_price
        else:
            pnl = order.quantity * (order.entry_price - exit_price)
            self.cash += pnl  # return profit/loss to cash

        order.status = "closed"
        order.exit_price = exit_price
        order.exit_reason = reason
        order.pnl = round(pnl, 2)
        order.closed_at = datetime.now(timezone.utc).isoformat()
        self.total_realized_pnl += pnl

        logger.info(
            "Closed %s: %s %s P&L=₹%.2f (%s)",
            order.order_id, order.ticker, reason, pnl,
            "WIN" if pnl > 0 else "LOSS",
            extra={"trade_id": order.order_id, "ticker": order.ticker},
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _save_state(self) -> None:
        try:
            self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "cash": self.cash,
                "total_realized_pnl": self.total_realized_pnl,
                "order_counter": self._order_counter,
                "orders": [o.to_dict() for o in self.orders],
                "snapshots": [asdict(s) for s in self.snapshots],
            }
            with open(self._persistence_path, "w") as fh:
                json.dump(state, fh, indent=2, default=str)
        except Exception:
            logger.exception("Failed to persist paper trading state")

    def _restore_state(self) -> None:
        if not self._persistence_path.exists():
            logger.info("No previous paper trading state found — starting fresh")
            return
        try:
            with open(self._persistence_path) as fh:
                state = json.load(fh)

            self.cash = state.get("cash", self._initial_capital)
            self.total_realized_pnl = state.get("total_realized_pnl", 0.0)
            self._order_counter = state.get("order_counter", 0)

            self.orders = [PaperOrder(**o) for o in state.get("orders", [])]
            self.snapshots = [DailySnapshot(**s) for s in state.get("snapshots", [])]

            logger.info(
                "Restored paper trading state: cash=₹%.0f, %d orders",
                self.cash, len(self.orders),
            )
        except Exception:
            logger.exception("Failed to restore paper trading state — starting fresh")
