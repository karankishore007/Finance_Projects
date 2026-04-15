"""
Meta-Learner Decision Engine — Synthesises signals from the Legacy and
SOTA councils into a unified trade decision.

A signal is only emitted when at least one Legacy model AND one SOTA model
agree on direction with confidence >= the alignment threshold (default 75%).

Output includes: signal direction, confidence, entry price, stop-loss,
take-profit, and a human-readable reasoning chain.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from backend.config import get_config, setup_logging
from backend.services.legacy_models import ModelSignal, run_legacy_council
from backend.services.sota_models import run_sota_council

logger = setup_logging("agent.meta_learner")
cfg = get_config()


# ---------------------------------------------------------------------------
# Trade Decision output
# ---------------------------------------------------------------------------
@dataclass
class TradeDecision:
    """Final output of the meta-learner for a single ticker evaluation."""

    ticker: str
    signal: int               # -1 = BUY, 0 = HOLD, +1 = SELL
    signal_label: str         # "BUY" | "HOLD" | "SELL"
    confidence: float         # [0, 1]
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    reasoning: str = ""
    legacy_signals: List[Dict] = field(default_factory=list)
    sota_signals: List[Dict] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    timestamp: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "ticker": self.ticker,
            "signal": self.signal,
            "signal_label": self.signal_label,
            "confidence": round(self.confidence, 4),
            "entry_price": round(self.entry_price, 2) if self.entry_price else None,
            "stop_loss": round(self.stop_loss, 2) if self.stop_loss else None,
            "take_profit": round(self.take_profit, 2) if self.take_profit else None,
            "reasoning": self.reasoning,
            "legacy_signals": self.legacy_signals,
            "sota_signals": self.sota_signals,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "latency_ms": round(self.latency_ms, 2),
        }


# ============================================================================
# ATR Calculator
# ============================================================================
def calculate_atr(ohlcv_df: pd.DataFrame, period: int = 14) -> float:
    """Compute the Average True Range for dynamic SL/TP sizing.

    Args:
        ohlcv_df: DataFrame with ``High``, ``Low``, ``Close`` columns.
        period:   Rolling window length.

    Returns:
        The most recent ATR value as a float.
    """
    high = ohlcv_df["High"].astype(float)
    low = ohlcv_df["Low"].astype(float)
    close = ohlcv_df["Close"].astype(float)

    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.rolling(window=period, min_periods=1).mean()
    return float(atr.iloc[-1])


# ============================================================================
# Meta-Learner
# ============================================================================
class MetaLearner:
    """Council-of-models decision engine.

    The decide() method:
    1. Runs the Legacy Council (3 models).
    2. Runs the SOTA Council (2 models).
    3. Computes alignment between councils.
    4. If alignment >= threshold, emits a trade signal with ATR-based SL/TP.
    5. Otherwise, emits a HOLD.
    """

    def __init__(self) -> None:
        self._alignment_thr: float = cfg.get("meta_learner.alignment_threshold", 0.75)
        self._legacy_weight: float = cfg.get("meta_learner.legacy_council_weight", 0.4)
        self._sota_weight: float = cfg.get("meta_learner.sota_council_weight", 0.6)
        self._min_legacy: int = cfg.get("meta_learner.min_legacy_agreement", 1)
        self._min_sota: int = cfg.get("meta_learner.min_sota_agreement", 1)
        self._atr_period: int = cfg.get("meta_learner.atr_period", 14)
        self._sl_mult: float = cfg.get("meta_learner.sl_atr_multiplier", 2.0)
        self._tp_mult: float = cfg.get("meta_learner.tp_atr_multiplier", 3.0)

    # ------------------------------------------------------------------
    def decide(
        self,
        ticker: str,
        ohlcv_df: pd.DataFrame,
        headlines: List[str],
    ) -> TradeDecision:
        """Run the full meta-learner pipeline.

        Args:
            ticker:    NSE ticker symbol (e.g. ``"TCS.NS"``).
            ohlcv_df:  DataFrame with OHLCV columns and DatetimeIndex.
            headlines: List of recent news headline strings.

        Returns:
            A ``TradeDecision`` with the synthesized signal.
        """
        overall_start = time.perf_counter()
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).isoformat()

        # --- Step 1: Run both councils ---
        legacy_signals = run_legacy_council(ohlcv_df)
        close_prices = ohlcv_df["Close"].values.astype(float)
        sota_signals = run_sota_council(close_prices, headlines)

        # --- Step 2: Compute per-council consensus ---
        legacy_scores = np.array([s.score for s in legacy_signals])
        sota_scores = np.array([s.score for s in sota_signals])

        legacy_consensus = float(np.mean(legacy_scores))
        sota_consensus = float(np.mean(sota_scores))

        # --- Step 3: Check directional agreement within each council ---
        legacy_buy_count = int(np.sum(legacy_scores < -0.1))
        legacy_sell_count = int(np.sum(legacy_scores > 0.1))
        sota_buy_count = int(np.sum(sota_scores < -0.1))
        sota_sell_count = int(np.sum(sota_scores > 0.1))

        # --- Step 4: Cross-council alignment ---
        same_direction = (legacy_consensus * sota_consensus) > 0
        legacy_confidence = abs(legacy_consensus)
        sota_confidence = abs(sota_consensus)

        # Weighted confidence
        blended_confidence = (
            self._legacy_weight * legacy_confidence
            + self._sota_weight * sota_confidence
        )

        # Determine if we have sufficient agreement
        is_buy = (
            same_direction
            and legacy_consensus < 0
            and legacy_buy_count >= self._min_legacy
            and sota_buy_count >= self._min_sota
            and blended_confidence >= self._alignment_thr
        )
        is_sell = (
            same_direction
            and legacy_consensus > 0
            and legacy_sell_count >= self._min_legacy
            and sota_sell_count >= self._min_sota
            and blended_confidence >= self._alignment_thr
        )

        # --- Step 5: Generate signal ---
        current_price = float(ohlcv_df["Close"].iloc[-1])
        atr = calculate_atr(ohlcv_df, self._atr_period)

        if is_buy:
            signal = -1
            signal_label = "BUY"
            entry = current_price
            sl = entry - self._sl_mult * atr
            tp = entry + self._tp_mult * atr
        elif is_sell:
            signal = 1
            signal_label = "SELL"
            entry = current_price
            sl = entry + self._sl_mult * atr
            tp = entry - self._tp_mult * atr
        else:
            signal = 0
            signal_label = "HOLD"
            entry = sl = tp = None

        # --- Step 6: Build reasoning ---
        reasons: List[str] = []
        for s in legacy_signals:
            if abs(s.score) > 0.3:
                reasons.append(f"{s.model_name}: {s.reasoning}")
        for s in sota_signals:
            if abs(s.score) > 0.3:
                reasons.append(f"{s.model_name}: {s.reasoning}")

        if same_direction:
            reasons.append(
                f"Councils aligned ({blended_confidence:.0%} confidence)."
            )
        else:
            reasons.append(
                "Councils diverge — holding until consensus forms."
            )

        elapsed = (time.perf_counter() - overall_start) * 1000

        decision = TradeDecision(
            ticker=ticker,
            signal=signal,
            signal_label=signal_label,
            confidence=blended_confidence,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            reasoning=" | ".join(reasons),
            legacy_signals=[s.to_dict() for s in legacy_signals],
            sota_signals=[s.to_dict() for s in sota_signals],
            metadata={
                "legacy_consensus": round(legacy_consensus, 4),
                "sota_consensus": round(sota_consensus, 4),
                "blended_confidence": round(blended_confidence, 4),
                "same_direction": same_direction,
                "atr": round(atr, 2),
                "legacy_buy_count": legacy_buy_count,
                "legacy_sell_count": legacy_sell_count,
                "sota_buy_count": sota_buy_count,
                "sota_sell_count": sota_sell_count,
            },
            timestamp=timestamp,
            latency_ms=elapsed,
        )

        logger.info(
            "MetaLearner decision: %s %s conf=%.2f price=%.2f elapsed=%.0fms",
            ticker, signal_label, blended_confidence, current_price, elapsed,
            extra={
                "ticker": ticker,
                "signal": signal_label,
                "confidence": round(blended_confidence, 4),
                "latency_ms": round(elapsed, 1),
            },
        )

        return decision

    # ------------------------------------------------------------------
    def decide_from_raw(
        self,
        ticker: str,
        ohlcv_df: pd.DataFrame,
        headlines: List[str],
        legacy_signals: List[ModelSignal],
        sota_signals: List[ModelSignal],
    ) -> TradeDecision:
        """Variant of decide() that accepts pre-computed model signals.

        Useful for backtesting where models are called separately.
        """
        overall_start = time.perf_counter()
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).isoformat()

        legacy_scores = np.array([s.score for s in legacy_signals])
        sota_scores = np.array([s.score for s in sota_signals])

        legacy_consensus = float(np.mean(legacy_scores))
        sota_consensus = float(np.mean(sota_scores))

        legacy_buy_count = int(np.sum(legacy_scores < -0.1))
        legacy_sell_count = int(np.sum(legacy_scores > 0.1))
        sota_buy_count = int(np.sum(sota_scores < -0.1))
        sota_sell_count = int(np.sum(sota_scores > 0.1))

        same_direction = (legacy_consensus * sota_consensus) > 0
        legacy_confidence = abs(legacy_consensus)
        sota_confidence = abs(sota_consensus)
        blended_confidence = (
            self._legacy_weight * legacy_confidence
            + self._sota_weight * sota_confidence
        )

        is_buy = (
            same_direction
            and legacy_consensus < 0
            and legacy_buy_count >= self._min_legacy
            and sota_buy_count >= self._min_sota
            and blended_confidence >= self._alignment_thr
        )
        is_sell = (
            same_direction
            and legacy_consensus > 0
            and legacy_sell_count >= self._min_legacy
            and sota_sell_count >= self._min_sota
            and blended_confidence >= self._alignment_thr
        )

        current_price = float(ohlcv_df["Close"].iloc[-1])
        atr = calculate_atr(ohlcv_df, self._atr_period)

        if is_buy:
            signal, signal_label = -1, "BUY"
            entry, sl, tp = current_price, current_price - self._sl_mult * atr, current_price + self._tp_mult * atr
        elif is_sell:
            signal, signal_label = 1, "SELL"
            entry, sl, tp = current_price, current_price + self._sl_mult * atr, current_price - self._tp_mult * atr
        else:
            signal, signal_label = 0, "HOLD"
            entry = sl = tp = None

        elapsed = (time.perf_counter() - overall_start) * 1000

        return TradeDecision(
            ticker=ticker,
            signal=signal,
            signal_label=signal_label,
            confidence=blended_confidence,
            entry_price=entry,
            stop_loss=sl,
            take_profit=tp,
            reasoning="Pre-computed council signals",
            legacy_signals=[s.to_dict() for s in legacy_signals],
            sota_signals=[s.to_dict() for s in sota_signals],
            metadata={
                "legacy_consensus": round(legacy_consensus, 4),
                "sota_consensus": round(sota_consensus, 4),
                "blended_confidence": round(blended_confidence, 4),
                "same_direction": same_direction,
                "atr": round(atr, 2),
            },
            timestamp=timestamp,
            latency_ms=elapsed,
        )
