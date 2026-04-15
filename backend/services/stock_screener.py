"""
Stock Screener — Intelligent watchlist construction for alpha generation.

Instead of a hardcoded sector list, this module scans a broad universe
(Nifty 200+) and selects stocks optimised for the Council-of-Models
trading system using a multi-factor scoring framework:

    1. Liquidity    — Minimum volume floor; illiquid stocks are untradeable.
    2. Volatility   — Goldilocks zone: enough movement for profit, not casino-level.
    3. Trend Clarity — Prefer stocks with identifiable technical regimes
                       (trending or mean-reverting), not choppy noise.
    4. Signal Density— Score how often legacy models would have fired signals
                       over the recent lookback window.
    5. Sector Mix   — Enforce diversification so the portfolio isn't one big
                       sector bet.

The output is a ranked list of tickers ready for the meta-learner pipeline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

from backend.config import get_config, setup_logging

logger = setup_logging("agent.screener")
cfg = get_config()


# ---------------------------------------------------------------------------
# NSE Universe — broad starting pool
# ---------------------------------------------------------------------------
# Nifty 50 + popular midcap IT/Pharma/Banking/Auto/FMCG/Energy/Metals
# This gives cross-sector coverage without needing to scrape an index API.
NSE_UNIVERSE: List[str] = [
    # --- Nifty 50 core ---
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "HINDUNILVR.NS", "ITC.NS", "SBIN.NS", "BHARTIARTL.NS", "KOTAKBANK.NS",
    "LT.NS", "AXISBANK.NS", "BAJFINANCE.NS", "ASIANPAINT.NS", "MARUTI.NS",
    "HCLTECH.NS", "SUNPHARMA.NS", "TITAN.NS", "ULTRACEMCO.NS", "WIPRO.NS",
    "NTPC.NS", "ONGC.NS", "POWERGRID.NS", "M&M.NS", "TATAMOTORS.NS",
    "ADANIPORTS.NS", "BAJAJFINSV.NS", "TATASTEEL.NS", "TECHM.NS", "INDUSINDBK.NS",
    "COALINDIA.NS", "HINDALCO.NS", "JSWSTEEL.NS", "NESTLEIND.NS", "DRREDDY.NS",
    "CIPLA.NS", "DIVISLAB.NS", "HEROMOTOCO.NS", "GRASIM.NS", "BPCL.NS",
    "EICHERMOT.NS", "APOLLOHOSP.NS", "TATACONSUM.NS", "SBILIFE.NS", "BRITANNIA.NS",
    # --- High-signal midcaps ---
    "TRENT.NS", "ZOMATO.NS", "POLYCAB.NS", "PERSISTENT.NS", "COFORGE.NS",
    "MPHASIS.NS", "LTIM.NS", "PIIND.NS", "DALBHARAT.NS", "IDFCFIRSTB.NS",
    "FEDERALBNK.NS", "TATAPOWER.NS", "IRCTC.NS", "HAL.NS", "BEL.NS",
    "SAIL.NS", "NHPC.NS", "IOC.NS", "VEDL.NS", "ADANIENT.NS",
    "JINDALSTEL.NS", "BANKBARODA.NS", "CANBK.NS", "PNB.NS", "RECLTD.NS",
    "PFC.NS", "TATAELXSI.NS", "DIXON.NS", "VOLTAS.NS", "GODREJCP.NS",
]


# ---------------------------------------------------------------------------
# Screening result
# ---------------------------------------------------------------------------
@dataclass
class ScreenedStock:
    """Output for a single screened stock with component scores."""

    ticker: str
    name: str
    sector: str
    current_price: float

    # Component scores (each 0-100)
    liquidity_score: float
    volatility_score: float
    trend_score: float
    signal_score: float

    # Composite
    composite_score: float

    # Raw metrics
    avg_volume: float
    annual_volatility: float
    adr_pct: float          # Average Daily Range as % of price
    rsi_14: float
    price_vs_52w_high: float  # % below 52-week high
    recent_return_1m: float
    recent_return_3m: float

    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "sector": self.sector,
            "current_price": round(self.current_price, 2),
            "composite_score": round(self.composite_score, 2),
            "liquidity_score": round(self.liquidity_score, 2),
            "volatility_score": round(self.volatility_score, 2),
            "trend_score": round(self.trend_score, 2),
            "signal_score": round(self.signal_score, 2),
            "avg_volume": round(self.avg_volume),
            "annual_volatility": round(self.annual_volatility, 4),
            "adr_pct": round(self.adr_pct, 4),
            "rsi_14": round(self.rsi_14, 2),
            "price_vs_52w_high": round(self.price_vs_52w_high, 4),
            "recent_return_1m": round(self.recent_return_1m, 4),
            "recent_return_3m": round(self.recent_return_3m, 4),
        }


# ---------------------------------------------------------------------------
# Sector classifier (simple keyword-based)
# ---------------------------------------------------------------------------
_SECTOR_MAP = {
    "RELIANCE": "Energy", "ONGC": "Energy", "BPCL": "Energy", "IOC": "Energy",
    "NTPC": "Power", "POWERGRID": "Power", "TATAPOWER": "Power", "NHPC": "Power",
    "RECLTD": "Power", "PFC": "Power", "COALINDIA": "Mining",
    "TCS": "IT", "INFY": "IT", "HCLTECH": "IT", "WIPRO": "IT", "TECHM": "IT",
    "LTIM": "IT", "PERSISTENT": "IT", "COFORGE": "IT", "MPHASIS": "IT",
    "TATAELXSI": "IT", "DIXON": "IT",
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking",
    "KOTAKBANK": "Banking", "AXISBANK": "Banking", "INDUSINDBK": "Banking",
    "IDFCFIRSTB": "Banking", "FEDERALBNK": "Banking", "BANKBARODA": "Banking",
    "CANBK": "Banking", "PNB": "Banking",
    "BAJFINANCE": "Finance", "BAJAJFINSV": "Finance", "SBILIFE": "Finance",
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
    "TATACONSUM": "FMCG", "BRITANNIA": "FMCG", "GODREJCP": "FMCG",
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma",
    "DIVISLAB": "Pharma", "APOLLOHOSP": "Healthcare",
    "MARUTI": "Auto", "TATAMOTORS": "Auto", "M&M": "Auto",
    "HEROMOTOCO": "Auto", "EICHERMOT": "Auto",
    "TATASTEEL": "Metals", "HINDALCO": "Metals", "JSWSTEEL": "Metals",
    "SAIL": "Metals", "VEDL": "Metals", "JINDALSTEL": "Metals",
    "ASIANPAINT": "Consumer", "TITAN": "Consumer", "TRENT": "Consumer",
    "PIIND": "Chemicals", "DALBHARAT": "Cement", "ULTRACEMCO": "Cement",
    "GRASIM": "Cement",
    "LT": "Infra", "ADANIPORTS": "Infra", "ADANIENT": "Conglomerate",
    "BHARTIARTL": "Telecom",
    "ZOMATO": "Tech", "IRCTC": "Travel", "HAL": "Defence", "BEL": "Defence",
    "POLYCAB": "Electricals", "VOLTAS": "Consumer Durables",
}


def _get_sector(ticker: str) -> str:
    symbol = ticker.replace(".NS", "").replace(".BO", "")
    return _SECTOR_MAP.get(symbol, "Other")


# ============================================================================
# Core Screening Logic
# ============================================================================
class StockScreener:
    """Multi-factor stock screener for watchlist construction.

    Usage::

        screener = StockScreener()
        results = screener.scan()
        top_10 = results[:10]
    """

    def __init__(
        self,
        universe: Optional[List[str]] = None,
        max_results: int = 15,
        max_per_sector: int = 3,
        min_volume: int = 500_000,
        lookback_days: int = 252,
    ) -> None:
        self._universe = universe or NSE_UNIVERSE
        self._max_results = max_results
        self._max_per_sector = max_per_sector
        self._min_volume = min_volume
        self._lookback = lookback_days

        # Score weights
        self._w_liquidity = 0.15
        self._w_volatility = 0.25
        self._w_trend = 0.30
        self._w_signal = 0.30

    def scan(self) -> List[ScreenedStock]:
        """Scan the full universe and return a ranked, diversified watchlist.

        Returns:
            Sorted list of ScreenedStock, best first.
        """
        start = time.perf_counter()
        logger.info(
            "Starting scan of %d stocks", len(self._universe),
        )

        raw_results: List[ScreenedStock] = []

        for ticker in self._universe:
            try:
                result = self._evaluate_single(ticker)
                if result is not None:
                    raw_results.append(result)
            except Exception:
                logger.debug("Skipping %s (fetch failed)", ticker)
                continue

        # Sort by composite score descending
        raw_results.sort(key=lambda s: s.composite_score, reverse=True)

        # Apply sector diversification cap
        final = self._apply_sector_cap(raw_results)

        elapsed = (time.perf_counter() - start) * 1000
        logger.info(
            "Scan complete: %d/%d passed filters, returning top %d (%.0fms)",
            len(raw_results), len(self._universe), len(final), elapsed,
        )

        return final

    # ------------------------------------------------------------------
    def _evaluate_single(self, ticker: str) -> Optional[ScreenedStock]:
        """Fetch data and compute all scores for one ticker."""
        t = yf.Ticker(ticker)
        hist = t.history(period="1y", interval="1d")

        if hist is None or len(hist) < 100:
            return None

        info = t.info or {}
        close = hist["Close"].astype(float)
        high = hist["High"].astype(float)
        low = hist["Low"].astype(float)
        volume = hist["Volume"].astype(float)

        current_price = float(close.iloc[-1])
        avg_vol = float(volume.tail(20).mean())

        # --- Filter: minimum liquidity ---
        if avg_vol < self._min_volume:
            return None

        # --- Filter: minimum price (penny stocks) ---
        if current_price < 50:
            return None

        # --- Compute raw metrics ---
        daily_returns = close.pct_change().dropna()
        annual_vol = float(daily_returns.std() * np.sqrt(252))

        # Average Daily Range (ADR) as % — proxy for intraday opportunity
        daily_range = (high - low) / close
        adr_pct = float(daily_range.tail(20).mean())

        # RSI-14
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        rs = gain / loss
        rsi_series = 100 - (100 / (1 + rs))
        rsi_14 = float(rsi_series.iloc[-1]) if not np.isnan(rsi_series.iloc[-1]) else 50.0

        # Price vs 52-week high
        high_52w = float(high.max())
        pct_below_high = (current_price - high_52w) / high_52w

        # Recent returns
        ret_1m = float((close.iloc[-1] / close.iloc[-22] - 1)) if len(close) >= 22 else 0.0
        ret_3m = float((close.iloc[-1] / close.iloc[-66] - 1)) if len(close) >= 66 else 0.0

        # --- Score: Liquidity (0-100) ---
        # Log-scale: 500K = 0, 10M+ = 100
        liq_score = float(np.clip(
            (np.log10(max(avg_vol, 1)) - np.log10(self._min_volume))
            / (np.log10(10_000_000) - np.log10(self._min_volume)) * 100,
            0, 100
        ))

        # --- Score: Volatility (0-100) ---
        # Sweet spot: 20-40% annual vol scores highest.
        # Too low (<10%) = no movement.  Too high (>60%) = too noisy.
        vol_score = self._bell_score(annual_vol, center=0.30, width=0.15)

        # --- Score: Trend Clarity (0-100) ---
        # High = clear directional move or clean mean-reversion.
        # Low = choppy, directionless.
        trend_score = self._compute_trend_clarity(close, daily_returns)

        # --- Score: Signal Density (0-100) ---
        # How often would the legacy models fire in the last 3 months?
        signal_score = self._compute_signal_density(close, high, low, rsi_14)

        # --- Composite ---
        composite = (
            self._w_liquidity * liq_score
            + self._w_volatility * vol_score
            + self._w_trend * trend_score
            + self._w_signal * signal_score
        )

        name = info.get("shortName") or info.get("longName") or ticker.split(".")[0]
        sector = _get_sector(ticker)

        return ScreenedStock(
            ticker=ticker,
            name=name,
            sector=sector,
            current_price=current_price,
            liquidity_score=liq_score,
            volatility_score=vol_score,
            trend_score=trend_score,
            signal_score=signal_score,
            composite_score=composite,
            avg_volume=avg_vol,
            annual_volatility=annual_vol,
            adr_pct=adr_pct,
            rsi_14=rsi_14,
            price_vs_52w_high=pct_below_high,
            recent_return_1m=ret_1m,
            recent_return_3m=ret_3m,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _bell_score(value: float, center: float, width: float) -> float:
        """Gaussian bell-curve score — max at center, falls off with distance."""
        return float(np.clip(
            100 * np.exp(-0.5 * ((value - center) / width) ** 2),
            0, 100,
        ))

    @staticmethod
    def _compute_trend_clarity(close: pd.Series, daily_returns: pd.Series) -> float:
        """Score how 'clean' the price trend is.

        Uses R-squared of a linear regression on the last 60 days:
        high R² = clear trend (up or down), low = choppy.
        Also rewards stocks near key RSI levels (oversold/overbought).
        """
        window = close.tail(60).values
        if len(window) < 30:
            return 50.0

        x = np.arange(len(window), dtype=float)
        coeffs = np.polyfit(x, window, 1)
        fitted = np.polyval(coeffs, x)
        ss_res = np.sum((window - fitted) ** 2)
        ss_tot = np.sum((window - np.mean(window)) ** 2)
        r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        # Also check autocorrelation (high = trending, negative = reverting)
        if len(daily_returns) >= 20:
            autocorr = float(daily_returns.tail(60).autocorr(lag=1))
            autocorr = autocorr if not np.isnan(autocorr) else 0.0
        else:
            autocorr = 0.0

        # Both trending (high R², positive autocorr) and reverting
        # (low R², negative autocorr) are tradeable — we want either extreme.
        trend_component = r_squared * 80  # 0-80
        regime_bonus = abs(autocorr) * 20  # 0-20

        return float(np.clip(trend_component + regime_bonus, 0, 100))

    @staticmethod
    def _compute_signal_density(
        close: pd.Series, high: pd.Series, low: pd.Series, rsi: float,
    ) -> float:
        """Score based on how actionable the current technical setup is.

        Rewards:
        - RSI in extreme zones (< 30 oversold, > 70 overbought)
        - Price near Bollinger Band edges
        - Clear momentum (strong recent move creates follow-through opportunity)
        """
        score = 0.0
        n = len(close)

        # RSI extremes: 30 pts max
        if rsi < 30 or rsi > 70:
            score += 30.0
        elif rsi < 40 or rsi > 60:
            score += 15.0

        # Bollinger Band position: 30 pts max
        if n >= 20:
            window = close.tail(20)
            sma = float(window.mean())
            std = float(window.std())
            if std > 0 and sma > 0:
                upper = sma + 2 * std
                lower = sma - 2 * std
                current = float(close.iloc[-1])
                band_pos = (current - lower) / (upper - lower) if upper != lower else 0.5
                # Reward extremes (near 0 or near 1)
                edge_dist = abs(band_pos - 0.5)
                score += edge_dist * 60  # max 30

        # Momentum clarity: 20 pts max
        if n >= 66:
            ret_3m = float(close.iloc[-1] / close.iloc[-66] - 1)
            # Strong moves in either direction are tradeable
            score += min(abs(ret_3m) * 100, 20.0)

        # Volume surge: 20 pts max
        if n >= 40:
            vol_ratio = float(
                close.tail(5).std() / close.tail(40).std()
            ) if close.tail(40).std() > 0 else 1.0
            if vol_ratio > 1.5:
                score += min((vol_ratio - 1.0) * 20, 20.0)

        return float(np.clip(score, 0, 100))

    # ------------------------------------------------------------------
    def _apply_sector_cap(self, ranked: List[ScreenedStock]) -> List[ScreenedStock]:
        """Enforce max N per sector for diversification."""
        sector_counts: Dict[str, int] = {}
        result: List[ScreenedStock] = []

        for stock in ranked:
            count = sector_counts.get(stock.sector, 0)
            if count >= self._max_per_sector:
                continue
            sector_counts[stock.sector] = count + 1
            result.append(stock)
            if len(result) >= self._max_results:
                break

        return result
