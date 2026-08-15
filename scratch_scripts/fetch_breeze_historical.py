import urllib.request
import os

_original_urlopen = urllib.request.urlopen
def _patched_urlopen(url, *args, **kwargs):
    if isinstance(url, str) and "SecurityMaster.zip" in url:
        local_path = "/Users/deepak/antigravity/NiftyOptions/SecurityMaster.zip"
        if not os.path.exists(local_path):
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with _original_urlopen(req, timeout=30) as resp:
                with open(local_path, "wb") as f:
                    f.write(resp.read())
        return open(local_path, "rb")
    return _original_urlopen(url, *args, **kwargs)
urllib.request.urlopen = _patched_urlopen

from breeze_connect import BreezeConnect
import sys
import json

def get_nifty_futures_expiries():
    import urllib.request, zipfile, io
    from datetime import datetime
    try:
        url = "https://directlink.icicidirect.com/MotherAppMaster/SecurityMaster.zip"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            z = zipfile.ZipFile(io.BytesIO(resp.read()))
            with z.open('FONSEScripMaster.txt') as f:
                content = f.read().decode('utf-8', errors='ignore')
                exp1, exp2 = None, None
                for line in content.split('\n'):
                    if '"NIFTY"' in line and '"FUTURE"' in line:
                        parts = [p.strip('"') for p in line.split(',')]
                        if len(parts) >= 40:
                            tenor = parts[39]
                            exp_str = parts[4]
                            dt = datetime.strptime(exp_str, "%d-%b-%Y")
                            iso_str = dt.strftime("%Y-%m-%dT06:00:00.000Z")
                            if tenor == "1-Mon":
                                exp1 = iso_str
                            elif tenor == "2-Mon":
                                exp2 = iso_str
                if exp1 and exp2:
                    return exp1, exp2
    except Exception as err:
        print(f"Warning: Failed to fetch security master ({err}). Using fallback expiry calculation.")
        
    from datetime import date, timedelta
    import calendar
    ref_date = date.today()
    def last_thursday(year, month):
        last_day = calendar.monthrange(year, month)[1]
        dt = date(year, month, last_day)
        while dt.weekday() != 3:
            dt -= timedelta(days=1)
        return dt
    curr_exp = last_thursday(ref_date.year, ref_date.month)
    if ref_date > curr_exp:
        m1_year, m1_month = ref_date.year, ref_date.month + 1
        if m1_month > 12:
            m1_month -= 12
            m1_year += 1
        m2_year, m2_month = ref_date.year, ref_date.month + 2
        if m2_month > 12:
            m2_month -= 12
            m2_year += 1
        exp1 = last_thursday(m1_year, m1_month)
        exp2 = last_thursday(m2_year, m2_month)
    else:
        m2_year, m2_month = ref_date.year, ref_date.month + 1
        if m2_month > 12:
            m2_month -= 12
            m2_year += 1
        exp1 = curr_exp
        exp2 = last_thursday(m2_year, m2_month)
    return f"{exp1}T06:00:00.000Z", f"{exp2}T06:00:00.000Z"

def fetch():
    if len(sys.argv) < 5:
        print(json.dumps({"error": "Missing arguments"}))
        sys.exit(1)
        
    session_token = sys.argv[1]
    interval = sys.argv[2]      # "1minute" or "1day"
    from_date = sys.argv[3]     # "YYYY-MM-DDTHH:MM:SS.000Z"
    to_date = sys.argv[4]       # "YYYY-MM-DDTHH:MM:SS.000Z"
    symbol = sys.argv[5] if len(sys.argv) > 5 else "NIFTY"
    expiry_date = sys.argv[6] if len(sys.argv) > 6 else ""
    
    api_key = "999407AZb39Vu3D&9X405B977330807K"
    api_secret = "584F70+Z075364Cz35y6O9931Y16I387"
    
    try:
        breeze = BreezeConnect(api_key=api_key)
        breeze.generate_session(api_secret=api_secret, session_token=session_token)
        
        exchange_code = "MCX" if symbol.upper() in ("GOLD", "SILVER", "COPPER", "CRUDEOIL") else "NSE"
        if symbol.upper() == "USDINR":
            exchange_code = "NDX"
        elif symbol.upper() in ("NIFTY_FUT_1", "NIFTY_FUT_2"):
            exchange_code = "NFO"
            
        product_type = "futures" if exchange_code in ("MCX", "NDX", "NFO") else "cash"
        breeze_stock_code = "NIFTY" if symbol.upper() in ("NIFTY_FUT_1", "NIFTY_FUT_2") else symbol
        
        # If expiry date is required but not provided, apply smart defaults
        if product_type == "futures" and not expiry_date:
            sym_upper = symbol.upper()
            if sym_upper == "USDINR":
                expiry_date = "2026-07-28T06:00:00.000Z"
            elif sym_upper == "CRUDEOIL":
                expiry_date = "2026-07-17T06:00:00.000Z"
            elif sym_upper in ("NIFTY_FUT_1", "NIFTY_FUT_2"):
                exp1, exp2 = get_nifty_futures_expiries()
                expiry_date = exp1 if sym_upper == "NIFTY_FUT_1" else exp2
            else:
                expiry_date = "2026-07-31T06:00:00.000Z" # GOLD, SILVER, COPPER
        
        if interval == "1day":
            res = breeze.get_historical_data(
                interval=interval,
                from_date=from_date,
                to_date=to_date,
                stock_code=breeze_stock_code,
                exchange_code=exchange_code,
                product_type=product_type,
                expiry_date=expiry_date if product_type == "futures" else None
            )
            # Normalize version 1 string values to floats/ints for consistency
            if res and res.get("Success"):
                for row in res["Success"]:
                    for field in ["open", "high", "low", "close"]:
                        if field in row and row[field] is not None:
                            try:
                                row[field] = float(row[field])
                            except ValueError:
                                pass
                    for field in ["volume", "open_interest"]:
                        if field in row and row[field] is not None:
                            try:
                                row[field] = int(row[field])
                            except ValueError:
                                pass
        else:
            res = breeze.get_historical_data_v2(
                interval=interval,
                from_date=from_date,
                to_date=to_date,
                stock_code=breeze_stock_code,
                exchange_code=exchange_code,
                product_type=product_type,
                expiry_date=expiry_date if product_type == "futures" else None
            )
        print(json.dumps(res))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    fetch()
