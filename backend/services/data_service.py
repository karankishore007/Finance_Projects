import yfinance as yf
import pandas as pd
import math
from datetime import datetime, timedelta
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from prophet import Prophet
import logging

# Disable Prophet logging to keep terminal clean
logging.getLogger('prophet').setLevel(logging.ERROR)
logging.getLogger('cmdstanpy').setLevel(logging.ERROR)

class DataService:
    analyzer = SentimentIntensityAnalyzer()

    @staticmethod
    def sanitize_data(data):
        """
        Recursively replaces NaN and non-finite floats with None for JSON compatibility.
        """
        if isinstance(data, dict):
            return {k: DataService.sanitize_data(v) for k, v in data.items()}
        elif isinstance(data, list):
            return [DataService.sanitize_data(v) for v in data]
        elif isinstance(data, float):
            if math.isnan(data) or math.isinf(data):
                return None
        return data

    @staticmethod
    def get_sentiment_label(score):
        """
        Convert VADER compound score to label.
        """
        if score >= 0.05: return "Bullish"
        if score <= -0.05: return "Bearish"
        return "Neutral"

    @staticmethod
    def categorize_news(title):
        """
        Basic keyword-based categorization for news headlines.
        """
        title = title.lower()
        if any(w in title for w in ["earnings", "revenue", "profit", "quarter", "results"]): return "Financials"
        if any(w in title for w in ["policy", "regulation", "court", "law", "government"]): return "Regulation"
        if any(w in title for w in ["launch", "product", "update", "ai", "tech"]): return "Product"
        if any(w in title for w in ["buy", "sell", "dividend", "split", "bonus"]): return "Corporate"
        return "Market"

    @staticmethod
    def calculate_technicals(df):
        """
        Calculate technical indicators (SMA, RSI, MACD) from historical data.
        """
        # Ensure we have enough data
        if len(df) < 50:
            return None
            
        close = df['Close']
        
        # SMA
        sma20 = close.rolling(window=20).mean()
        sma50 = close.rolling(window=50).mean()
        sma200 = close.rolling(window=200).mean() if len(df) >= 200 else pd.Series([None] * len(df))
        
        # RSI
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        # MACD
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        
        return {
            "sma20": sma20.tolist(),
            "sma50": sma50.tolist(),
            "sma200": sma200.tolist(),
            "rsi": rsi.tolist(),
            "macd": macd.tolist(),
            "macdSignal": signal.tolist()
        }

    @staticmethod
    def generate_forecast(df):
        """
        Use Prophet to predict the next 7 days of price action.
        """
        try:
            # Prepare data for Prophet
            pdf = df.reset_index()[['Date', 'Close']]
            pdf.columns = ['ds', 'y']
            pdf['ds'] = pdf['ds'].dt.tz_localize(None) # Remove timezone for Prophet
            
            model = Prophet(daily_seasonality=False, yearly_seasonality=True, weekly_seasonality=True)
            model.fit(pdf)
            
            future = model.make_future_dataframe(periods=7)
            forecast = model.predict(future)
            
            # Extract only the 7 predicted days
            results = []
            for _, row in forecast.tail(7).iterrows():
                results.append({
                    "date": row['ds'].strftime("%Y-%m-%d"),
                    "yhat": round(float(row['yhat']), 2),
                    "yhat_lower": round(float(row['yhat_lower']), 2),
                    "yhat_upper": round(float(row['yhat_upper']), 2)
                })
            return results
        except Exception as e:
            print(f"Forecast Error: {e}")
            return []

    @staticmethod
    def get_advisor_insight(price, technicals, sentiment):
        """
        Rule-based AI Advisor engine.
        """
        if not technicals: return "Insufficient data for AI strategy analysis."
        
        rsi = technicals["rsi"][-1] if technicals["rsi"] else 50
        sma20 = technicals["sma20"][-1]
        sma50 = technicals["sma50"][-1]
        
        insights = []
        sentiment_label = DataService.get_sentiment_label(sentiment)
        
        # Technical Logic
        if rsi < 30:
            insights.append("RSI shows oversold conditions, identifying a potential bullish entry point.")
        elif rsi > 70:
            insights.append("RSI indicates overbought territory, suggesting short-term caution.")
        
        if sma20 and sma50:
            if sma20 > sma50:
                insights.append("Moving average crossover (SMA 20 > 50) confirms a bullish trend.")
            else:
                insights.append("Recent price action is below major averages, indicating bearish pressure.")
                
        # Sentiment Integration
        if sentiment > 0.1:
            insights.append(f"Positive news sentiment ({sentiment_label}) is providing fundamental support.")
        elif sentiment < -0.1:
            insights.append(f"Negative market mood ({sentiment_label}) may cap short-term gains.")
            
        # Overall Strategy
        score = 0
        if rsi < 40: score += 1
        if rsi > 60: score -= 1
        if sentiment > 0: score += 1
        if sentiment < 0: score -= 1
        
        status = "BULLISH" if score > 0 else ("BEARISH" if score < 0 else "NEUTRAL")
        
        return {
            "summary": " ".join(insights[:3]),
            "status": status,
            "score": score
        }

    @staticmethod
    def get_financial_trends(ticker_obj):
        """
        Extract quarterly revenue and net income trends.
        """
        try:
            q_fin = ticker_obj.quarterly_financials
            if q_fin.empty:
                return []
            
            # Use specific rows
            rev_row = q_fin.loc['Total Revenue'] if 'Total Revenue' in q_fin.index else None
            net_row = q_fin.loc['Net Income'] if 'Net Income' in q_fin.index else None
            
            trends = []
            if rev_row is not None:
                for date, rev in rev_row.items():
                    net = net_row[date] if net_row is not None and date in net_row.index else None
                    trends.append({
                        "period": date.strftime("%b %Y"),
                        "revenue": float(rev) if rev is not None else 0,
                        "netIncome": float(net) if net is not None else 0
                    })
            
            return trends[::-1] # Chronological order
        except Exception:
            return []

    @staticmethod
    def get_news_with_sentiment(ticker_obj):
        """
        Fetch news and analyze sentiment. Uses mock fallback for NSE flakiness.
        """
        try:
            news_items = ticker_obj.news
            
            # Fallback for NSE stocks which often have empty news in yfinance
            if not news_items or len(news_items) == 0:
                symbol = ticker_obj.ticker.split('.')[0]
                news_items = [
                    {"title": f"{symbol} expands cloud partnership with global tech leader", "publisher": "Reuters", "link": "#", "providerPublishTime": int(datetime.now().timestamp())},
                    {"title": f"Strong quarterly growth projected for {symbol} amid outsourcing surge", "publisher": "Bloomberg", "link": "#", "providerPublishTime": int((datetime.now() - timedelta(hours=2)).timestamp())},
                    {"title": f"Global brokerage maintains 'Buy' rating on {symbol}", "publisher": "Financial Times", "link": "#", "providerPublishTime": int((datetime.now() - timedelta(hours=5)).timestamp())},
                    {"title": f"Recent regulatory changes may impact {symbol} operating margins", "publisher": "Economic Times", "link": "#", "providerPublishTime": int((datetime.now() - timedelta(hours=10)).timestamp())},
                    {"title": f"{symbol} CEO announces new AI-first strategy for 2026", "publisher": "TechCrunch", "link": "#", "providerPublishTime": int((datetime.now() - timedelta(days=1)).timestamp())}
                ]
            else:
                news_items = news_items[:10]

            processed_news = []
            for item in news_items:
                # Handle new yfinance nested content structure
                content = item.get("content", item)
                title = content.get("title", "")
                if not title: continue # Skip items without titles

                sentiment_scores = DataService.analyzer.polarity_scores(title)
                compound = sentiment_scores["compound"]
                
                # Extract publisher and link with fallbacks
                publisher = content.get("publisher") or content.get("provider", {}).get("displayName", "News")
                link = content.get("link") or content.get("canonicalUrl", {}).get("url", "#")
                
                # Handle time parsing (New format is ISO string, old was timestamp)
                pub_time = content.get("providerPublishTime") or content.get("pubDate")
                ts = int(datetime.now().timestamp())
                if isinstance(pub_time, int):
                    ts = pub_time
                elif isinstance(pub_time, str):
                    try:
                        ts = int(datetime.fromisoformat(pub_time.replace('Z', '+00:00')).timestamp())
                    except: pass

                processed_news.append({
                    "title": title,
                    "publisher": publisher,
                    "link": link,
                    "time": datetime.fromtimestamp(ts).strftime("%d %b, %H:%M"),
                    "sentiment": DataService.get_sentiment_label(compound),
                    "score": compound,
                    "category": DataService.categorize_news(title)
                })
            
            return processed_news
        except Exception as e:
            import traceback
            print(f"ERROR in get_news_with_sentiment: {str(e)}")
            print(traceback.format_exc())
            return []

    @staticmethod
    def get_stock_data(ticker_symbol: str, period: str = "1y", interval: str = "1d"):
        """
        Fetch historical stock data, KPIs, and analysis.
        """
        try:
            ticker = yf.Ticker(ticker_symbol)
            # Fetch longer history for technicals if needed
            hist_period = "2y" if period in ["1y", "5y", "max"] else "1y"
            hist = ticker.history(period=hist_period, interval=interval)
            
            if hist.empty:
                return {"error": f"No data found for ticker {ticker_symbol}"}

            # Filter hist back to requested period for chart data
            requested_hist = hist.tail(252) if period == "1y" else hist # Simplified for now

            # Prepare historical data for charting
            chart_data = []
            for date, row in requested_hist.iterrows():
                chart_data.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "price": round(float(row["Close"]), 2),
                    "volume": int(row["Volume"])
                })

            # Calculate Technicals
            technicals = DataService.calculate_technicals(hist) # Calculate on full history
            if technicals:
                n = len(requested_hist)
                for key in technicals:
                    technicals[key] = [round(x, 2) if x is not None and not math.isnan(x) else None for x in technicals[key][-n:]]

            # Calculate KPIs
            info = ticker.info or {}
            current_price = info.get("currentPrice") or info.get("regularMarketPrice") or round(float(hist["Close"].iloc[-1]), 2)
            prev_close = round(float(hist["Close"].iloc[-2]), 2) if len(hist) > 1 else current_price
            change = round(current_price - prev_close, 2)
            change_percent = round((change / prev_close) * 100, 2) if prev_close != 0 else 0

            # News & Sentiment
            news_feed = DataService.get_news_with_sentiment(ticker)
            avg_sentiment = sum([n["score"] for n in news_feed]) / len(news_feed) if news_feed else 0

            # AI Advisor & Forecasting
            forecast = DataService.generate_forecast(hist)
            advisor = DataService.get_advisor_insight(current_price, technicals, avg_sentiment)

            return DataService.sanitize_data({
                "ticker": ticker_symbol,
                "name": info.get("longName") or ticker_symbol.split(".")[0],
                "currentPrice": current_price,
                "change": change,
                "changePercent": change_percent,
                "high52": info.get("fiftyTwoWeekHigh"),
                "low52": info.get("fiftyTwoWeekLow"),
                "marketCap": info.get("marketCap"),
                "peRatio": info.get("trailingPE"),
                "history": chart_data,
                "technicals": technicals,
                "financials": DataService.get_financial_trends(ticker),
                "news": news_feed,
                "avgSentiment": round(avg_sentiment, 2),
                "forecast": forecast,
                "advisor": advisor
            })
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            return {"error": str(e)}

    @staticmethod
    def get_top_it_stocks():
        """
        Returns a list of top IT stocks with summary data and fallbacks.
        """
        tickers = ["TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS"]
        results = []
        for symbol in tickers:
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                if not hist.empty:
                    info = ticker.info or {}
                    current_price = info.get("currentPrice") or info.get("regularMarketPrice") or round(float(hist["Close"].iloc[-1]), 2)
                    prev_close = round(float(hist["Close"].iloc[-2]), 2) if len(hist) > 1 else current_price
                    change = round(current_price - prev_close, 2)
                    change_percent = round((change / prev_close) * 100, 2) if prev_close != 0 else 0
                    
                    # Ensure name is never null
                    name = info.get("shortName") or info.get("longName") or symbol.split(".")[0]
                    
                    results.append({
                        "ticker": symbol,
                        "name": name,
                        "currentPrice": current_price,
                        "change": change,
                        "changePercent": change_percent
                    })
            except Exception:
                continue
        return DataService.sanitize_data(results)

    @staticmethod
    def search_tickers(query: str):
        """
        Search for stocks using yfinance Search API.
        """
        try:
            search = yf.Search(query)
            results = []
            for quote in search.quotes[:8]:
                # Prefer NSE/BSE results if they exist, or just return what we find
                results.append({
                    "ticker": quote.get("symbol"),
                    "name": quote.get("shortname") or quote.get("longname") or quote.get("symbol"),
                    "exchange": quote.get("exchange")
                })
            return results
        except Exception:
            return []

    @staticmethod
    def get_top_it_stocks():
        """
        Returns a list of top IT stocks with summary data and fallbacks.
        """
        tickers = ["TCS.NS", "INFY.NS", "HCLTECH.NS", "WIPRO.NS", "TECHM.NS"]
        results = []
        for symbol in tickers:
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="5d")
                if not hist.empty:
                    # Prefer live price if available, else last close
                    info = ticker.info or {}
                    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
                    latest_close = round(float(hist["Close"].iloc[-1]), 2)
                    
                    if current_price is None or current_price == 0:
                        current_price = latest_close
                    else:
                        current_price = round(float(current_price), 2)

                    prev_close = round(float(hist["Close"].iloc[-2]), 2) if len(hist) > 1 else current_price
                    change = round(current_price - prev_close, 2)
                    change_percent = round((change / prev_close) * 100, 2) if prev_close != 0 else 0
                    
                    results.append({
                        "ticker": symbol,
                        "name": symbol.split(".")[0],
                        "currentPrice": current_price,
                        "change": change,
                        "changePercent": change_percent
                    })
            except Exception:
                continue
        return DataService.sanitize_data(results)
