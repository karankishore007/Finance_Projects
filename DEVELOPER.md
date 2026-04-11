# AI Financial Assistant: Developer Reference

This document provides technical details on the backend API, service layer, and frontend architecture of the **Intelligent Financial Analytics & Trading Assistant**.

---

## 🏗️ 1. Project Structure
```
Finance_Projects/
├── backend/
│   ├── main.py             # FastAPI entry point & routes
│   ├── services/
│   │   ├── data_service.py  # NSE Ingestion, Technicals, AI Forecast
│   │   └── dhan_service.py  # Dhan HQ Trading & Portfolio Logic
│   └── static/             # Frontend assets
│       ├── app.js          # Main dashboard logic
│       ├── style.css       # Premium glassmorphic styles
│       └── index.html      # UI structure
├── .env                    # Secrets (DHAN credentials)
└── README.md               # Entry point
```

---

## 📡 2. Backend API Reference

### GET `/api/stocks/top`
*   **Description**: Fetches the top-performing IT sector stocks (NSE) for the default view.
*   **Response**: `Array<StockMetadata>`

### GET `/api/stocks/{ticker}`
*   **Description**: Detailed analytics for a specific ticker.
*   **Parameters**: `period` (optional, default: "1y")
*   **Response**:
    ```json
    {
        "info": { "name": "Company Name", "price": 100.0, ... },
        "historical": [ { "Date": "...", "Close": 100.0, "Volume": ... } ],
        "indicators": { "sma20": [...], "rsi": [...], "macd": {...} },
        "financials": { "trends": [...] },
        "news": [ { "title": "...", "sentiment": 0.8, "category": "..." } ],
        "forecast": { "dates": [...], "values": [...], "lower": [...], "upper": [...] },
        "advisor": { "status": "BULLISH", "insight": "Strategy explanation..." }
    }
    ```

### GET `/api/portfolio/summary`
*   **Description**: Fetches live holdings and balance from Dhan.
*   **Authentication**: Requires valid `.env` tokens.
*   **Privacy**: Should be triggered after the user clicks "Sync & View Portfolio" in the UI.

### POST `/api/trade/place`
*   **Description**: Places a market order on NSE Cash (NSE_EQ).
*   **Body**:
    ```json
    {
        "ticker": "TCS.NS",
        "quantity": 1,
        "side": "buy"
    }
    ```
*   **Safety**: Validates AI Scientist alignment before execution request.

---

## 🔧 3. Service Layer Details

### `DataService.py`
*   **Recursion Policy**: Uses `sanitize_data_recursive` to ensure all `NaN` and `Inf` values from `yfinance` are converted to JSON-safe nulls.
*   **Caching**: Implements a simple news cache in `news_cache.json` (if enabled) to reduce API overhead.
*   **Prophet Pipeline**: Historical data is sanitized (tz-aware strings removed) before being fed into the Prophet additive model.

### `DhanService.py`
*   **Constants**: Accesses `dhanhq` constants (e.g., `NSE_EQ`, `MARKET`, `CNC`) directly from the class.
*   **Security Master**: Includes a static mapper for top IT stocks to Dhan `security_id`s. This should be expanded to a dynamic lookup for full production.

---

## 🎨 4. Frontend Component Map

### `app.js`
*   **`init()`**: Entry point; loads top stocks and initializes event listeners.
*   **`renderMainChart()`**: Uses **ApexCharts** to synchronize Price, Volume, and Technical Indicator overlays.
*   **`openTradeModal(side)`**: Triggers the confirmation modal and performs the **AI Alignment Check** by comparing `side` vs `stockData.advisor.status`.
*   **`loadPortfolioData()`**: Asynchronous fetch of holdings; updates the UI dynamically upon resolution.

---

## 🛠️ 5. Troubleshooting
*   **"Repository not found"**: Ensure the remote URL in `.git/config` is exactly correct.
*   **422 Unprocessable Entity**: The `/api/trade/place` endpoint expects a JSON `OrderRequest` body, not query parameters.
*   **Dhan Attribute Error**: Ensure `NSE_EQ` and other constants are accessed via the `dhanhq` class name, not the instance.
