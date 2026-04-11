# AI Financial Assistant: System Documentation & User Guide

This document provides a technical and functional deep-dive into the development, algorithms, and interpretation of the **Intelligent Financial Analytics & Trading Assistant**.

---

## 🏗️ 1. Development Process

The system was developed using an incremental, **Persona-Based Architecture**, evolving through four distinct phases:

1.  **Phase 1: The Observer** (Foundation)
    *   Setup of the FastAPI backend and Vanilla JS frontend.
    *   Implementation of real-time data ingestion for NSE (National Stock Exchange) stocks via `yfinance`.
2.  **Phase 2: The Analyst** (Mathematical Layer)
    *   Transitioned from raw data to technical analysis.
    *   Implemented server-side calculation of moving averages and momentum oscillators.
3.  **Phase 3: The Strategist** (Contextual Layer)
    *   Integrated news feeds and sentiment analysis to provide "Why" behind price movements.
4.  **Phase 4: The Advisor** (Predictive Layer)
    *   Introduced time-series forecasting and the AI Strategy Engine to provide actionable advice.
5.  **Phase 5: The Executive** (Capital Management Layer)
    *   Integrated Dhan HQ for live trading and portfolio tracking.
    *   Implemented privacy-first data handling and AI-assisted order confirmation.

---

## 🧠 2. Analytics, Logic & Algorithms

The system employs four core analytical layers to generate insights:

### A. Technical Indicator Logic (The Analyst)
Standard mathematical formulas are applied to historical price data (1-year daily close):
*   **SMA (Simple Moving Average)**: Calculates the average price over the last 20, 50, and 200 days. Used to identify trend direction and support/resistance.
*   **RSI (Relative Strength Index)**: A momentum oscillator that measures the speed and change of price movements. 
    *   *Algorithm*: `100 - (100 / (1 + AvgGain / AvgLoss))` over a 14-day window.
*   **MACD (Moving Average Convergence Divergence)**: A trend-following momentum indicator.
    *   *Algorithm*: Difference between 12-day and 26-day EMAs, compared against a 9-day Signal line.

### B. Sentiment Analysis (The Strategist)
*   **Algorithm**: **VADER (Valence Aware Dictionary and sEntiment Reasoner)**.
*   **Method**: Lexicon and rule-based analysis specifically attuned to social media and news sentiment. 
*   **Scoring**: Each headline is assigned a Compound Score (-1.0 to 1.0).
    *   `> 0.05`: Bullish (Positive)
    *   `< -0.05`: Bearish (Negative)
    *   `Between`: Neutral

### C. Predictive Forecasting (The Advisor)
*   **Algorithm**: **Facebook Prophet**.
*   **Method**: An additive model where non-linear trends are fit with yearly, weekly, and daily seasonality.
*   **Processing**: 
    1.  Historical data is cleaned and timezones removed.
    2.  The model identifies seasonal patterns (e.g., end-of-quarter surges).
    3.  A 7-day projection is generated with upper and lower confidence intervals.

### D. Trade Alignment Analysis (The Executive)
*   **Algorithm**: Contextual Validation Loop.
*   **Method**: When a user attempts to place an order, the system compares the **Order Side** (Buy/Sell) with the **AI Strategist Bias**.
*   **Logic**:
    *   *Match*: If `Side == 'Buy'` and `Bias == 'Bullish'`, the UI confirms alignment (✨).
    *   *Conflict*: If `Side == 'Buy'` and `Bias == 'Bearish'`, a warning (⚠️) is displayed urging the user to review the technicals.

---

## ⚙️ 3. Current Running Processes

The system operates as a self-contained full-stack application:

1.  **FastAPI Backend (Uvicorn)**:
    *   Listening on `http://localhost:8000`.
    *   Handles asynchronous API requests for stock data, search, and health checks.
2.  **Data Ingestion Engine**:
    *   Communicates with Yahoo Finance APIs in real-time.
    *   Performs recursive sanitization (removing `NaN`/`Infinity` values) before sending data to the UI.
3.  **AI Strategy Engine**:
    *   A background logic layer that runs whenever a new stock is loaded, calculating the "AI Strategist" insight by cross-referencing RSI, SMA, and Sentiment scores.
4.  **Dhan Trading Service**:
    *   Connects to Dhan HQ for order placement and holdings retrieval.
    *   Enforces "Privacy-First" mode, only retrieving delicate financial data upon user "Unlock."

---

## 📈 4. How to Interpret the Dashboard

To use this dashboard like a professional analyst, follow these interpretation guidelines:

### The AI Strategist Card (Top Header)
*   **BULLISH**: Multiple indicators (e.g., RSI < 30 and positive news) suggests an upward trend is likely.
*   **BEARISH**: Indicates overbought conditions or negative sentiment; caution is advised.
*   **Insight Text**: Read this for the "Strategy Summary." It explains exactly why the AI reached its conclusion (e.g., a "Golden Cross" or "Oversold RSI").

### Portfolio Privacy & Syncing
*   **Observer View**: By default, the system hides your live balance and holdings to ensure privacy in public spaces.
*   **The Unlock**: Click **"Sync & View Portfolio"** to reveal your real-world capital, P&L, and current holdings. This action establishes a secure live link with your Dhan account.

### The Trade Modal (Confirm Order)
*   **Status Alerts**: If an order fails (e.g., market closed on a weekend), a red alert box appears inside the modal with the exact failure reason from the exchange.
*   **AI Check**: Always check the "AI Advisor Note" before clicking confirm. It provides a final logic-check against the technical "Analytical Layer" (Phases 1-4).

### The Price Chart & Overlays
*   **Price Line**: Real-time historical trajectory.
*   **SMA 20 (Green)**: Short-term trend. If the price is above this, momentum is strong.
*   **SMA 50 (Yellow)**: Medium-term trend. Used to identify the "Primary Trend."

### AI 7-Day Forecast (Dashed Line)
*   **The Projection**: This dashed line indicates the statistical "Most Likely" direction for the next 7 days.
*   **How to use**: Use this to anticipate major moves before they happen, but always cross-reference with the **Market Mood** indicator.

### News & Sentiment (Right Panel)
*   **Sentiment Badges**: High-impact bullish news (Green) can often override negative technical signals.
*   **Categorization**: "Financials" and "Regulation" news typically have the highest impact on long-term price action.

---
> [!IMPORTANT]
> **Data Disclaimer**: While the system uses sophisticated algorithms like Prophet and VADER, stock market predictions are probabilistic. This tool should be used for research and analytics assistance, not as a guaranteed financial advisor.
