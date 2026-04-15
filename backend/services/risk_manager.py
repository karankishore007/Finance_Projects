"""
Risk Manager — Enforces capital-preservation constraints before any
paper or live trade is executed.

Rules (all config-driven):
  - Max 5% of portfolio in a single position.
  - Max 3% daily portfolio loss.
  - Max 15% total drawdown from peak NAV.
  - Max 5 concurrent open positions.
  - Min 20% cash reserve at all times.
  - Risk-based position sizing (Kelly-lite: 2% per trade).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from backend.config import get_config, setup_logging

logger = setup_logging("agent.risk_manager")
cfg = get_config()


@dataclass
class RiskCheckResult:
    """Outcome of a pre-trade risk validation."""

    approved: bool
    reason: str
    suggested_quantity: int
    risk_metrics: Dict


class RiskManager:
    """Stateless validator — takes portfolio state and proposed trade,
    returns approval / rejection with reasoning."""

    def __init__(self) -> None:
        self._max_position_pct: float = cfg.get("risk.max_position_pct", 0.05)
        self._max_daily_loss_pct: float = cfg.get("risk.max_daily_loss_pct", 0.03)
        self._max_drawdown_pct: float = cfg.get("risk.max_total_drawdown_pct", 0.15)
        self._max_open_positions: int = cfg.get("risk.max_open_positions", 5)
        self._min_cash_reserve_pct: float = cfg.get("risk.min_cash_reserve_pct", 0.20)
        self._risk_per_trade_pct: float = cfg.get("risk.risk_per_trade_pct", 0.02)

    def validate_trade(
        self,
        ticker: str,
        side: str,
        quantity: int,
        entry_price: float,
        stop_loss: float,
        portfolio_nav: float,
        cash: float,
        open_position_count: int,
        peak_nav: float,
        daily_pnl: float = 0.0,
    ) -> RiskCheckResult:
        """Run all risk checks against a proposed trade.

        Args:
            ticker:              Symbol to trade.
            side:                ``"buy"`` or ``"sell"``.
            quantity:            Desired position size.
            entry_price:         Expected fill price.
            stop_loss:           SL level.
            portfolio_nav:       Current total portfolio value.
            cash:                Available cash.
            open_position_count: Number of currently open positions.
            peak_nav:            Highest NAV ever recorded.
            daily_pnl:           Today's cumulative realized + unrealized P&L.

        Returns:
            RiskCheckResult with approval status and adjusted quantity.
        """
        trade_value = quantity * entry_price
        reasons = []
        suggested_qty = quantity

        # --- Check 1: Max position size ---
        max_trade_value = portfolio_nav * self._max_position_pct
        if trade_value > max_trade_value:
            suggested_qty = max(1, int(max_trade_value / entry_price))
            reasons.append(
                f"Position ₹{trade_value:,.0f} exceeds "
                f"{self._max_position_pct:.0%} limit (₹{max_trade_value:,.0f}). "
                f"Reduced to {suggested_qty} shares."
            )

        # --- Check 2: Cash reserve ---
        min_cash = portfolio_nav * self._min_cash_reserve_pct
        available = cash - min_cash
        if side == "buy" and (suggested_qty * entry_price) > available:
            if available > entry_price:
                suggested_qty = int(available / entry_price)
                reasons.append(
                    f"Cash reserve constraint: adjusted to {suggested_qty} shares "
                    f"(keeping {self._min_cash_reserve_pct:.0%} reserve)."
                )
            else:
                return RiskCheckResult(
                    approved=False,
                    reason=f"Insufficient cash after {self._min_cash_reserve_pct:.0%} reserve.",
                    suggested_quantity=0,
                    risk_metrics=self._metrics(portfolio_nav, peak_nav, daily_pnl,
                                               open_position_count),
                )

        # --- Check 3: Max open positions ---
        if open_position_count >= self._max_open_positions:
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"Max {self._max_open_positions} open positions reached. "
                    "Close an existing position first."
                ),
                suggested_quantity=0,
                risk_metrics=self._metrics(portfolio_nav, peak_nav, daily_pnl,
                                           open_position_count),
            )

        # --- Check 4: Daily loss limit ---
        if daily_pnl < -(portfolio_nav * self._max_daily_loss_pct):
            return RiskCheckResult(
                approved=False,
                reason=(
                    f"Daily loss limit hit ({self._max_daily_loss_pct:.0%}). "
                    "Trading halted for the day."
                ),
                suggested_quantity=0,
                risk_metrics=self._metrics(portfolio_nav, peak_nav, daily_pnl,
                                           open_position_count),
            )

        # --- Check 5: Max drawdown ---
        if peak_nav > 0:
            current_dd = (peak_nav - portfolio_nav) / peak_nav
            if current_dd > self._max_drawdown_pct:
                return RiskCheckResult(
                    approved=False,
                    reason=(
                        f"Portfolio drawdown {current_dd:.1%} exceeds "
                        f"{self._max_drawdown_pct:.0%} limit. "
                        "All new trades blocked until recovery."
                    ),
                    suggested_quantity=0,
                    risk_metrics=self._metrics(portfolio_nav, peak_nav, daily_pnl,
                                               open_position_count),
                )

        # --- Passed all checks ---
        if suggested_qty <= 0:
            return RiskCheckResult(
                approved=False,
                reason="Quantity reduced to zero by risk constraints.",
                suggested_quantity=0,
                risk_metrics=self._metrics(portfolio_nav, peak_nav, daily_pnl,
                                           open_position_count),
            )

        combined_reason = " | ".join(reasons) if reasons else "All risk checks passed."
        approved = suggested_qty > 0

        logger.info(
            "Risk check for %s %s %d x %s: %s (qty=%d)",
            side.upper(), ticker, quantity, ticker,
            "APPROVED" if approved else "REJECTED",
            suggested_qty,
            extra={"ticker": ticker},
        )

        return RiskCheckResult(
            approved=approved,
            reason=combined_reason,
            suggested_quantity=suggested_qty,
            risk_metrics=self._metrics(portfolio_nav, peak_nav, daily_pnl,
                                       open_position_count),
        )

    def optimal_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        portfolio_nav: float,
    ) -> int:
        """Calculate the optimal position size using risk-per-trade sizing.

        Formula:
            quantity = (portfolio_nav * risk_pct) / |entry - stop_loss|

        Also respects the max_position_pct ceiling.
        """
        price_risk = abs(entry_price - stop_loss)
        if price_risk <= 0:
            return 0

        risk_amount = portfolio_nav * self._risk_per_trade_pct
        qty_by_risk = int(risk_amount / price_risk)

        max_by_pct = int((portfolio_nav * self._max_position_pct) / entry_price)
        return max(1, min(qty_by_risk, max_by_pct))

    def _metrics(
        self,
        nav: float,
        peak_nav: float,
        daily_pnl: float,
        open_count: int,
    ) -> Dict:
        dd = (peak_nav - nav) / peak_nav if peak_nav > 0 else 0.0
        return {
            "current_nav": round(nav, 2),
            "peak_nav": round(peak_nav, 2),
            "drawdown_pct": round(dd, 4),
            "daily_pnl": round(daily_pnl, 2),
            "open_positions": open_count,
            "max_position_pct": self._max_position_pct,
            "max_drawdown_pct": self._max_drawdown_pct,
        }
