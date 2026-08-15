import urllib.request
import csv
import io
from datetime import datetime

def get_expiries():
    url = "https://api.kite.trade/instruments"
    try:
        # Request with user-agent
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            csv_content = response.read().decode('utf-8')
            
        reader = csv.reader(io.StringIO(csv_content))
        header = next(reader)
        
        # Kite instruments headers:
        # instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange
        name_idx = header.index("name")
        expiry_idx = header.index("expiry")
        segment_idx = header.index("segment")
        
        expiries = set()
        for row in reader:
            if len(row) > max(name_idx, expiry_idx, segment_idx):
                name = row[name_idx].strip().upper()
                segment = row[segment_idx].strip().upper()
                # Filter for NIFTY options
                if name == "NIFTY" and "OPT" in segment:
                    exp_date = row[expiry_idx].strip()
                    if exp_date:
                        try:
                            # Kite expiry format: "YYYY-MM-DD"
                            parsed_date = datetime.strptime(exp_date, "%Y-%m-%d")
                            # Convert to ISO format "YYYY-MM-DDT06:00:00.000Z"
                            exp_iso = parsed_date.strftime("%Y-%m-%dT06:00:00.000Z")
                            expiries.add(exp_iso)
                        except Exception:
                            pass
                            
        sorted_expiries = sorted(list(expiries))
        print("EXPIRED LIST:", sorted_expiries[:10])
    except Exception as e:
        print("Error:", str(e))

if __name__ == "__main__":
    get_expiries()
