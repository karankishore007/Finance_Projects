"""
Legacy Models — Rule-based technical analysis models that form the
"Legacy Council" of the meta-learner.

Each model implements a ``score()`` classmethod returning a float in
[-1, +1]:  -1 = strong BUY, +1 = strong SELL, 0 = neutral.

All models also return a structured ``ModelSignal`` for audit logging.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from backend.config import get_config, setup_logging

logger = setup_logging("agent.legacy_models")
cfg = get_config()


# ---------------------------------------------------------------------------
# Shared data structures
# ---------------------------------------------------------------------------
@dataclass
class ModelSignal:
    """Standardised output from every model in the council."""

    model_name: str
    score: float            # [-1, 1]  negative = buy, positive = sell
    confidence: float       # [0, 1]   absolute strength
    direction: str          # "buy" | "sell" | "neutral"
    reasoning: str
    metadata: Dict = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "model_name": self.model_name,
            "score": round(self.score, 4),
            "confidence": round(self.confidence, 4),
            "direction": self.direction,
            "reasoning": self.reasoning,
            "metadata": self.metadata,
            "latency_ms": round(self.latency_ms, 2),
        }


def _direction_from_score(score: float, threshold: float = 0.1) -> str:
    if score < -threshold:
        return "buy"
    if score > threshold:
        return "sell"
    return "neutral"


# ============================================================================
# 1. Mean Reversion Model
# ============================================================================
class MeanReversionModel:
    """Percentile-rank based mean-reversion signal.

    Logic:
        - Compute the percentile rank of the current close price within
          a rolling lookback window.
        - Oversold (low percentile) => BUY signal (negative score).
        - Overbought (high percentile) => SELL signal (positive score).

    The score is linearly mapped from the percentile rank:
        score = 2 * percentile - 1   (maps [0,1] -> [-1,+1])
    Then inverted because LOW percentile = buy = negative score.
    """

    NAME = "mean_reversion"

    @classmethod
    def score(cls, close_prices: np.ndarray) -> ModelSignal:
        """Compute mean-reversion signal.

        Args:
            close_prices: 1-D array of historical close prices, oldest first.

        Returns:
            ModelSignal with score in [-1, 1].
        """
        start = time.perf_counter()
        lookback = cfg.get("models.legacy.mean_reversion.lookback_period", 252)
        oversold = cfg.get("models.legacy.mean_reversion.oversold_threshold", 0.2)
        overbought = cfg.get("models.legacy.mean_reversion.overbought_threshold", 0.8)

        try:
            if len(close_prices) < lookback:
                lookback = len(close_prices)
            if lookback < 20:
                return cls._neutral("Insufficient data for mean reversion",
                                    time.perf_counter() - start)

            window = close_prices[-lookback:]
            current = close_prices[-1]
            window_min = np.nanmin(window)
            window_max = np.nanmax(window)

            if window_max == window_min:
                return cls._neutral("No price variance in window",
                                    time.perf_counter() - start)

            percentile = (current - window_min) / (window_max - window_min)

            # Invert: low percentile => negative score (buy)
            raw_score = 2.0 * percentile - 1.0

            # Build reasoning
            if percentile <= oversold:
                reasoning = (
                    f"Price at {percentile:.0%} percentile of {lookback}-day range "
                    f"— deeply oversold, mean-reversion buy signal."
                )
            elif percentile >= overbought:
                reasoning = (
                    f"Price at {percentile:.0%} percentile of {lookback}-day range "
                    f"— overbought, mean-reversion sell signal."
                )
            else:
                reasoning = (
                    f"Price at {percentile:.0%} percentile of {lookback}-day range "
                    f"— within normal range."
                )

            elapsed = (time.perf_counter() - start) * 1000
            signal = ModelSignal(
                model_name=cls.NAME,
                score=float(np.clip(raw_score, -1.0, 1.0)),
                confidence=float(abs(raw_score)),
                direction=_direction_from_score(raw_score),
                reasoning=reasoning,
                metadata={
                    "percentile": round(float(percentile), 4),
                    "lookback": lookback,
                    "window_min": round(float(window_min), 2),
                    "window_max": round(float(window_max), 2),
                    "current_price": round(float(current), 2),
                },
                latency_ms=elapsed,
            )
            logger.debug(
                "MeanReversion score=%.3f confidence=%.3f",
                signal.score, signal.confidence,
                extra={"model": cls.NAME, "signal": signal.direction},
            )
            return signal

        except Exception:
            logger.exception("MeanReversion model error")
            return cls._neutral("Model error", (time.perf_counter() - start) * 1000)

    @classmethod
    def _neutral(cls, reason: str, elapsed_ms: float) -> ModelSignal:
        return ModelSignal(
            model_name=cls.NAME, score=0.0, confidence=0.0,
            direction="neutral", reasoning=reason, latency_ms=elapsed_ms,
        )


# ============================================================================
# 2. Bollinger Bands Model
# ============================================================================
class BollingerBandsModel:
    """Bollinger Band breakout / reversion signal.

    Logic:
        - Compute 20-day SMA and upper/lower bands (SMA +/- 2*sigma).
        - Price near lower band => BUY (negative score).
        - Price near upper band => SELL (positive score).
        - Band squeeze (narrow bandwidth) amplifies the signal (breakout
          expected).
    """

    NAME = "bollinger_bands"

    @classmethod
    def score(cls, close_prices: np.ndarray) -> ModelSignal:
        start = time.perf_counter()
        lookback = cfg.get("models.legacy.bollinger_bands.lookback_period", 20)
        std_mult = cfg.get("models.legacy.bollinger_bands.std_dev_multiplier", 2.0)
        squeeze_thr = cfg.get("models.legacy.bollinger_bands.squeeze_threshold", 0.05)

        try:
            if len(close_prices) < lookback + 5:
                return cls._neutral("Insufficient data for Bollinger Bands",
                                    (time.perf_counter() - start) * 1000)

            window = close_prices[-lookback:]
            sma = float(np.nanmean(window))
            std = float(np.nanstd(window, ddof=1))

            if std == 0 or sma == 0:
                return cls._neutral("Zero variance in window",
                                    (time.perf_counter() - start) * 1000)

            upper = sma + std_mult * std
            lower = sma - std_mult * std
            bandwidth = (upper - lower) / sma  # relative bandwidth
            current = float(close_prices[-1])

            # Position within bands:  0 = lower band, 1 = upper band
            band_range = upper - lower
            if band_range == 0:
                band_position = 0.5
            else:
                band_position = (current - lower) / band_range

            # Map to [-1, 1]:  0->-1 (lower band, buy), 1->+1 (upper band, sell)
            raw_score = 2.0 * np.clip(band_position, 0, 1) - 1.0

            # Squeeze amplification: narrow bands => stronger conviction
            squeeze_factor = 1.0
            if bandwidth < squeeze_thr:
                squeeze_factor = 1.3
                squeeze_note = " Band squeeze detected — breakout likely."
            else:
                squeeze_note = ""

            adjusted_score = float(np.clip(raw_score * squeeze_factor, -1.0, 1.0))

            if adjusted_score < -0.5:
                reasoning = f"Price near lower Bollinger Band (₹{lower:.0f}).{squeeze_note}"
            elif adjusted_score > 0.5:
                reasoning = f"Price near upper Bollinger Band (₹{upper:.0f}).{squeeze_note}"
            else:
                reasoning = f"Price within Bollinger Bands (mid ₹{sma:.0f}).{squeeze_note}"

            elapsed = (time.perf_counter() - start) * 1000
            signal = ModelSignal(
                model_name=cls.NAME,
                score=adjusted_score,
                confidence=float(abs(adjusted_score)),
                direction=_direction_from_score(adjusted_score),
                reasoning=reasoning,
                metadata={
                    "sma": round(sma, 2),
                    "upper_band": round(upper, 2),
                    "lower_band": round(lower, 2),
                    "bandwidth": round(bandwidth, 4),
                    "band_position": round(float(band_position), 4),
                    "is_squeeze": bandwidth < squeeze_thr,
                },
                latency_ms=elapsed,
            )
            logger.debug(
                "BollingerBands score=%.3f bandwidth=%.4f",
                signal.score, bandwidth,
                extra={"model": cls.NAME},
            )
            return signal

        except Exception:
            logger.exception("BollingerBands model error")
            return cls._neutral("Model error", (time.perf_counter() - start) * 1000)

    @classmethod
    def _neutral(cls, reason: str, elapsed_ms: float) -> ModelSignal:
        return ModelSignal(
            model_name=cls.NAME, score=0.0, confidence=0.0,
            direction="neutral", reasoning=reason, latency_ms=elapsed_ms,
        )


# ============================================================================
# 3. Fibonacci Retracement Model
# ============================================================================
class FibonacciModel:
    """Fibonacci retracement level proximity signal.

    Logic:
        - Identify swing high / swing low over the lookback window.
        - Calculate standard Fibonacci levels (23.6%, 38.2%, 50%, 61.8%, 78.6%).
        - Score based on proximity to a Fib level:
            * Price near a support Fib level in an uptrend => BUY.
            * Price near a resistance Fib level in a downtrend => SELL.
    """

    NAME = "fibonacci"

    @classmethod
    def score(cls, ohlcv_df: pd.DataFrame) -> ModelSignal:
        """Compute Fibonacci retracement signal.

        Args:
            ohlcv_df: DataFrame with columns ``High``, ``Low``, ``Close``
                      (DatetimeIndex, oldest first).
        """
        start = time.perf_counter()
        lookback = cfg.get("models.legacy.fibonacci.swing_lookback", 126)
        proximity_thr = cfg.get("models.legacy.fibonacci.proximity_threshold", 0.02)
        fib_ratios: List[float] = cfg.get(
            "models.legacy.fibonacci.levels",
            [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0],
        )

        try:
            if len(ohlcv_df) < 50:
                return cls._neutral("Insufficient data for Fibonacci",
                                    (time.perf_counter() - start) * 1000)

            window = ohlcv_df.iloc[-lookback:] if len(ohlcv_df) >= lookback else ohlcv_df

            swing_high = float(window["High"].max())
            swing_low = float(window["Low"].min())
            current = float(ohlcv_df["Close"].iloc[-1])
            swing_range = swing_high - swing_low

            if swing_range == 0:
                return cls._neutral("No swing range",
                                    (time.perf_counter() - start) * 1000)

            # Compute fib levels (retracement from swing high)
            fib_levels = {
                f"fib_{r:.1%}": swing_high - swing_range * r for r in fib_ratios
            }

            # Find nearest Fib level
            distances = {
                name: abs(current - level) / current
                for name, level in fib_levels.items()
            }
            nearest_name = min(distances, key=distances.get)  # type: ignore
            nearest_dist = distances[nearest_name]
            nearest_level = fib_levels[nearest_name]

            # Determine trend direction (simple: is price above midpoint?)
            midpoint = (swing_high + swing_low) / 2.0
            is_uptrend = current > midpoint

            # Score calculation:
            # If near a Fib level, signal is stronger.
            # Direction depends on trend + whether level is support or resistance.
            if nearest_dist <= proximity_thr:
                # Near a Fib level
                if is_uptrend and current >= nearest_level:
                    # Price holding above support in uptrend => buy
                    raw_score = -0.7 - (0.3 * (1 - nearest_dist / proximity_thr))
                elif not is_uptrend and current <= nearest_level:
                    # Price rejected at resistance in downtrend => sell
                    raw_score = 0.7 + (0.3 * (1 - nearest_dist / proximity_thr))
                else:
                    raw_score = -0.3 if is_uptrend else 0.3
                reasoning = (
                    f"Price near {nearest_name} level (₹{nearest_level:.0f}), "
                    f"{'uptrend support' if is_uptrend else 'downtrend resistance'}."
                )
            else:
                # Not near any Fib level — weak signal
                position_in_range = (current - swing_low) / swing_range
                raw_score = 2.0 * position_in_range - 1.0
                raw_score *= 0.3  # dampen because not at a key level
                reasoning = (
                    f"Between Fib levels. Nearest: {nearest_name} "
                    f"(₹{nearest_level:.0f}, {nearest_dist:.1%} away)."
                )

            raw_score = float(np.clip(raw_score, -1.0, 1.0))
            elapsed = (time.perf_counter() - start) * 1000

            signal = ModelSignal(
                model_name=cls.NAME,
                score=raw_score,
                confidence=float(abs(raw_score)),
                direction=_direction_from_score(raw_score),
                reasoning=reasoning,
                metadata={
                    "swing_high": round(swing_high, 2),
                    "swing_low": round(swing_low, 2),
                    "nearest_fib": nearest_name,
                    "nearest_level": round(nearest_level, 2),
                    "distance_pct": round(float(nearest_dist), 4),
                    "is_uptrend": is_uptrend,
                    "fib_levels": {k: round(v, 2) for k, v in fib_levels.items()},
                },
                latency_ms=elapsed,
            )
            logger.debug(
                "Fibonacci score=%.3f nearest=%s dist=%.3f%%",
                signal.score, nearest_name, nearest_dist * 100,
                extra={"model": cls.NAME},
            )
            return signal

        except Exception:
            logger.exception("Fibonacci model error")
            return cls._neutral("Model error", (time.perf_counter() - start) * 1000)

    @classmethod
    def _neutral(cls, reason: str, elapsed_ms: float) -> ModelSignal:
        return ModelSignal(
            model_name=cls.NAME, score=0.0, confidence=0.0,
            direction="neutral", reasoning=reason, latency_ms=elapsed_ms,
        )


# ============================================================================
# Convenience: run all legacy models at once
# ============================================================================
def run_legacy_council(ohlcv_df: pd.DataFrame) -> List[ModelSignal]:
    """Execute all three legacy models and return their signals.

    Args:
        ohlcv_df: DataFrame with at minimum ``Close``, ``High``, ``Low``
                  columns and a DatetimeIndex.

    Returns:
        List of three ``ModelSignal`` objects.
    """
    close = ohlcv_df["Close"].values.astype(float)

    signals = [
        MeanReversionModel.score(close),
        BollingerBandsModel.score(close),
        FibonacciModel.score(ohlcv_df),
    ]

    total_ms = sum(s.latency_ms for s in signals)
    logger.info(
        "Legacy council complete: scores=[%.2f, %.2f, %.2f] total_ms=%.1f",
        signals[0].score, signals[1].score, signals[2].score, total_ms,
    )
    return signals
