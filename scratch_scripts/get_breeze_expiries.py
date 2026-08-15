import sys
import json
import urllib.request
import zipfile
import io
from datetime import datetime

def get_breeze_nse_expiries():
    url = "https://directlink.icicidirect.com/MotherAppMaster/SecurityMaster.zip"
    local_path = "/Users/deepak/antigravity/NiftyOptions/SecurityMaster.zip"
    
    import os
    if not os.path.exists(local_path):
        print("Downloading security master...")
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                with open(local_path, "wb") as f:
                    f.write(resp.read())
        except Exception as e:
            print(f"Error downloading: {e}")
            return
            
    print("Reading SecurityMaster FONSEScripMaster...")
    expiries = set()
    try:
        with zipfile.ZipFile(local_path) as z:
            with z.open('FONSEScripMaster.txt') as f:
                content = f.read().decode('utf-8', errors='ignore')
                for line in content.split('\n'):
                    # Match ShortName "NIFTY" and InstrumentName "OPTION" precisely
                    if '"NIFTY"' in line and '"OPTION"' in line:
                        parts = [p.strip('"') for p in line.split(',')]
                        if len(parts) >= 5:
                            exp_str = parts[4] # ExpiryDate
                            try:
                                # Format: "28-Jul-2026"
                                dt = datetime.strptime(exp_str, "%d-%b-%Y")
                                exp_iso = dt.strftime("%Y-%m-%dT06:00:00.000Z")
                                expiries.add(exp_iso)
                            except Exception:
                                pass
    except Exception as e:
        print(f"Error parsing scrip master: {e}")
        return

    sorted_exp = sorted(list(expiries))
    print("\n[SUCCESS] Retrieved Option Expiries directly from Breeze NSE security master:")
    for ex in sorted_exp[:10]:
        print(f" - {ex}")

if __name__ == "__main__":
    get_breeze_nse_expiries()
