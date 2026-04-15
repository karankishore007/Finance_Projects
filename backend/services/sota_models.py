"""
SOTA Models — Machine-learning based models that form the "SOTA Council"
of the meta-learner.

1. TransformerForecastModel  — Time-series forecasting via Chronos
   (Amazon), with Prophet fallback.
2. LLMSentimentModel         — Financial headline sentiment via FinBERT,
   with VADER fallback.

Both models are **inference-only** (no GPU training required) and produce
a ``ModelSignal`` compatible with the legacy council.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from backend.config import get_config, setup_logging
from backend.services.legacy_models import ModelSignal, _direction_from_score

logger = setup_logging("agent.sota_models")
cfg = get_config()


# ============================================================================
# 1. Transformer-Based Time-Series Forecast
# ============================================================================
class TransformerForecastModel:
    """Generates a multi-day price forecast using a pre-trained transformer.

    Primary: Amazon Chronos (``chronos-t5-small``)
    Fallback: Facebook Prophet (already in the codebase).

    The score is derived from the predicted percentage change over the
    forecast horizon:
        pct_change = (forecast_mean[-1] - current) / current
        score = tanh(pct_change * scaling_factor)  # maps to [-1, 1]
    Positive forecast => negative score (buy).
    """

    NAME = "transformer_forecast"

    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None
        self._enabled: bool = cfg.get("models.sota.transformer.enabled", True)
        self._model_name: str = cfg.get(
            "models.sota.transformer.model_name", "amazon/chronos-t5-small"
        )
        self._context_len: int = cfg.get("models.sota.transformer.context_length", 512)
        self._horizon: int = cfg.get("models.sota.transformer.forecast_horizon", 5)
        self._fallback: bool = cfg.get("models.sota.transformer.fallback_to_prophet", True)
        self._loaded = False

    def _lazy_load(self) -> bool:
        """Attempt to load the Chronos model on first use."""
        if self._loaded:
            return self._model is not None
        self._loaded = True

        if not self._enabled:
            logger.info("Transformer model disabled in config")
            return False

        try:
            import torch
            from chronos import ChronosPipeline

            logger.info("Loading Chronos model: %s ...", self._model_name)
            start = time.perf_counter()
            self._model = ChronosPipeline.from_pretrained(
                self._model_name,
                device_map="cpu",
                torch_dtype=torch.float32,
            )
            elapsed = (time.perf_counter() - start) * 1000
            logger.info("Chronos loaded in %.0fms", elapsed)
            return True
        except ImportError:
            logger.warning(
                "chronos-forecasting not installed — will use Prophet fallback"
            )
            return False
        except Exception:
            logger.exception("Failed to load Chronos model")
            return False

    def score(self, close_prices: np.ndarray) -> ModelSignal:
        """Generate forecast-based signal.

        Args:
            close_prices: 1-D array of historical close prices.
        """
        start = time.perf_counter()

        if self._lazy_load() and self._model is not None:
            return self._score_chronos(close_prices, start)

        if self._fallback:
            return self._score_prophet(close_prices, start)

        return self._neutral(
            "Transformer model unavailable and fallback disabled",
            (time.perf_counter() - start) * 1000,
        )

    # ------------------------------------------------------------------
    def _score_chronos(self, close_prices: np.ndarray, start: float) -> ModelSignal:
        """Score using the loaded Chronos pipeline."""
        try:
            import torch

            context = close_prices[-self._context_len:]
            context_tensor = torch.tensor(context, dtype=torch.float32).unsqueeze(0)

            forecast = self._model.predict(
                context_tensor,
                self._horizon,
                num_samples=20,
            )
            # forecast shape: (1, num_samples, horizon)
            median_forecast = np.median(forecast[0].numpy(), axis=0)

            current = float(close_prices[-1])
            predicted = float(median_forecast[-1])
            pct_change = (predicted - current) / current

            # Negative pct_change => price going down => sell (positive score)
            # Positive pct_change => price going up => buy (negative score)
            raw_score = float(-np.tanh(pct_change * 8))  # dampened scaling

            elapsed = (time.perf_counter() - start) * 1000
            signal = ModelSignal(
                model_name=self.NAME,
                score=float(np.clip(raw_score, -1.0, 1.0)),
                confidence=float(min(abs(raw_score), 1.0)),
                direction=_direction_from_score(raw_score),
                reasoning=(
                    f"Chronos {self._horizon}-day forecast: "
                    f"₹{current:.0f} -> ₹{predicted:.0f} "
                    f"({pct_change:+.2%})."
                ),
                metadata={
                    "model": self._model_name,
                    "current_price": round(current, 2),
                    "predicted_price": round(predicted, 2),
                    "pct_change": round(pct_change, 4),
                    "forecast_horizon": self._horizon,
                    "median_forecast": [round(float(v), 2) for v in median_forecast],
                },
                latency_ms=elapsed,
            )
            logger.debug(
                "Chronos score=%.3f pct_change=%.4f",
                signal.score, pct_change,
                extra={"model": self.NAME},
            )
            return signal

        except Exception:
            logger.exception("Chronos inference failed, falling back to Prophet")
            if self._fallback:
                return self._score_prophet(close_prices, start)
            return self._neutral("Chronos error", (time.perf_counter() - start) * 1000)

    # ------------------------------------------------------------------
    def _score_prophet(self, close_prices: np.ndarray, start: float) -> ModelSignal:
        """Fallback: use Facebook Prophet for forecasting."""
        try:
            from prophet import Prophet
            import logging as _logging

            _logging.getLogger("prophet").setLevel(_logging.ERROR)
            _logging.getLogger("cmdstanpy").setLevel(_logging.ERROR)

            # Build dataframe for Prophet
            dates = pd.date_range(
                end=pd.Timestamp.now().normalize(),
                periods=len(close_prices),
                freq="B",
            )
            pdf = pd.DataFrame({"ds": dates, "y": close_prices})

            model = Prophet(
                daily_seasonality=False,
                yearly_seasonality=True,
                weekly_seasonality=True,
            )
            model.fit(pdf)
            future = model.make_future_dataframe(periods=self._horizon)
            forecast = model.predict(future)

            predicted_values = forecast["yhat"].iloc[-self._horizon:].values
            current = float(close_prices[-1])
            predicted = float(predicted_values[-1])
            pct_change = (predicted - current) / current

            raw_score = float(-np.tanh(pct_change * 50))

            elapsed = (time.perf_counter() - start) * 1000
            signal = ModelSignal(
                model_name=self.NAME,
                score=float(np.clip(raw_score, -1.0, 1.0)),
                confidence=float(min(abs(raw_score) * 0.8, 1.0)),  # slightly less confident
                direction=_direction_from_score(raw_score),
                reasoning=(
                    f"Prophet {self._horizon}-day forecast (fallback): "
                    f"₹{current:.0f} -> ₹{predicted:.0f} "
                    f"({pct_change:+.2%})."
                ),
                metadata={
                    "model": "prophet_fallback",
                    "current_price": round(current, 2),
                    "predicted_price": round(predicted, 2),
                    "pct_change": round(pct_change, 4),
                    "forecast_horizon": self._horizon,
                },
                latency_ms=elapsed,
            )
            logger.debug(
                "Prophet fallback score=%.3f pct_change=%.4f",
                signal.score, pct_change,
                extra={"model": self.NAME},
            )
            return signal

        except Exception:
            logger.exception("Prophet fallback also failed")
            return self._neutral(
                "Both Chronos and Prophet unavailable",
                (time.perf_counter() - start) * 1000,
            )

    @classmethod
    def _neutral(cls, reason: str, elapsed_ms: float) -> ModelSignal:
        return ModelSignal(
            model_name=cls.NAME, score=0.0, confidence=0.0,
            direction="neutral", reasoning=reason, latency_ms=elapsed_ms,
        )


# ============================================================================
# 2. LLM-Based Sentiment Analysis
# ============================================================================
class LLMSentimentModel:
    """Financial-domain sentiment analysis using FinBERT.

    Primary: ProsusAI/finbert (HuggingFace)
    Fallback: VADER (already in the codebase).

    Headlines are scored individually, then combined with exponential
    recency weighting (newer headlines matter more).
    """

    NAME = "llm_sentiment"

    def __init__(self) -> None:
        self._pipeline = None
        self._enabled: bool = cfg.get("models.sota.sentiment.enabled", True)
        self._model_name: str = cfg.get(
            "models.sota.sentiment.model_name", "ProsusAI/finbert"
        )
        self._max_headlines: int = cfg.get("models.sota.sentiment.max_headlines", 15)
        self._decay: float = cfg.get("models.sota.sentiment.recency_decay", 0.85)
        self._fallback: bool = cfg.get("models.sota.sentiment.fallback_to_vader", True)
        self._loaded = False

    def _lazy_load(self) -> bool:
        if self._loaded:
            return self._pipeline is not None
        self._loaded = True

        if not self._enabled:
            logger.info("LLM Sentiment model disabled in config")
            return False

        try:
            from transformers import pipeline as hf_pipeline

            logger.info("Loading FinBERT model: %s ...", self._model_name)
            start = time.perf_counter()
            self._pipeline = hf_pipeline(
                "sentiment-analysis",
                model=self._model_name,
                device=-1,  # CPU
                top_k=None,
            )
            elapsed = (time.perf_counter() - start) * 1000
            logger.info("FinBERT loaded in %.0fms", elapsed)
            return True
        except ImportError:
            logger.warning("transformers not installed — using VADER fallback")
            return False
        except Exception:
            logger.exception("Failed to load FinBERT")
            return False

    def score(self, headlines: List[str]) -> ModelSignal:
        """Compute aggregate sentiment from a list of headlines.

        Args:
            headlines: List of news headline strings (newest first).
        """
        start = time.perf_counter()

        if not headlines:
            return self._neutral(
                "No headlines provided",
                (time.perf_counter() - start) * 1000,
            )

        trimmed = headlines[: self._max_headlines]

        if self._lazy_load() and self._pipeline is not None:
            return self._score_finbert(trimmed, start)

        if self._fallback:
            return self._score_vader(trimmed, start)

        return self._neutral(
            "Sentiment model unavailable", (time.perf_counter() - start) * 1000
        )

    # ------------------------------------------------------------------
    def _score_finbert(self, headlines: List[str], start: float) -> ModelSignal:
        """Score headlines using FinBERT."""
        try:
            label_map = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
            scores: List[float] = []
            details: List[Dict] = []

            for headline in headlines:
                result = self._pipeline(headline[:512])  # truncate to model max
                if result and isinstance(result[0], list):
                    result = result[0]
                best = max(result, key=lambda x: x["score"])
                raw = label_map.get(best["label"], 0.0) * best["score"]
                scores.append(raw)
                details.append({
                    "headline": headline[:80],
                    "label": best["label"],
                    "confidence": round(best["score"], 3),
                    "weighted_score": round(raw, 3),
                })

            # Recency-weighted average
            weights = np.array([self._decay ** i for i in range(len(scores))])
            weights /= weights.sum()
            weighted_avg = float(np.dot(scores, weights))

            # Invert: positive sentiment => buy => negative score
            raw_score = -weighted_avg

            elapsed = (time.perf_counter() - start) * 1000
            signal = ModelSignal(
                model_name=self.NAME,
                score=float(np.clip(raw_score, -1.0, 1.0)),
                confidence=float(min(abs(raw_score), 1.0)),
                direction=_direction_from_score(raw_score),
                reasoning=(
                    f"FinBERT sentiment across {len(headlines)} headlines: "
                    f"{'bullish' if weighted_avg > 0.1 else 'bearish' if weighted_avg < -0.1 else 'mixed'}."
                ),
                metadata={
                    "model": self._model_name,
                    "headline_count": len(headlines),
                    "weighted_avg": round(weighted_avg, 4),
                    "top_headlines": details[:5],
                },
                latency_ms=elapsed,
            )
            logger.debug(
                "FinBERT score=%.3f avg_sentiment=%.3f n=%d",
                signal.score, weighted_avg, len(headlines),
                extra={"model": self.NAME},
            )
            return signal

        except Exception:
            logger.exception("FinBERT inference failed, falling back to VADER")
            if self._fallback:
                return self._score_vader(headlines, start)
            return self._neutral("FinBERT error", (time.perf_counter() - start) * 1000)

    # ------------------------------------------------------------------
    def _score_vader(self, headlines: List[str], start: float) -> ModelSignal:
        """Fallback: score headlines with VADER."""
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

            analyzer = SentimentIntensityAnalyzer()
            scores: List[float] = []

            for headline in headlines:
                compound = analyzer.polarity_scores(headline)["compound"]
                scores.append(compound)

            weights = np.array([self._decay ** i for i in range(len(scores))])
            weights /= weights.sum()
            weighted_avg = float(np.dot(scores, weights))

            raw_score = -weighted_avg  # positive sentiment => buy (negative score)

            elapsed = (time.perf_counter() - start) * 1000
            signal = ModelSignal(
                model_name=self.NAME,
                score=float(np.clip(raw_score, -1.0, 1.0)),
                confidence=float(min(abs(raw_score) * 0.7, 1.0)),  # VADER less confident
                direction=_direction_from_score(raw_score),
                reasoning=(
                    f"VADER sentiment (fallback) across {len(headlines)} headlines: "
                    f"{'bullish' if weighted_avg > 0.1 else 'bearish' if weighted_avg < -0.1 else 'mixed'}."
                ),
                metadata={
                    "model": "vader_fallback",
                    "headline_count": len(headlines),
                    "weighted_avg": round(weighted_avg, 4),
                },
                latency_ms=elapsed,
            )
            return signal

        except Exception:
            logger.exception("VADER fallback also failed")
            return self._neutral(
                "All sentiment models failed",
                (time.perf_counter() - start) * 1000,
            )

    @classmethod
    def _neutral(cls, reason: str, elapsed_ms: float) -> ModelSignal:
        return ModelSignal(
            model_name=cls.NAME, score=0.0, confidence=0.0,
            direction="neutral", reasoning=reason, latency_ms=elapsed_ms,
        )


# ============================================================================
# Convenience: run all SOTA models at once
# ============================================================================
# Singleton instances (models are lazily loaded on first call)
_transformer_model: Optional[TransformerForecastModel] = None
_sentiment_model: Optional[LLMSentimentModel] = None


def _get_transformer() -> TransformerForecastModel:
    global _transformer_model
    if _transformer_model is None:
        _transformer_model = TransformerForecastModel()
    return _transformer_model


def _get_sentiment() -> LLMSentimentModel:
    global _sentiment_model
    if _sentiment_model is None:
        _sentiment_model = LLMSentimentModel()
    return _sentiment_model


def run_sota_council(
    close_prices: np.ndarray,
    headlines: List[str],
) -> List[ModelSignal]:
    """Execute both SOTA models and return their signals.

    Args:
        close_prices: 1-D array of close prices for the transformer.
        headlines: List of news headlines for sentiment model.

    Returns:
        List of two ``ModelSignal`` objects.
    """
    signals = [
        _get_transformer().score(close_prices),
        _get_sentiment().score(headlines),
    ]

    total_ms = sum(s.latency_ms for s in signals)
    logger.info(
        "SOTA council complete: scores=[%.2f, %.2f] total_ms=%.1f",
        signals[0].score, signals[1].score, total_ms,
    )
    return signals
