from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from backend.services.data_service import DataService
import uvicorn
import os

app = FastAPI(title="Intelligent Financial Analytics API")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Endpoints
@app.get("/api/stocks/top")
async def get_top_stocks():
    return DataService.get_top_it_stocks()

@app.get("/api/stocks/{ticker}")
async def get_stock_details(ticker: str, period: str = "1y"):
    data = DataService.get_stock_data(ticker, period=period)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    return data

@app.get("/api/stocks/search")
async def search_stocks(q: str):
    """
    Search for stocks and return a list of tickers/names.
    """
    if not q:
        return []
    return DataService.search_tickers(q)

# Serve Static Files
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)

app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
