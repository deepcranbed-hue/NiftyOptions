import urllib.request
import zipfile
import io
import os

def check():
    url = "https://directlink.icicidirect.com/MotherAppMaster/SecurityMaster.zip"
    print(f"Downloading security master from {url}...")
    
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        zip_data = response.read()
        
    print("Extracting ZIP contents...")
    z = zipfile.ZipFile(io.BytesIO(zip_data))
    
    target_symbols = [
        "INFY", "BHARTIARTL", "M&M", "TITAN", "BAJAJFINSV", "POWERGRID", "INDIGO",
        "TECHM", "SBILIFE", "HDFCLIFE", "BRITANNIA", "BPCL", "BEL", "SHRIRAMFIN"
    ]
    
    # Let's search inside NSE.txt if it exists
    for name in z.namelist():
        print(f"Scanning file: {name}")
        if "NSE" in name:
            with z.open(name) as f:
                content = f.read().decode('utf-8', errors='ignore')
                for line in content.split('\n'):
                    parts = line.split(',')
                    if len(parts) > 1:
                        nse_token = parts[0]
                        breeze_code = parts[1] # usually second or third field
                        short_name = parts[2] if len(parts) > 2 else ""
                        description = parts[3] if len(parts) > 3 else ""
                        
                        # Let's print matching rows
                        for sym in target_symbols:
                            if f",{sym}," in line or f",{sym.upper()}," in line or short_name.upper() == sym or description.upper().startswith(sym):
                                print(f"MATCH FOR {sym}: {line.strip()}")

if __name__ == "__main__":
    check()
