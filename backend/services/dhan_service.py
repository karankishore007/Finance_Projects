import os
from dhanhq import dhanhq
from dotenv import load_dotenv
import logging

load_dotenv()

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DhanService")

class DhanService:
    def __init__(self):
        self.client_id = os.getenv("DHAN_CLIENT_ID")
        self.access_token = os.getenv("DHAN_ACCESS_TOKEN")
        self.client = None
        
        if self.client_id and self.access_token:
            try:
                self.client = dhanhq(self.client_id, self.access_token)
                logger.info("Dhan Client Initialized Successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Dhan Client: {e}")
        else:
            logger.warning("Dhan Credentials missing in .env. Integration will run in limited mode.")

    def is_connected(self):
        return self.client is not None

    def get_holdings(self):
        """Fetch demat holdings from Dhan."""
        if not self.client:
            return {"error": "Dhan not connected", "data": []}
        try:
            response = self.client.get_holdings()
            if response.get('status') == 'success':
                data = response.get('data', [])
                # Ensure null-safety for numeric fields
                for item in data:
                    item['pnl'] = item.get('pnl', 0.0) or 0.0
                    item['currentValue'] = item.get('currentValue', 0.0) or 0.0
                return {"status": "success", "data": data}
            return {"status": "error", "message": response.get('remarks'), "data": []}
        except Exception as e:
            return {"status": "error", "message": str(e), "data": []}

    def get_positions(self):
        """Fetch today's open positions from Dhan."""
        if not self.client:
            return {"error": "Dhan not connected", "data": []}
        try:
            response = self.client.get_positions()
            if response.get('status') == 'success':
                return {"status": "success", "data": response.get('data', [])}
            return {"status": "error", "message": response.get('remarks'), "data": []}
        except Exception as e:
            return {"status": "error", "message": str(e), "data": []}

    def place_market_order(self, ticker, quantity, transaction_type):
        """
        Place a market order. 
        Note: Requires security_id mapping.
        """
        if not self.client:
            return {"error": "Dhan not connected"}
            
        # Basic mapping for top IT stocks
        # In production, this would use a dynamic lookup or Security Master
        security_map = {
            "TCS.NS": "11536",
            "INFY.NS": "1594",
            "HCLTECH.NS": "236",
            "WIPRO.NS": "3787",
            "TECHM.NS": "13538"
        }
        
        security_id = security_map.get(ticker)
        if not security_id:
            return {"error": f"Security ID for {ticker} not found in mapper."}

        try:
            # Constants are accessed from the dhanhq class
            response = self.client.place_order(
                transaction_type=transaction_type,  
                exchange_segment=dhanhq.NSE_EQ,
                product_type=dhanhq.CNC,
                order_type=dhanhq.MARKET,
                security_id=security_id,
                quantity=quantity,
                validity="DAY"
            )
            return response
        except Exception as e:
            return {"status": "error", "message": str(e)}

# Singleton instance
dhan_service = DhanService()
